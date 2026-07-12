<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useAgentAssistantStore } from '../../stores/agentAssistant'
import type { AgentContextRef, AgentImageAttachment, AgentMessage } from '../../types/agent'

const props = defineProps<{
  initialUserId?: number
  contextRef?: AgentContextRef | null
}>()

type MessageTextPart = {
  text: string
  url?: string
}

const assistantName = 'OS Agent'
const assistantIntroText = [
  '我是 OS Agent，可以帮你处理从执行、取证到提交缺陷的端到端的日常任务。',
  '',
  '1. 手机端：操作真机打开 App、走业务流程、确认功能、复现反馈。',
  '2. 网页端：浏览器里打开网页，免登录、查信息、核数据、探查页面表现。',
  '3. 接口/API：发请求、验接口，覆盖边界与异常，自然语言跑增删改查。',
  '4. Bug/缺陷：结合你的描述、截图和最近的执行结果，建单到飞书。',
].join('\n')
const FAB_WIDTH = 148
const FAB_HEIGHT = 62
const DEFAULT_PANEL_WIDTH = 420
const DEFAULT_PANEL_HEIGHT = 560
const MIN_PANEL_WIDTH = 320
const MIN_PANEL_HEIGHT = 360
const store = useAgentAssistantStore()
const draft = ref('')
const pendingImages = ref<AgentImageAttachment[]>([])
const useCurrentFunctionMap = ref(true)
const fileInput = ref<HTMLInputElement | null>(null)
const assistantRef = ref<HTMLElement | null>(null)
const bodyRef = ref<HTMLElement | null>(null)
const previewImage = ref<{ src: string; alt: string } | null>(null)
const position = ref({ x: 24, y: 24 })
const panelSize = ref({ width: DEFAULT_PANEL_WIDTH, height: DEFAULT_PANEL_HEIGHT })
const hasPosition = ref(false)
const dragMoved = ref(false)
let refreshTimer: number | null = null
let dragState: { startX: number; startY: number; originX: number; originY: number; moved: boolean } | null = null
let resizeState: {
  startX: number
  startY: number
  startWidth: number
  startHeight: number
  horizontal: 'left' | 'right'
  vertical: 'top' | 'bottom'
} | null = null

const canSend = computed(() => Boolean(draft.value.trim() || pendingImages.value.length > 0) && !store.sending)

const sortedMessages = computed(() => store.messages)
const assistantStyle = computed(() => ({
  left: `${position.value.x}px`,
  top: `${position.value.y}px`,
}))
const panelStyle = computed(() => ({
  width: `${panelSize.value.width}px`,
  height: `${panelSize.value.height}px`,
}))
const panelPlacementClass = computed(() => (
  typeof window !== 'undefined' && position.value.y < window.innerHeight / 2
    ? 'opens-below'
    : 'opens-above'
))
const panelAlignClass = computed(() => (
  position.value.x < 420 ? 'align-left' : 'align-right'
))
const resizeHandleClass = computed(() => {
  const horizontal = panelAlignClass.value === 'align-right' ? 'left' : 'right'
  const vertical = panelPlacementClass.value === 'opens-above' ? 'top' : 'bottom'
  return [`edge-${horizontal}`, `edge-${vertical}`]
})
const activeContextRef = computed(() => props.contextRef ?? null)
const functionMapTooltip = computed(() => {
  if (!activeContextRef.value) {
    return '当前页面没有可携带的 function map。'
  }
  if (useCurrentFunctionMap.value) {
    return '已开启：发送时会把当前需求的 function map 作为只读执行参考带给下游执行器。'
  }
  return '已关闭：本条消息不会携带当前需求的 function map。'
})

function outgoingContextRef(): AgentContextRef | null {
  const context = activeContextRef.value
  if (!context) return null
  return {
    ...context,
    useCurrentFunctionMap: useCurrentFunctionMap.value,
  }
}

function toggleFunctionMap() {
  if (!activeContextRef.value) return
  useCurrentFunctionMap.value = !useCurrentFunctionMap.value
}

function messageImages(message: AgentMessage): AgentImageAttachment[] {
  const images = message.attachments?.images
  return Array.isArray(images) ? images : []
}

