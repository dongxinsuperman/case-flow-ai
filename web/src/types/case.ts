export type ExecutionStatus = 'not_run' | 'running' | 'passed' | 'failed'
export type ExecutionTarget = 'app' | 'web' | 'api' | 'mixed' | 'manual' | 'unknown'

// 覆盖标记：纯展示/提醒层，与 executionStatus 解耦，不参与任何执行流转。
// 三态点按循环：未执行(none) → 通过(passed) → 失败(failed) → 未执行。
// 按执行器切换覆盖泳道：app → 安卓/iOS/鸿蒙；web → Chrome/Safari/Firefox。
// AFN 执行可顺带写对应泳道；人工可手动点。仅在有泳道的执行器（app/web）展示。
export type CoverageState = 'none' | 'passed' | 'failed'
export type CoverageLane = 'android' | 'ios' | 'harmony' | 'chrome' | 'safari' | 'firefox'

export type CaseCoverage = Partial<Record<CoverageLane, CoverageState>>

export interface CasePathNode {
  level?: number
  label: string
  rawText?: string
  displayText: string
}

export interface CaseListItem {
  id: number
  batchId: number
  ordinal: number
  displayNo?: string
  suiteTitle: string
  sourceName: string
  assetStatus: string
  moduleName: string
  productFeature: string
  testFeature: string
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
  bugs?: SubmittedBug[]
  diagnosisReady?: boolean
  bugDraftReady?: boolean
  externalSubmissionId?: string | null
  executionStartedAt?: string | null
  executionFinishedAt?: string | null
  preconditions: string
  stepsText: string
  expectedResult: string
}

export interface SubmittedBug {
  url: string
  id: string
}

export interface CasePlatformResult {
  platform: string
  state: string
  reportUrl?: string | null
  runId?: string | null
  statusReason?: string | null
}

export interface User {
  id: number
  name: string
  displayName: string
  status: string
}

export interface RequirementTask {
  requirementItemId: number
  requirementItemTitle: string
  requirementLifecycleStatus: string
  groupId?: number | null
  groupName?: string | null
  caseCount: number
  notRun: number
  running: number
  passed: number
  failed: number
  attentionChanged: number
  autoDiscoveryEnabled: boolean
}

export interface RequirementItem {
  id: number
  groupId?: number | null
  title: string
  status: string
  version?: string | null
  lifecycleStatus: string
  sourceSpace?: string | null
  testerUserIds?: number[]
  card?: PoolCard | null
}

export interface RequirementGroup {
  id: number
  name: string
  status: string
  items: RequirementItem[]
}

export interface RequirementCatalog {
  groups: RequirementGroup[]
  ungroupedItems: RequirementItem[]
  total?: number
  page?: number
  pageSize?: number
  filterUserIds?: number[]
  sprints?: PoolCardSprint[]
}

export interface PoolCardRole {
  label: string
  names: string[]
}

export interface PoolCardSprint {
  id: string
  name: string
}

export interface PoolCard {
  number?: number | string | null
  status?: string | null
  createdDate?: string | null
  link?: string | null
  roles: PoolCardRole[]
  sprints?: PoolCardSprint[]
}

export interface RequirementPoolItem {
  id: number
  externalKey: string
  title: string
  description?: string | null
  sourceType: string
  status: string
  lifecycleStatus: string
  sourceSpace?: string | null
  ownerUserId?: number | null
  ownerName?: string | null
  testerUserIds?: number[]
  card?: PoolCard | null
  boundGroupId?: number | null
  boundGroupName?: string | null
  boundItemId?: number | null
  boundItemTitle?: string | null
}

export interface RequirementPoolPage {
  items: RequirementPoolItem[]
  total: number
  attachableTotal: number
  page: number
  pageSize: number
  filterUserIds: number[]
  sprints: PoolCardSprint[]
}

export interface FeishuPullJob {
  jobId: string
  projectKeys?: string[] | null
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  message: string
  currentSpace?: string | null
  fetched: number
  created: number
  updated: number
  spaces: Array<{
    projectKey: string
    name: string
    fetched: number
    matched?: number
    created: number
    updated: number
    skippedNoQa?: number
    error?: string
  }>
  error?: string | null
  createdAt?: string
  startedAt?: string | null
  finishedAt?: string | null
}

export interface HomeSummary {
  requirements: number
  caseCount: number
  notRun: number
  running: number
  passed: number
  failed: number
  attentionChanged: number
}

export interface HomeDashboard {
  user: User
  summary: HomeSummary
  requirements: RequirementTask[]
}

export interface CaseWorkItemUpdate {
  caseId: number
  executionStatus?: ExecutionStatus
  executionTarget?: ExecutionTarget
  runEnabled?: boolean
}

export interface CaseAssetUpdate {
  rawTitle?: string
  cleanTitle?: string
  preconditions?: string
  stepsText?: string
  expectedResult?: string
}

export interface CaseAssetCreate {
  requirementItemId: number
  batchId: number
  pathNodes: CasePathNode[]
  rawTitle: string
  preconditions?: string
  stepsText?: string
  expectedResult?: string
}

export interface CaseSuiteExportResult {
  batchId: number
  suiteTitle: string
  filename: string
  content: string
  caseCount: number
}

