import { defineStore } from 'pinia'
import { request } from '../api/client'
import type {
  AgentContextRef,
  AgentImageAttachment,
  AgentMessage,
  AgentSessionDetail,
  AgentSessionSummary,
  AgentUploadResult,
} from '../types/agent'

function normalizeUploadedImage(raw: Record<string, unknown>): AgentImageAttachment {
  return {
    url: String(raw.url || ''),
    thumbnailUrl: typeof raw.thumbnail_url === 'string' ? raw.thumbnail_url : undefined,
    filename: typeof raw.filename === 'string' ? raw.filename : '',
    mime: typeof raw.mime === 'string' ? raw.mime : '',
    size: typeof raw.size === 'number' ? raw.size : 0,
  }
}

function isOptimisticMessage(message: AgentMessage): boolean {
  return message.id < 0
}

function imageSignature(message: AgentMessage): string {
  const images = message.attachments?.images
  if (!Array.isArray(images)) return ''
  return images.map((image) => image.url).join('|')
}

function hasMatchingPersistedMessage(messages: AgentMessage[], localMessage: AgentMessage): boolean {
  return messages.some((message) => (
    message.id > 0
    && message.role === localMessage.role
    && message.content === localMessage.content
    && imageSignature(message) === imageSignature(localMessage)
  ))
}

function latestPersistedMessageId(messages: AgentMessage[]): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const id = messages[index]?.id ?? 0
    if (id > 0) return id
  }
  return 0
}

function hasUnreadAssistantMessage(messages: AgentMessage[], lastSeenMessageId: number): boolean {
  return messages.some((message) => (
    message.id > lastSeenMessageId && message.role !== 'user'
  ))
}

export const useAgentAssistantStore = defineStore('agentAssistant', {
  state: () => ({
    currentUserId: 0,
    session: null as AgentSessionSummary | null,
    messages: [] as AgentSessionDetail['messages'],
    open: false,
    loading: false,
    sending: false,
    uploading: false,
    resetting: false,
    error: '',
    lastSeenMessageId: 0,
    sessionInitialized: false,
  }),
  getters: {
    hasUnread(state) {
      if (state.open) return false
      return hasUnreadAssistantMessage(state.messages, state.lastSeenMessageId)
    },
    unreadCount(state) {
      if (state.open) return 0
      return hasUnreadAssistantMessage(state.messages, state.lastSeenMessageId) ? 1 : 0
    },
  },
  actions: {
    async syncInitialUser(userId: number | null | undefined) {
      if (!userId) {
        this.currentUserId = 0
        this.session = null
        this.messages = []
        this.lastSeenMessageId = 0
        this.sessionInitialized = false
        return
      }
      if (userId === this.currentUserId) return
      this.currentUserId = userId
      this.session = null
      this.messages = []
      this.lastSeenMessageId = 0
      this.sessionInitialized = false
      if (this.open) {
        await this.loadSession()
      }
    },
    applyDetail(detail: AgentSessionDetail, options?: { preserveOptimistic?: boolean }) {
      const firstLoad = !this.sessionInitialized
      const preserveOptimistic = options?.preserveOptimistic ?? this.sending
      const optimisticMessages = preserveOptimistic
        ? this.messages.filter((message) => (
          isOptimisticMessage(message) && !hasMatchingPersistedMessage(detail.messages, message)
        ))
        : []
      this.session = detail.session
      this.messages = [...detail.messages, ...optimisticMessages]
      this.sessionInitialized = true
      if (this.open || firstLoad) {
        this.lastSeenMessageId = latestPersistedMessageId(detail.messages) || this.lastSeenMessageId
      }
    },
    async loadSession() {
      if (!this.currentUserId) return
      this.loading = true
      this.error = ''
      try {
        const detail = await request<AgentSessionDetail>(`/api/v1/agent/session?user_id=${this.currentUserId}`)
        this.applyDetail(detail)
      } catch (error) {
        this.error = error instanceof Error ? error.message : String(error)
      } finally {
        this.loading = false
      }
    },
    async refresh() {
      if (!this.currentUserId || this.sending || this.uploading) return
      try {
        const detail = await request<AgentSessionDetail>(`/api/v1/agent/session?user_id=${this.currentUserId}`)
        this.applyDetail(detail)
      } catch {
        // 背景刷新失败不打断用户输入。
      }
    },
    async send(content: string, images: AgentImageAttachment[], contextRef?: AgentContextRef | null) {
      const trimmed = content.trim()
      if (!trimmed && images.length === 0) return
      if (!this.currentUserId) throw new Error('请选择当前用户')
      const optimisticId = -Date.now()
      const optimisticMessage: AgentMessage = {
        id: optimisticId,
        role: 'user',
        content: trimmed,
        attachments: { images },
        createdAt: new Date().toISOString(),
      }
      this.messages = [...this.messages, optimisticMessage]
      this.sending = true
      this.error = ''
      try {
        const detail = await request<AgentSessionDetail>(`/api/v1/agent/messages?user_id=${this.currentUserId}`, {
          method: 'POST',
          body: {
            content: trimmed,
            attachments: { images },
            contextRef: contextRef || undefined,
          } as unknown as BodyInit,
        })
        this.applyDetail(detail, { preserveOptimistic: false })
      } catch (error) {
        this.error = error instanceof Error ? error.message : String(error)
        throw error
      } finally {
        this.sending = false
      }
    },
    async upload(files: File[]) {
      if (!this.currentUserId) throw new Error('请选择当前用户')
      const form = new FormData()
      files.forEach((file) => form.append('files', file))
      this.uploading = true
      this.error = ''
      try {
        const response = await fetch(`/api/v1/agent/uploads?user_id=${this.currentUserId}`, {
          method: 'POST',
          body: form,
        })
        const data = await response.json()
        if (!response.ok) {
          throw new Error(typeof data?.detail === 'string' ? data.detail : response.statusText)
        }
        const images = Array.isArray((data as AgentUploadResult).images)
          ? (data as AgentUploadResult).images
          : []
        return images.map((image) => normalizeUploadedImage(image as unknown as Record<string, unknown>))
      } finally {
        this.uploading = false
      }
    },
    async resetContext() {
      if (!this.currentUserId) throw new Error('请选择当前用户')
      this.resetting = true
      this.error = ''
      try {
        const detail = await request<AgentSessionDetail>(`/api/v1/agent/session?user_id=${this.currentUserId}`, {
          method: 'DELETE',
        })
        this.applyDetail(detail, { preserveOptimistic: false })
        this.lastSeenMessageId = latestPersistedMessageId(this.messages)
        this.sessionInitialized = true
      } catch (error) {
        this.error = error instanceof Error ? error.message : String(error)
        throw error
      } finally {
        this.resetting = false
      }
    },
    toggleOpen() {
      this.open = !this.open
      if (this.open) {
        this.lastSeenMessageId = latestPersistedMessageId(this.messages) || this.lastSeenMessageId
        void this.loadSession()
      }
    },
  },
})