function resultImages(message: AgentMessage): { image: string; platform?: string; index?: number; caption?: string }[] {
  const images = message.attachments?.keyImages
  return Array.isArray(images) ? images : []
}

function reportUrl(message: AgentMessage): string {
  const raw = message.attachments?.reportUrl
  return typeof raw === 'string' ? raw : ''
}

function bugUrl(message: AgentMessage): string {
  const raw = message.attachments?.bugUrl
  return typeof raw === 'string' ? raw : ''
}

function splitTrailingUrlPunctuation(raw: string): { url: string; suffix: string } {
  let url = raw
  let suffix = ''
  while (/[，。！？、；：.,!?;:]$/.test(url)) {
    suffix = `${url.slice(-1)}${suffix}`
    url = url.slice(0, -1)
  }
  return { url, suffix }
}

function messageTextParts(content: string): MessageTextPart[] {
  const text = String(content || '')
  const urlPattern = /https?:\/\/[^\s<>"')\]]+/g
  const parts: MessageTextPart[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = urlPattern.exec(text))) {
    const start = match.index
    const rawUrl = match[0]
    if (start > lastIndex) {
      parts.push({ text: text.slice(lastIndex, start) })
    }
    const { url, suffix } = splitTrailingUrlPunctuation(rawUrl)
    if (url) {
      parts.push({ text: url, url })
    }
    if (suffix) {
      parts.push({ text: suffix })
    }
    lastIndex = start + rawUrl.length
  }

  if (lastIndex < text.length) {
    parts.push({ text: text.slice(lastIndex) })
  }
  return parts.length ? parts : [{ text }]
}

function scrollToBottom() {
  void nextTick(() => {
    window.requestAnimationFrame(() => {
      const el = bodyRef.value
      if (el) el.scrollTop = el.scrollHeight
    })
  })
}

function clampPosition(x: number, y: number) {
  const margin = 16
  const maxX = Math.max(margin, window.innerWidth - FAB_WIDTH - margin)
  const maxY = Math.max(margin, window.innerHeight - FAB_HEIGHT - margin)
  return {
    x: Math.min(Math.max(x, margin), maxX),
    y: Math.min(Math.max(y, margin), maxY),
  }
}

function clampPanelSize(width: number, height: number) {
  if (typeof window === 'undefined') {
    return { width: DEFAULT_PANEL_WIDTH, height: DEFAULT_PANEL_HEIGHT }
  }
  const maxWidth = Math.max(MIN_PANEL_WIDTH, window.innerWidth - 32)
  const maxHeight = Math.max(MIN_PANEL_HEIGHT, window.innerHeight - 176)
  return {
    width: Math.min(Math.max(width, MIN_PANEL_WIDTH), maxWidth),
    height: Math.min(Math.max(height, MIN_PANEL_HEIGHT), maxHeight),
  }
}

function ensureInitialPosition() {
  if (hasPosition.value || typeof window === 'undefined') return
  position.value = clampPosition(window.innerWidth - FAB_WIDTH - 24, window.innerHeight - FAB_HEIGHT - 24)
  hasPosition.value = true
}

function handleResize() {
  if (typeof window === 'undefined') return
  position.value = clampPosition(position.value.x, position.value.y)
  panelSize.value = clampPanelSize(panelSize.value.width, panelSize.value.height)
}

function handleFabPointerDown(event: PointerEvent) {
  if (event.button !== 0) return
  ensureInitialPosition()
  dragState = {
    startX: event.clientX,
    startY: event.clientY,
    originX: position.value.x,
    originY: position.value.y,
    moved: false,
  }
  window.addEventListener('pointermove', handleFabPointerMove)
  window.addEventListener('pointerup', handleFabPointerUp, { once: true })
}

function handleFabPointerMove(event: PointerEvent) {
  if (!dragState) return
  const dx = event.clientX - dragState.startX
  const dy = event.clientY - dragState.startY
  if (Math.abs(dx) + Math.abs(dy) > 4) {
    dragState.moved = true
  }
  position.value = clampPosition(dragState.originX + dx, dragState.originY + dy)
}

