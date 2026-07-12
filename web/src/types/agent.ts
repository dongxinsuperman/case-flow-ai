export interface AgentSessionSummary {
  id: number
  userId: number
  title: string
  bugTarget?: Record<string, unknown>
  pendingAction?: Record<string, unknown>
  createdAt?: string | null
  updatedAt?: string | null
}

export interface AgentMessage {
  id: number
  role: 'user' | 'assistant' | 'dispatch' | 'result' | string
  content: string
  dispatchId?: number | null
  attachments?: AgentAttachments
  createdAt?: string | null
}

export interface AgentImageAttachment {
  url: string
  thumbnailUrl?: string | null
  filename?: string
  mime?: string
  size?: number
}

export type AgentContextMode = 'standard' | 'quick'

export interface AgentContextRef {
  mode: AgentContextMode
  requirementItemId?: number | null
  quickSessionId?: string | null
  useCurrentFunctionMap?: boolean
}

export interface AgentAttachments {
  images?: AgentImageAttachment[]
  keyImages?: { image: string; platform?: string; index?: number; caption?: string }[]
  reportUrl?: string | null
  bugUrl?: string | null
  toolKey?: string | null
  status?: string | null
  [key: string]: unknown
}

export interface AgentSessionDetail {
  session: AgentSessionSummary
  messages: AgentMessage[]
}

export interface AgentUploadResult {
  images: AgentImageAttachment[]
}
