import type {
  AIPhoneDeviceList,
  AIPhoneSubmitResult,
  BugDraft,
  BugField,
  BugSubmitResult,
  CaseCoverage,
  CasePathNode,
  CasePlatformResult,
  ExecutionStatus,
  ExecutionTarget,
  RepairApplyResult,
  RepairDraft,
  RepairPreview,
} from './case'

export interface QuickFunctionFile {
  filename: string
  content: string
  charCount: number
}

export interface QuickSessionSummary {
  sessionId: string
  sourceName: string
  suiteTitle: string
  caseCount: number
  functionFiles: QuickFunctionFile[]
  feishuRequirementUrl?: string | null
  feishuBugUrl?: string | null
  currentUserId?: number | null
  createdAt?: string | null
  updatedAt?: string | null
}

export interface QuickCaseItem {
  id: number
  ordinal: number
  displayNo?: string
  suiteTitle: string
  sourceName: string
  assetStatus: string
  rawTitle: string
  cleanTitle: string
  path: string
  pathNodes?: CasePathNode[]
  scenarioTags: string[]
  manual: boolean
  executionStatus: ExecutionStatus
  coverage?: CaseCoverage
  lifecycleState: string
  attentionReason?: string | null
  caseType: string
  executionTarget: ExecutionTarget
  tagReason?: string | null
  tagConfidence: number
  runEnabled: boolean
  reportUrl?: string | null
  failureType?: string | null
  failureSummary?: string | null
  bugUrl?: string | null
  bugs?: { url: string; id: string }[]
  diagnosisReady?: boolean
  bugDraftReady?: boolean
  externalSubmissionId?: string | null
  executionStartedAt?: string | null
  executionFinishedAt?: string | null
  preconditions: string
  stepsText: string
  expectedResult: string
  coreNodes?: Record<string, unknown>
}

export interface QuickSessionDetail {
  session: QuickSessionSummary
  cases: QuickCaseItem[]
}

export interface QuickImportResult extends QuickSessionDetail {
  warnings: string[]
}

export interface QuickCaseUpdate {
  rawTitle?: string
  cleanTitle?: string
  preconditions?: string
  stepsText?: string
  expectedResult?: string
}

export interface QuickWorkItemUpdate {
  caseId: number
  executionStatus?: ExecutionStatus
  executionTarget?: ExecutionTarget
  runEnabled?: boolean
}

export interface QuickExportResult {
  filename: string
  content: string
  cleared: boolean
}

export interface QuickFeishuTarget {
  url: string
  projectKey?: string | null
  workItemType?: string | null
  workItemId?: string | null
  title?: string | null
  readable: boolean
  message?: string | null
}

export interface QuickAIPhoneSubmitPayload {
  sessionId: string
  caseIds: number[]
  deviceAliasPools?: Record<string, string[]> | null
  submissionName?: string | null
  cacheMode?: 'off' | 'v1' | 'v2' | 'v3'
  retryMax?: number
  // 一次点击的聚合 ID（检查点 7 台账用），批量执行时同一次点击共享。
  executionRequestGroupId?: string
}

export type {
  AIPhoneDeviceList,
  AIPhoneSubmitResult,
  BugDraft,
  BugField,
  BugSubmitResult,
  CasePlatformResult,
  RepairApplyResult,
  RepairDraft,
  RepairPreview,
}