function handleFabPointerUp() {
  if (dragState?.moved) {
    dragMoved.value = true
    window.setTimeout(() => {
      dragMoved.value = false
    }, 0)
  }
  dragState = null
  window.removeEventListener('pointermove', handleFabPointerMove)
}

function handlePanelResizePointerDown(event: PointerEvent) {
  if (event.button !== 0) return
  const horizontal = panelAlignClass.value === 'align-right' ? 'left' : 'right'
  const vertical = panelPlacementClass.value === 'opens-above' ? 'top' : 'bottom'
  resizeState = {
    startX: event.clientX,
    startY: event.clientY,
    startWidth: panelSize.value.width,
    startHeight: panelSize.value.height,
    horizontal,
    vertical,
  }
  window.addEventListener('pointermove', handlePanelResizePointerMove)
  window.addEventListener('pointerup', handlePanelResizePointerUp, { once: true })
}

function handlePanelResizePointerMove(event: PointerEvent) {
  if (!resizeState) return
  const dx = event.clientX - resizeState.startX
  const dy = event.clientY - resizeState.startY
  const width = resizeState.startWidth + (resizeState.horizontal === 'right' ? dx : -dx)
  const height = resizeState.startHeight + (resizeState.vertical === 'bottom' ? dy : -dy)
  panelSize.value = clampPanelSize(width, height)
}

function handlePanelResizePointerUp() {
  resizeState = null
  window.removeEventListener('pointermove', handlePanelResizePointerMove)
}

function handleFabClick() {
  if (dragMoved.value) return
  store.toggleOpen()
}

function handleDocumentPointerDown(event: PointerEvent) {
  if (!store.open) return
  if (previewImage.value) return
  const target = event.target
  if (!(target instanceof Node)) return
  if (assistantRef.value?.contains(target)) return
  store.open = false
}

function handleDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && previewImage.value) {
    previewImage.value = null
  }
}

async function openFiles(files: FileList | File[]) {
  const imageFiles = Array.from(files).filter((file) => file.type.startsWith('image/'))
  if (!imageFiles.length) return
  const uploaded = await store.upload(imageFiles)
  pendingImages.value = [...pendingImages.value, ...uploaded].slice(0, 6)
}

async function handleFileChange(event: Event) {
  const files = (event.target as HTMLInputElement).files
  if (files) await openFiles(files)
  if (fileInput.value) fileInput.value.value = ''
}

async function handlePaste(event: ClipboardEvent) {
  const files = Array.from(event.clipboardData?.files ?? [])
  if (files.length) {
    event.preventDefault()
    await openFiles(files)
  }
}

async function handleDrop(event: DragEvent) {
  event.preventDefault()
  const files = event.dataTransfer?.files
  if (files) await openFiles(files)
}

async function send() {
  if (!canSend.value) return
  const content = draft.value
  const images = pendingImages.value
  draft.value = ''
  pendingImages.value = []
  await store.send(content, images, outgoingContextRef())
  scrollToBottom()
}

function handleComposeKeydown(event: KeyboardEvent) {
  if (event.isComposing || event.key !== 'Enter' || event.shiftKey) return
  event.preventDefault()
  void send()
}

async function resetContext() {
  if (!window.confirm('清空当前人的 OS Agent 对话和临时上下文？')) return
  await store.resetContext()
  scrollToBottom()
}

function chooseFiles() {
  fileInput.value?.click()
}

function removePendingImage(index: number) {
  pendingImages.value = pendingImages.value.filter((_item, i) => i !== index)
}

function openImagePreview(src: string, alt: string) {
  previewImage.value = { src, alt }
}

function closeImagePreview() {
  previewImage.value = null
}

function formatTime(value?: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

watch(() => props.initialUserId, (value) => {
  void store.syncInitialUser(value)
})

watch(() => store.messages.length, scrollToBottom)
watch(() => store.open, (open) => {
  if (open) scrollToBottom()
})
watch(() => store.loading, (loading) => {
  if (!loading && store.open) scrollToBottom()
})
watch(() => store.sending, (sending) => {
  if (sending) scrollToBottom()
})

onMounted(() => {
  ensureInitialPosition()
  if (props.initialUserId) {
    void store.syncInitialUser(props.initialUserId)
  }
  refreshTimer = window.setInterval(() => {
    void store.refresh()
  }, 8000)
  window.addEventListener('resize', handleResize)
  document.addEventListener('pointerdown', handleDocumentPointerDown)
  document.addEventListener('keydown', handleDocumentKeydown)
})

onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
  window.removeEventListener('resize', handleResize)
  window.removeEventListener('pointermove', handleFabPointerMove)
  window.removeEventListener('pointermove', handlePanelResizePointerMove)
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
  document.removeEventListener('keydown', handleDocumentKeydown)
})
</script>

