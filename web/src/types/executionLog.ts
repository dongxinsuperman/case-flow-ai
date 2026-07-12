export interface ExecutionCallLog {
  id: number
  callId: string
  requestGroupId: string | null
  mode: string
  scope: string
  entry: string
  executor: string
  requirementItemId: number | null
  quickSessionId: string | null
  caseIds: number[]
  executionBatchId: number | null
  submissionId: string | null
  triggerUserId: number | null
  triggerUserName: string | null
  requirementItemTitle: string | null
  quickSessionTitle: string | null
  submittedFunctionMapContext: string | null
  input: Record<string, unknown>
  functionMapResult: Record<string, unknown> | null
  effectiveContext: Record<string, unknown> | null
  status: string
  failureReason: string | null
  createdAt: string
  updatedAt: string
}

export interface ExecutionCallLogPage {
  items: ExecutionCallLog[]
  total: number
  page: number
  pageSize: number
}