export interface CaseSuiteDeleteResult {
  requirementItemId: number
  batchId: number
  suiteTitle: string
  deletedCaseCount: number
  deletedRunningCount: number
  deletedBatchId: number
  message: string
}

export interface AIPhoneDevice {
  alias?: string
  serial?: string
  platform?: string
  brand?: string
  model?: string
  osVersion?: string
  occupancy?: 'idle' | 'busy'
  lockHolderType?: string
  [key: string]: unknown
}

export interface AIPhoneDeviceList {
  source: string
  devices: AIPhoneDevice[]
  error?: string | null
}

export interface AIPhoneSubmitResult {
  submissionId: string
  submissionName?: string | null
  callbackUrl: string
  batchId: number
  submittedCount: number
  response: Record<string, unknown>
}

export interface RepairDraft {
  draftId?: number | null
  caseId: number
  caseTitle: string
  path: string
  status: string
  repairable: boolean
  failureType: string
  reason: string
  fixReason?: string
  evidence?: string
  keyImage?: string | null
  keyImages?: { platform: string; image: string }[]
  repairChannel?: string
  process?: Array<{
    stage?: string
    round?: number
    shownImages?: number[]
    shownWindows?: string[]
    note?: string
    decision?: string
    requestImages?: number[]
    rawRequestImages?: number[]
    requestWindows?: string[]
    selectedWindows?: string[]
    conclusionReason?: string
    failureAnchor?: string
    verificationTarget?: string
    expectedStandard?: string
    failureTypeHint?: string
    allowed?: boolean
    reason?: string
    rawDecision?: Record<string, unknown>
  }>
  originalSteps: string
  proposedSteps: string
  originalPreconditions?: string
  proposedPreconditions?: string
  originalExpected?: string
  proposedExpected?: string
  reportUrl?: string | null
  reportSummary: string
  bugUrl?: string | null
  modelName?: string | null
  gate: Record<string, unknown>
  createdAt?: string | null
}

export interface FunctionMapFile {
  filename: string
  content: string
  charCount: number
}

export interface FunctionMapState {
  groupId: number
  files: FunctionMapFile[]
  totalChars: number
  maxChars: number
  overwritten?: boolean
}

export interface RepairPreview {
  items: RepairDraft[]
}

export interface RepairApplyResult {
  caseId: number
  message: string
}

export interface ReviewCaseSnapshot {
  ordinal?: number | null
  path: string
  pathNodes?: CasePathNode[]
  title?: string | null
  moduleName?: string | null
  productFeature?: string | null
  testFeature?: string | null
  cleanTitle?: string | null
  rawTitle?: string | null
  preconditions?: string
  stepsText?: string
  steps?: string
  expected?: string
  expectedResult?: string
}

export interface ReviewCandidate extends ReviewCaseSnapshot {
  caseId: number
  similarity: number
  changeHint: string
}

export interface ImportReviewItem {
  incomingKey: string
  incoming: ReviewCaseSnapshot
  candidates: ReviewCandidate[]
  bestSimilarity?: number | null
  primaryOldCaseId?: number | null
  primarySimilarity?: number | null
  modelUsed: boolean
  modelSummary?: string
  modelSimilarity?: number | null
  modelBestCaseId?: number | null
  modelShouldLock?: boolean
  modelRisk?: string | null
}

export interface DeleteReviewItem {
  deleteKey: string
  oldCaseId: number
  oldCase: ReviewCaseSnapshot
  reason: string
}

export interface ImportReview {
  reviewId?: string
  suiteTitle: string
  caseCount: number
  exactCount: number
  reviewCount: number
  deleteCount: number
  primaryMatchThreshold: number
  modelTopN: number
  exactOldIds?: number[]
  reviewItems: ImportReviewItem[]
  deleteItems: DeleteReviewItem[]
}

export interface ImportMarkdownResult {
  mode:
    | 'imported'
    | 'no_changes'
    | 'collision_review'
    | 'independent_confirm_required'
    | 'collision_pending'
  message?: string | null
  taskId?: string | null
  suiteTitle?: string | null
  caseCount?: number | null
  batch?: Record<string, unknown> | null
  existingBatch?: Record<string, unknown> | null
  review?: ImportReview | null
  warnings: string[]
}

export interface ImportJobStatus<T = unknown> {
  status: 'pending' | 'done' | 'error'
  result?: T | null
  error?: string | null
  elapsedMs?: number | null
}

export interface BugEditableOption {
  name: string
  id: string
}

export interface BugField {
  fieldKey: string
  label: string
  type?: string | null
  editable: boolean
  required: boolean
  options: BugEditableOption[]
  selected: string | string[] | null
  display: string
  submitValue?: unknown
}

export interface BugDraft {
  caseId: number
  space?: string | null
  title: string
  description: string
  fields: BugField[]
  hasDiagnosisImage: boolean
  keyImage?: string | null
  keyImages?: { platform: string; image: string }[]
  existingBugUrl?: string | null
  submittedBugs?: SubmittedBug[]
}

export interface BugSubmitResult {
  caseId: number
  bugId: number
  bugUrl: string
  submittedCount?: number
  message: string
}

export interface BugImageUploadResult {
  images: { platform: string; image: string }[]
}

export type ImportDecisionAction = 'add' | 'replace' | 'skip' | 'delete' | 'keep'

export interface ImportDecision {
  incomingKey?: string
  oldCaseId?: number
  action: ImportDecisionAction
}