<template>
  <div ref="assistantRef" class="agent-assistant" :style="assistantStyle">
    <button
      class="agent-fab"
      type="button"
      :class="{ 'has-unread': store.hasUnread, 'is-open': store.open, 'is-busy': store.sending || store.uploading }"
      :title="assistantName"
      :aria-label="assistantName"
      @pointerdown="handleFabPointerDown"
      @click="handleFabClick"
    >
      <span class="agent-fab-copy">
        <strong>{{ assistantName }}</strong>
        <em>AI E2E</em>
      </span>
      <span class="agent-avatar" aria-hidden="true">
        <span class="agent-avatar-shell">
          <span class="agent-avatar-shine"></span>
          <span class="agent-avatar-face">
            <span class="agent-avatar-eye"></span>
            <span class="agent-avatar-eye"></span>
          </span>
          <span class="agent-avatar-mark">Flow</span>
        </span>
        <span class="agent-avatar-shadow"></span>
      </span>
      <b v-if="store.hasUnread" aria-label="有新消息"></b>
    </button>

    <section
      v-if="store.open"
      class="agent-panel"
      :class="[panelPlacementClass, panelAlignClass]"
      :style="panelStyle"
      @drop="handleDrop"
      @dragover.prevent
    >
      <header class="agent-header">
        <div>
          <strong>{{ assistantName }}</strong>
          <span>手机 · 网页 · 接口 · Bug · 测试数据 · 端到端</span>
        </div>
        <div class="agent-header-actions">
          <button
            class="agent-reset-button"
            type="button"
            title="清空当前上下文"
            :disabled="store.resetting"
            @click="resetContext"
          >
            {{ store.resetting ? '清空中' : '清空上下文' }}
          </button>
          <button type="button" title="关闭" @click="store.toggleOpen()">×</button>
        </div>
      </header>

      <div ref="bodyRef" class="agent-body">
        <p v-if="store.loading" class="agent-muted">加载中...</p>
        <p v-else-if="!sortedMessages.length" class="agent-empty">
          {{ assistantIntroText }}
        </p>
        <article
          v-for="message in sortedMessages"
          :key="message.id"
          class="agent-message"
          :class="`role-${message.role}`"
        >
          <div class="agent-bubble">
            <p class="agent-message-text">
              <template
                v-for="(part, index) in messageTextParts(message.content)"
                :key="`${message.id}-${index}`"
              >
                <a v-if="part.url" :href="part.url" target="_blank" rel="noreferrer">
                  {{ part.text }}
                </a>
                <span v-else>{{ part.text }}</span>
              </template>
            </p>
            <div v-if="messageImages(message).length" class="agent-image-grid">
              <button
                v-for="image in messageImages(message)"
                :key="image.url"
                class="agent-image-thumb"
                type="button"
                title="查看图片"
                @click="openImagePreview(image.url, image.filename || '图片')"
              >
                <img :src="image.thumbnailUrl || image.url" :alt="image.filename || '图片'" />
              </button>
            </div>
            <div v-if="resultImages(message).length" class="agent-image-grid">
              <button
                v-for="image in resultImages(message)"
                :key="image.image"
                class="agent-image-thumb"
                type="button"
                :title="image.caption || '查看截图'"
                @click="openImagePreview(image.image, image.caption || image.platform || '关键截图')"
              >
                <img :src="image.image" :alt="image.caption || image.platform || '关键截图'" />
              </button>
            </div>
            <div v-if="reportUrl(message) || bugUrl(message)" class="agent-links">
              <a v-if="reportUrl(message)" :href="reportUrl(message)" target="_blank" rel="noreferrer">
                查看报告
              </a>
              <a v-if="bugUrl(message)" :href="bugUrl(message)" target="_blank" rel="noreferrer">
                查看 bug
              </a>
            </div>
            <time>{{ formatTime(message.createdAt) }}</time>
          </div>
        </article>
        <article v-if="store.sending" class="agent-message role-assistant agent-processing-message">
          <div class="agent-bubble">
            <p>处理中...</p>
          </div>
        </article>
      </div>

      <footer class="agent-footer">
        <p v-if="store.error" class="agent-error">{{ store.error }}</p>
        <div v-if="pendingImages.length" class="agent-pending-images">
          <button
            v-for="(image, index) in pendingImages"
            :key="image.url"
            type="button"
            title="移除图片"
            @click="removePendingImage(index)"
          >
            <img :src="image.thumbnailUrl || image.url" :alt="image.filename || '待发送图片'" />
          </button>
        </div>
        <textarea
          v-model="draft"
          rows="3"
          placeholder="说一下你想做什么，或直接贴图..."
          @paste="handlePaste"
          @keydown="handleComposeKeydown"
        />
        <div class="agent-compose-actions">
          <input ref="fileInput" type="file" accept="image/*" multiple hidden @change="handleFileChange" />
          <button
            class="agent-function-map-toggle"
            :class="{ active: activeContextRef && useCurrentFunctionMap }"
            type="button"
            :aria-pressed="Boolean(activeContextRef && useCurrentFunctionMap)"
            :disabled="!activeContextRef"
            :data-tooltip="functionMapTooltip"
            @click="toggleFunctionMap"
          >
            function map
          </button>
          <button type="button" :disabled="store.uploading" @click="chooseFiles">
            图片
          </button>
          <button type="button" :disabled="!canSend" @click="send">
            {{ store.sending ? '发送中' : '发送' }}
          </button>
        </div>
      </footer>
      <button
        class="agent-resize-handle"
        :class="resizeHandleClass"
        type="button"
        title="拖拽调整大小"
        aria-label="拖拽调整大小"
        @pointerdown.stop.prevent="handlePanelResizePointerDown"
      ></button>
    </section>

  </div>
  <Teleport to="body">
    <div
      v-if="previewImage"
      class="agent-image-preview"
      role="dialog"
      aria-modal="true"
      @click.self="closeImagePreview"
    >
      <button type="button" title="关闭预览" @click="closeImagePreview">×</button>
      <img :src="previewImage.src" :alt="previewImage.alt" />
    </div>
  </Teleport>
</template>

<style scoped>
.agent-assistant {
  position: fixed;
  z-index: 120;
  font-size: 14px;
  perspective: 720px;
}

.agent-fab {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 148px;
  height: 62px;
  border: 1px solid rgba(15, 23, 42, 0.14);
  border-radius: 24px;
  padding: 8px 9px 8px 13px;
  background:
    radial-gradient(circle at 18% 18%, rgba(255, 255, 255, 0.95) 0 16%, transparent 36%),
    linear-gradient(135deg, rgba(248, 250, 252, 0.98) 0%, rgba(221, 252, 245, 0.96) 42%, rgba(199, 210, 254, 0.94) 100%);
  color: #0f172a;
  cursor: pointer;
  transform-style: preserve-3d;
  animation: agent-idle-float 4.2s ease-in-out infinite;
  box-shadow:
    inset -8px -10px 16px rgba(15, 23, 42, 0.1),
    inset 7px 8px 14px rgba(255, 255, 255, 0.78),
    0 12px 24px rgba(15, 23, 42, 0.17);
  will-change: transform;
}

.agent-fab::after {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: 27px;
  opacity: 0;
  border: 1px solid rgba(20, 184, 166, 0.34);
  box-shadow: 0 0 0 0 rgba(20, 184, 166, 0.2);
  pointer-events: none;
}

.agent-fab:hover {
  animation-play-state: paused;
  transform: translateY(-4px) rotateX(8deg) rotateY(-8deg);
  box-shadow:
    inset -8px -10px 16px rgba(15, 23, 42, 0.11),
    inset 7px 8px 14px rgba(255, 255, 255, 0.82),
    0 16px 30px rgba(15, 23, 42, 0.21);
}

.agent-fab.is-open {
  animation-duration: 6s;
}

.agent-fab.has-unread {
  animation: agent-unread-float 1.45s ease-in-out infinite;
}

.agent-fab.has-unread::after {
  opacity: 1;
  animation: agent-attention-pulse 1.45s ease-out infinite;
}

.agent-fab.is-busy .agent-avatar-shell {
  animation: agent-busy-light 1.6s ease-in-out infinite;
}

.agent-fab-copy {
  position: relative;
  z-index: 1;
  display: grid;
  gap: 2px;
  min-width: 0;
  text-align: left;
  transform: translateZ(14px);
}

.agent-fab-copy strong {
  font-size: 14px;
  font-weight: 800;
  letter-spacing: 0;
  line-height: 1.1;
}

.agent-fab-copy em {
  color: #475569;
  font-size: 10px;
  font-style: normal;
  line-height: 1.2;
  white-space: nowrap;
}

.agent-avatar {
  position: relative;
  flex: 0 0 42px;
  display: block;
  width: 42px;
  height: 44px;
  transform-style: preserve-3d;
}

.agent-avatar-shell {
  position: absolute;
  left: 2px;
  top: 1px;
  width: 38px;
  height: 40px;
  overflow: hidden;
  border: 1px solid rgba(15, 23, 42, 0.18);
  border-radius: 14px 14px 16px 16px;
  background:
    radial-gradient(circle at 28% 18%, rgba(255, 255, 255, 0.96) 0 12%, transparent 23%),
    linear-gradient(135deg, #f8fafc 0%, #c7f9ee 34%, #4fd1c5 62%, #1f2937 100%);
  box-shadow:
    inset -8px -10px 16px rgba(15, 23, 42, 0.28),
    inset 7px 8px 12px rgba(255, 255, 255, 0.64),
    0 12px 20px rgba(15, 23, 42, 0.19);
  transform: rotateX(13deg) rotateY(-14deg) translateZ(12px);
  transition: transform 180ms ease, box-shadow 180ms ease, filter 180ms ease;
}

.agent-fab:hover .agent-avatar-shell {
  filter: saturate(1.1);
  box-shadow:
    inset -8px -10px 16px rgba(15, 23, 42, 0.28),
    inset 7px 8px 12px rgba(255, 255, 255, 0.68),
    0 16px 26px rgba(15, 23, 42, 0.23);
  transform: rotateX(16deg) rotateY(-18deg) translateZ(18px);
}

.agent-fab.has-unread .agent-avatar-shell {
  box-shadow:
    inset -8px -10px 16px rgba(15, 23, 42, 0.3),
    inset 7px 8px 12px rgba(255, 255, 255, 0.68),
    0 14px 28px rgba(20, 184, 166, 0.32);
}

.agent-avatar-shell::before {
  content: '';
  position: absolute;
  inset: auto 6px -6px;
  height: 12px;
  border-radius: 50%;
  background: rgba(15, 23, 42, 0.24);
  transform: rotateX(64deg);
}

.agent-avatar-shine {
  position: absolute;
  left: 7px;
  top: 7px;
  width: 14px;
  height: 7px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.72);
  filter: blur(0.2px);
  transform: rotate(-22deg);
}

.agent-avatar-face {
  position: absolute;
  left: 7px;
  right: 7px;
  top: 16px;
  display: flex;
  justify-content: space-between;
}

.agent-avatar-eye {
  width: 6px;
  height: 7px;
  border-radius: 999px;
  background: #0f172a;
  box-shadow: 0 0 10px rgba(20, 184, 166, 0.72);
}

.agent-avatar-mark {
  position: absolute;
  right: 4px;
  bottom: 2px;
  min-width: 23px;
  height: 13px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.76);
  color: #ecfeff;
  font-size: 7px;
  font-weight: 800;
  line-height: 13px;
  text-align: center;
}

.agent-avatar-shadow {
  position: absolute;
  left: 6px;
  bottom: 0;
  width: 30px;
  height: 9px;
  border-radius: 50%;
  background: rgba(15, 23, 42, 0.22);
  filter: blur(5px);
  transform: translateZ(-18px);
}

.agent-fab b {
  position: absolute;
  top: 3px;
  right: 3px;
  width: 10px;
  height: 10px;
  padding: 0;
  border-radius: 999px;
  background: #dc2626;
  box-shadow: 0 4px 12px rgba(220, 38, 38, 0.32);
}

@keyframes agent-idle-float {
  0%,
  100% {
    transform: translateY(0) rotateX(0deg) rotateY(0deg);
  }
  50% {
    transform: translateY(-7px) rotateX(5deg) rotateY(-5deg);
  }
}

@keyframes agent-unread-float {
  0%,
  100% {
    transform: translateY(0) scale(1) rotateX(0deg) rotateY(0deg);
  }
  45% {
    transform: translateY(-13px) scale(1.04) rotateX(8deg) rotateY(-8deg);
  }
}

@keyframes agent-attention-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(20, 184, 166, 0.34);
    transform: scale(0.92);
  }
  100% {
    box-shadow: 0 0 0 18px rgba(20, 184, 166, 0);
    transform: scale(1.18);
  }
}

@keyframes agent-busy-light {
  0%,
  100% {
    filter: saturate(1);
  }
  50% {
    filter: saturate(1.22) brightness(1.06);
  }
}

@media (prefers-reduced-motion: reduce) {
  .agent-fab,
  .agent-fab.has-unread,
  .agent-fab.has-unread::after,
  .agent-fab.is-busy .agent-avatar-shell {
    animation: none;
  }

  .agent-fab:hover {
    transform: none;
  }
}

.agent-panel {
  position: absolute;
  width: min(420px, calc(100vw - 32px));
  height: min(560px, calc(100vh - 176px));
  min-width: 320px;
  min-height: 360px;
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 176px);
  display: grid;
  grid-template-rows: auto 1fr auto;
  overflow: hidden;
  border: 1px solid #d7dde8;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 20px 50px rgba(15, 23, 42, 0.22);
}

.agent-resize-handle {
  position: absolute;
  z-index: 2;
  width: 22px;
  height: 22px;
  padding: 0;
  border: 0;
  background: transparent;
}

.agent-resize-handle::before {
  content: '';
  position: absolute;
  inset: 5px;
  border-right: 2px solid #94a3b8;
  border-bottom: 2px solid #94a3b8;
  opacity: 0.72;
}

.agent-resize-handle:hover::before {
  border-color: #2563eb;
  opacity: 1;
}

.agent-resize-handle.edge-left {
  left: 3px;
}

.agent-resize-handle.edge-right {
  right: 3px;
}

.agent-resize-handle.edge-top {
  top: 3px;
}

.agent-resize-handle.edge-bottom {
  bottom: 3px;
}

.agent-resize-handle.edge-left::before {
  transform: rotate(90deg);
}

.agent-resize-handle.edge-top.edge-left::before {
  transform: rotate(180deg);
}

.agent-resize-handle.edge-top.edge-right::before {
  transform: rotate(270deg);
}

.agent-resize-handle.edge-bottom.edge-left::before {
  transform: rotate(90deg);
}

.agent-resize-handle.edge-top.edge-left,
.agent-resize-handle.edge-bottom.edge-right {
  cursor: nwse-resize;
}

.agent-resize-handle.edge-top.edge-right,
.agent-resize-handle.edge-bottom.edge-left {
  cursor: nesw-resize;
}

.agent-panel.align-right {
  right: 0;
}

.agent-panel.align-left {
  left: 0;
}

.agent-panel.opens-above {
  bottom: 96px;
}

.agent-panel.opens-below {
  top: 96px;
}

.agent-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid #e5e7eb;
}

.agent-header span {
  display: block;
  margin-top: 2px;
  color: #6b7280;
  font-size: 12px;
}

.agent-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.agent-header select {
  max-width: 128px;
  height: 30px;
}

.agent-header button {
  width: 30px;
  height: 30px;
}

.agent-header .agent-reset-button {
  width: auto;
  min-width: 76px;
  padding: 0 10px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  background: #fff;
  color: #334155;
  font-size: 12px;
  white-space: nowrap;
}

.agent-header .agent-reset-button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.agent-body {
  overflow-y: auto;
  padding: 14px;
  background: #f8fafc;
}

.agent-empty,
.agent-muted,
.agent-error {
  margin: 0;
  color: #6b7280;
}

.agent-error {
  color: #b91c1c;
}

.agent-empty {
  white-space: pre-wrap;
}

.agent-message {
  display: flex;
  margin-bottom: 12px;
}

.agent-message.role-user {
  justify-content: flex-end;
}

.agent-bubble {
  max-width: 86%;
  padding: 10px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.role-user .agent-bubble {
  border-color: #bfdbfe;
  background: #eff6ff;
}

.agent-processing-message .agent-bubble {
  color: #64748b;
  background: #f1f5f9;
}

.agent-bubble p {
  margin: 0;
  white-space: pre-wrap;
  line-height: 1.5;
}

.agent-message-text,
.agent-message-text a {
  overflow-wrap: anywhere;
  word-break: break-word;
}

.agent-message-text a {
  color: #2563eb;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.agent-bubble time {
  display: block;
  margin-top: 6px;
  color: #9ca3af;
  font-size: 11px;
}

.agent-image-grid,
.agent-pending-images {
  display: flex;
  gap: 8px;
  margin-top: 8px;
  overflow-x: auto;
}

.agent-image-thumb {
  flex: 0 0 auto;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: zoom-in;
}

.agent-image-thumb:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 2px;
  border-radius: 6px;
}

.agent-image-grid img,
.agent-pending-images img {
  width: 72px;
  height: 72px;
  object-fit: cover;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  background: #fff;
}

.agent-links {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.agent-links a {
  color: #2563eb;
}

.agent-footer {
  padding: 12px;
  border-top: 1px solid #e5e7eb;
  background: #fff;
}

.agent-footer textarea {
  width: 100%;
  resize: none;
  box-sizing: border-box;
  padding: 10px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  font: inherit;
}

.agent-compose-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

.agent-compose-actions button,
.agent-pending-images button {
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
}

.agent-compose-actions button {
  min-width: 64px;
  height: 32px;
}

.agent-function-map-toggle {
  position: relative;
  min-width: 104px;
  margin-right: auto;
  padding: 0 12px;
  border-color: transparent;
  background: transparent;
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
  text-transform: lowercase;
}

.agent-function-map-toggle.active {
  border-color: transparent;
  background: transparent;
  color: #2563eb;
}

.agent-function-map-toggle:hover:not(:disabled) {
  border-color: transparent;
  background: transparent;
  color: #334155;
}

.agent-function-map-toggle.active:hover:not(:disabled) {
  background: transparent;
  color: #1d4ed8;
}

.agent-function-map-toggle:disabled {
  border-color: transparent;
  background: transparent;
  color: #94a3b8;
  cursor: default;
}

.agent-function-map-toggle::after {
  content: attr(data-tooltip);
  position: absolute;
  left: 0;
  bottom: calc(100% + 8px);
  z-index: 3;
  width: 230px;
  padding: 8px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  background: #0f172a;
  color: #fff;
  font-size: 12px;
  font-weight: 400;
  line-height: 1.45;
  text-align: left;
  text-transform: none;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.22);
  opacity: 0;
  pointer-events: none;
  transform: translateY(4px);
  transition: opacity 120ms ease, transform 120ms ease;
}

.agent-function-map-toggle:hover::after,
.agent-function-map-toggle:focus-visible::after {
  opacity: 1;
  transform: translateY(0);
}

.agent-compose-actions button:last-child {
  border-color: #1f2937;
  background: #1f2937;
  color: #fff;
}

.agent-compose-actions button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.agent-pending-images button {
  padding: 0;
}

.agent-image-preview {
  position: fixed;
  inset: 0;
  z-index: 140;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.58);
  backdrop-filter: blur(2px);
}

.agent-image-preview button {
  position: absolute;
  top: 18px;
  right: 18px;
  width: 34px;
  height: 34px;
  border: 1px solid rgba(255, 255, 255, 0.64);
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.72);
  color: #fff;
  font-size: 20px;
  cursor: pointer;
}

.agent-image-preview img {
  max-width: min(920px, calc(100vw - 48px));
  max-height: calc(100vh - 64px);
  object-fit: contain;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 22px 60px rgba(15, 23, 42, 0.34);
}
</style>
