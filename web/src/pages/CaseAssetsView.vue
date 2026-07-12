<script setup lang="ts">
import { computed, defineComponent, nextTick, onMounted, onUnmounted, reactive, ref, watch, type PropType } from 'vue'
import { useRoute } from 'vue-router'
import { ApiError, request } from '../api/client'
import CaseAssetCard from '../components/case-assets/CaseAssetCard.vue'
import CaseAssetPathBranch from '../components/case-assets/CaseAssetPathBranch.vue'
import ImportProgressOverlay from '../components/ImportProgressOverlay.vue'
import UserSearchSelect from '../components/UserSearchSelect.vue'
import { useCaseWorkbenchStore } from '../stores/caseWorkbench'
import type {
  CaseAssetCreate,
  CaseSuiteDeleteResult,
  CaseSuiteExportResult,
  CaseListItem,
  CasePathNode,
  DeleteReviewItem,
  ImportDecision,
  ImportJobStatus,
  ImportMarkdownResult,
  ImportReview,
  ImportReviewItem,
  RequirementCatalog,
  RequirementGroup,
  RequirementItem,
  ReviewCandidate,
  ReviewCaseSnapshot,
} from '../types/case'
import { filterVisibleUsers } from '../utils/visibleUsers'

interface AssetPathGroup {
  nodeId: string
  label: string
  name: string
  count: number
  children: AssetPathGroup[]
  cases: CaseListItem[]
}

interface BuildAssetPathGroup {
  label: string
  name: string
  count: number
  children: BuildAssetPathGroup[]
  childMap: Map<string, BuildAssetPathGroup>
  cases: CaseListItem[]
}

interface AssetSuiteGroup {
  batchId: number
  suiteTitle: string
  sourceName: string
  count: number
  runningCount: number
  pathGroups: AssetPathGroup[]
  cases: CaseListItem[]
}

interface CasePathOption {
  key: string
  label: string
  nodes: CasePathNode[]
}

const route = useRoute()
const store = useCaseWorkbenchStore()
const UNGROUPED_GROUP_ID = -1
const UNGROUPED_GROUP_NAME = '未进入目录'
const groups = ref<RequirementGroup[]>([])
const ungroupedItems = ref<RequirementCatalog['ungroupedItems']>([])
const cases = ref<CaseListItem[]>([])
const spaces = ref<{ projectKey: string; name: string }[]>([])
const selectedGroupId = ref<number | null>(null)
const selectedRequirementId = ref<number | null>(null)
const catalogFilter = ref('')
const catalogPage = ref(1)
const catalogPageSize = 50
const catalogTotal = ref(0)
const catalogFilterUserIds = ref<number[]>([])
const catalogSprints = ref<{ id: string; name: string }[]>([])
const spaceFilter = ref<string>('all')
const personFilter = ref<number | 'all'>('all')
const sprintFilter = ref<string>('all')
const testingOnly = ref(false)
const expandedGroups = ref<Set<number>>(new Set())
const selectedFileName = ref('')
const selectedFileContent = ref('')
const fileInputRef = ref<HTMLInputElement | null>(null)
const loading = ref(false)
const importing = ref(false)
const IMPORT_DEFAULT_STAGES = ['解析 Markdown', '规则识别执行端', '智能打标中', '写入用例']
const COLLISION_STAGES = ['解析 Markdown', '本地碰撞匹配', '模型碰撞判断']
const COLLISION_HINT = '模型正在进行二次导入碰撞判断，请耐心等待；失败会明确报错，绝不会未碰撞就入库。'
const importProgress = reactive({
  visible: false,
  title: '正在导入 Case',
  filename: '',
  startedAt: 0,
  elapsedSeconds: 0,
  stageIndex: 0,
  stages: [...IMPORT_DEFAULT_STAGES] as string[],
  hint: '',
})
const importProgressText = computed(() => importProgress.stages[importProgress.stageIndex] || '')
let importProgressTimer: number | null = null
const exportingSuiteId = ref<number | null>(null)
const deletingSuiteId = ref<number | null>(null)
const error = ref('')
const notice = ref('')
const review = ref<ImportReview | null>(null)
const independentConfirm = ref<ImportMarkdownResult | null>(null)
const reviewDecisions = reactive<Record<string, ImportDecision>>({})
const deleteDecisions = reactive<Record<number, ImportDecision>>({})
const editingCase = ref<CaseListItem | null>(null)
const creatingSuite = ref<AssetSuiteGroup | null>(null)
const creatingCase = ref(false)
const deletingCase = ref<CaseListItem | null>(null)
const deletingSuite = ref<AssetSuiteGroup | null>(null)
const expandedCaseId = ref<number | null>(null)
const expandedSuiteIds = ref<Set<number>>(new Set())
const createForm = reactive({
  pathKey: '',
  rawTitle: '',
  preconditions: '',
  stepsText: '',
  expectedResult: '',
})
const editForm = reactive({
  moduleName: '',
  productFeature: '',
  testFeature: '',
  rawTitle: '',
  preconditions: '',
  stepsText: '',
  expectedResult: '',
})

const reviewFields = [
  { key: 'moduleName', label: '模块' },
  { key: 'productFeature', label: '功能点' },
  { key: 'testFeature', label: '测试功能点' },
  { key: 'rawTitle', label: '测试标题' },
  { key: 'preconditions', label: '前置条件' },
  { key: 'stepsText', label: '操作步骤' },
  { key: 'expectedResult', label: '预期结果' },
] as const

const visibleUsers = computed(() => filterVisibleUsers(store.users))

const CaseSnapshot = defineComponent({
  props: {
    title: { type: String, required: true },
    caseData: { type: Object as PropType<ReviewCaseSnapshot | null>, default: null },
  },
  template: `
    <section class="case-snapshot">
      <h4>{{ title }}</h4>
      <template v-if="caseData">
        <p><b>路径</b>{{ caseData.path }}</p>
        <p><b>标题</b>{{ caseData.rawTitle || '缺少完整标题' }}</p>
        <p><b>前置条件</b>{{ caseData.preconditions || '无' }}</p>
        <p><b>操作步骤</b>{{ caseData.stepsText || caseData.steps || '无' }}</p>
        <p><b>预期结果</b>{{ caseData.expectedResult || caseData.expected || '无' }}</p>
      </template>
      <template v-else>
        <p>没有可比较的旧候选。</p>
      </template>
    </section>
  `,
})

const catalogGroups = computed<RequirementGroup[]>(() => {
  const items = ungroupedItems.value
  return items.length
    ? [...groups.value, { id: UNGROUPED_GROUP_ID, name: UNGROUPED_GROUP_NAME, status: 'active', items }]
    : groups.value
})

const selectableUsers = computed(() => {
  const userIds = new Set<number>(catalogFilterUserIds.value)
  return visibleUsers.value.filter((user) => userIds.has(user.id))
})

const sprintOptions = computed(() => catalogSprints.value)

function matchesCatalogFilters(item: RequirementItem) {
  return (
    (spaceFilter.value === 'all' || item.sourceSpace === spaceFilter.value) &&
    (!testingOnly.value || item.lifecycleStatus === '测试中') &&
    (personFilter.value === 'all' || (item.testerUserIds ?? []).includes(personFilter.value as number)) &&
    (sprintFilter.value === 'all' || (item.card?.sprints ?? []).some((sp) => sp.id === sprintFilter.value))
  )
}

function filterGroupItems(group: RequirementGroup, keyword: string): RequirementGroup | null {
  const groupHit = group.name.toLowerCase().includes(keyword)
  const items = group.items.filter(
    (item) =>
      matchesCatalogFilters(item) &&
      (
        !keyword ||
        groupHit ||
        item.title.toLowerCase().includes(keyword) ||
        (item.version ?? '').toLowerCase().includes(keyword)
      ),
  )
  return items.length ? { ...group, items } : null
}

const filteredDirectoryGroups = computed(() => {
  const keyword = catalogFilter.value.trim().toLowerCase()
  return groups.value
    .map((group) => filterGroupItems(group, keyword))
    .filter((group): group is RequirementGroup => group !== null)
})

const filteredUngroupedItems = computed(() => {
  const keyword = catalogFilter.value.trim().toLowerCase()
  return ungroupedItems.value.filter(
    (item) =>
      matchesCatalogFilters(item) &&
      (!keyword || item.title.toLowerCase().includes(keyword) || (item.version ?? '').toLowerCase().includes(keyword)),
  )
})

const catalogPageCount = computed(() => Math.max(1, Math.ceil(catalogTotal.value / catalogPageSize)))
const currentCatalogPage = computed(() => Math.min(catalogPage.value, catalogPageCount.value))
const catalogPageStart = computed(() =>
  catalogTotal.value ? (currentCatalogPage.value - 1) * catalogPageSize + 1 : 0,
)
const catalogPageEnd = computed(() => Math.min(currentCatalogPage.value * catalogPageSize, catalogTotal.value))

function toggleGroup(groupId: number) {
  const next = new Set(expandedGroups.value)
  if (next.has(groupId)) {
    next.delete(groupId)
  } else {
    next.add(groupId)
  }
  expandedGroups.value = next
}

// 有筛选关键字时自动展开（命中的需求才看得见）；否则按手动折叠状态。
function isGroupOpen(groupId: number) {
  return catalogFilter.value.trim() !== '' || expandedGroups.value.has(groupId)
}

function selectPersonFilter(value: number | string | null) {
  personFilter.value = typeof value === 'number' ? value : 'all'
}

function syncDependentCatalogFilters() {
  if (
    personFilter.value !== 'all' &&
    !selectableUsers.value.some((user) => user.id === personFilter.value)
  ) {
    personFilter.value = 'all'
  }
  if (
    sprintFilter.value !== 'all' &&
    !sprintOptions.value.some((sp) => sp.id === sprintFilter.value)
  ) {
    sprintFilter.value = 'all'
  }
}

function firstVisibleRequirement(): { groupId: number; itemId: number } | null {
  const group = filteredDirectoryGroups.value.find((candidate) => candidate.items.length > 0)
  if (group?.items[0]) {
    return { groupId: group.id, itemId: group.items[0].id }
  }
  const item = filteredUngroupedItems.value[0]
  return item ? { groupId: UNGROUPED_GROUP_ID, itemId: item.id } : null
}

function selectedRequirementVisible() {
  if (!selectedRequirementId.value) {
    return false
  }
  return [...filteredDirectoryGroups.value, { id: UNGROUPED_GROUP_ID, items: filteredUngroupedItems.value }]
    .some((group) => group.items.some((item) => item.id === selectedRequirementId.value))
}

async function ensureSelectedRequirementVisible() {
  if (selectedRequirementVisible()) {
    return
  }
  const first = firstVisibleRequirement()
  selectedGroupId.value = first?.groupId ?? null
  selectedRequirementId.value = first?.itemId ?? null
  if (first?.groupId) {
    expandedGroups.value = new Set([first.groupId])
  }
  await loadCases()
}

const selectedGroup = computed(
  () => catalogGroups.value.find((item) => item.id === selectedGroupId.value) ?? null,
)

const selectedRequirement = computed(
  () => selectedGroup.value?.items.find((item) => item.id === selectedRequirementId.value) ?? null,
)

const suiteGroups = computed<AssetSuiteGroup[]>(() => {
  const suites = new Map<number, CaseListItem[]>()
  for (const item of cases.value) {
    if (!suites.has(item.batchId)) {
      suites.set(item.batchId, [])
    }
    suites.get(item.batchId)!.push(item)
  }
  return [...suites.entries()].map(([batchId, items]) => ({
    batchId,
    suiteTitle: items[0]?.suiteTitle || '测试集无',
    sourceName: uniqueValues(items.map((item) => item.sourceName)).join(' / ') || '无',
    count: items.length,
    runningCount: items.filter((item) => item.executionStatus === 'running').length,
    pathGroups: buildPathGroups(items),
    cases: items.filter((item) => itemPathNodes(item).length === 0),
  }))
})

function buildPathGroups(items: CaseListItem[]): AssetPathGroup[] {
  const roots: BuildAssetPathGroup[] = []
  const rootMap = new Map<string, BuildAssetPathGroup>()
  for (const item of items) {
    let groups = roots
    let groupMap = rootMap
    let current: BuildAssetPathGroup | null = null
    for (const node of itemPathNodes(item)) {
      const key = `${node.label}\u0000${node.displayText}`
      let group = groupMap.get(key)
      if (!group) {
        group = {
          label: node.label,
          name: node.displayText,
          count: 0,
          children: [],
          childMap: new Map<string, BuildAssetPathGroup>(),
          cases: [],
        }
        groupMap.set(key, group)
        groups.push(group)
      }
      group.count += 1
      current = group
      groups = group.children
      groupMap = group.childMap
    }
    if (current) {
      current.cases.push(item)
    }
  }
  return roots.map((group, index) => normalizeAssetPathGroup(group, `asset-path-${index}`))
}

function normalizeAssetPathGroup(group: BuildAssetPathGroup, nodeId: string): AssetPathGroup {
  return {
    nodeId,
    label: group.label,
    name: group.name,
    count: group.count,
    children: group.children.map((child, index) => normalizeAssetPathGroup(child, `${nodeId}-${index}`)),
    cases: group.cases,
  }
}

function itemPathNodes(item: CaseListItem): CasePathNode[] {
  const configured = (item.pathNodes || [])
    .map((node) => ({
      level: node.level,
      label: node.label || '层级',
      rawText: node.rawText,
      displayText: node.displayText || node.rawText || '',
    }))
    .filter((node) => node.displayText)
  if (configured.length) {
    return configured
  }
  return []
}

function casePathKey(nodes: CasePathNode[]) {
  return nodes.map((node) => `${node.label}\u0000${node.displayText}`).join('\u0001')
}

function casePathLabel(nodes: CasePathNode[]) {
  return nodes.map((node) => `${node.label}：${node.displayText}`).join(' / ')
}

function pathOptionsForSuite(suite: AssetSuiteGroup): CasePathOption[] {
  const options: CasePathOption[] = []
  const seen = new Set<string>()
  for (const item of cases.value.filter((candidate) => candidate.batchId === suite.batchId)) {
    const nodes = itemPathNodes(item)
    if (!nodes.length) {
      continue
    }
    const key = casePathKey(nodes)
    if (seen.has(key)) {
      continue
    }
    seen.add(key)
    options.push({
      key,
      label: casePathLabel(nodes),
      nodes,
    })
  }
  return options
}

const createPathOptions = computed(() => (
  creatingSuite.value ? pathOptionsForSuite(creatingSuite.value) : []
))

const selectedCreatePathOption = computed(() =>
  createPathOptions.value.find((option) => option.key === createForm.pathKey) ?? null,
)

const pendingReviewCount = computed(() => {
  if (!review.value) {
    return 0
  }
  const replaced = replacedOldCaseIds.value
  const missingIncoming = review.value.reviewItems.filter(
    (item) => !reviewDecisions[item.incomingKey],
  ).length
  const missingDeletes = review.value.deleteItems.filter(
    (item) => !replaced.has(item.oldCaseId) && !deleteDecisions[item.oldCaseId],
  ).length
  return missingIncoming + missingDeletes
})

const allReviewHandled = computed(() => Boolean(review.value) && pendingReviewCount.value === 0)

const replacedOldCaseIds = computed(() => {
  const ids = new Set<number>()
  for (const decision of Object.values(reviewDecisions)) {
    if (decision.action === 'replace' && decision.oldCaseId) {
      ids.add(decision.oldCaseId)
    }
  }
  return ids
})

const moduleOptions = computed(() => uniqueValues(cases.value.map((item) => item.moduleName)))

const featureOptions = computed(() => uniqueValues(
  cases.value
    .filter((item) => item.moduleName === editForm.moduleName)
    .map((item) => item.productFeature),
))

const testFeatureOptions = computed(() => uniqueValues(
  cases.value
    .filter(
      (item) =>
        item.moduleName === editForm.moduleName &&
        item.productFeature === editForm.productFeature,
    )
    .map((item) => item.testFeature),
))

onMounted(async () => {
  loading.value = true
  try {
    await store.loadUsers()
    const wantedItemId = Number(route.query.item)
    const focusItemId = Number.isFinite(wantedItemId) && wantedItemId > 0 ? wantedItemId : undefined
    spaces.value = await store.listFeishuSpaces()
    await loadCatalog({ focusItemId })
    await scrollSelectedRequirementIntoView()
  } catch (err) {
    error.value = normalizeError(err)
  } finally {
    loading.value = false
  }
})

async function selectRequirement(groupId: number, requirementId: number) {
  selectedGroupId.value = groupId
  selectedRequirementId.value = requirementId
  expandedCaseId.value = null
  resetImportState()
  await loadCases()
}

watch([spaceFilter, personFilter, sprintFilter, testingOnly, catalogFilter], () => {
  syncDependentCatalogFilters()
  catalogPage.value = 1
  void loadCatalog()
})

async function scrollSelectedRequirementIntoView() {
  await nextTick()
  if (!selectedRequirementId.value) {
    return
  }
  const target = document.querySelector<HTMLElement>(
    `.asset-sidebar [data-requirement-id="${selectedRequirementId.value}"]`,
  )
  target?.scrollIntoView({ block: 'center' })
}

async function loadCases() {
  if (!selectedRequirementId.value) {
    cases.value = []
    expandedSuiteIds.value = new Set()
    return
  }
  cases.value = await request<CaseListItem[]>(
    `/api/v1/workbench-cases?requirement_item_id=${selectedRequirementId.value}`,
  )
  expandedSuiteIds.value = new Set()
  if (!cases.value.some((item) => item.id === expandedCaseId.value)) {
    expandedCaseId.value = null
  }
}

function buildCatalogQuery(focusItemId?: number) {
  const query = new URLSearchParams({
    page: String(catalogPage.value),
    page_size: String(catalogPageSize),
    testing_only: String(testingOnly.value),
  })
  if (spaceFilter.value !== 'all') {
    query.set('source_space', spaceFilter.value)
  }
  if (personFilter.value !== 'all') {
    query.set('person_id', String(personFilter.value))
  }
  if (sprintFilter.value !== 'all') {
    query.set('sprint_id', sprintFilter.value)
  }
  const keyword = catalogFilter.value.trim()
  if (keyword) {
    query.set('keyword', keyword)
  }
  if (focusItemId) {
    query.set('focus_item_id', String(focusItemId))
  }
  return query.toString()
}

async function loadCatalog(options: { focusItemId?: number } = {}) {
  const catalog = await request<RequirementCatalog>(`/api/v1/requirement-catalog?${buildCatalogQuery(options.focusItemId)}`)
  groups.value = catalog.groups
  ungroupedItems.value = catalog.ungroupedItems
  catalogTotal.value = catalog.total ?? catalogGroups.value.reduce((sum, group) => sum + group.items.length, 0)
  catalogPage.value = catalog.page ?? catalogPage.value
  catalogFilterUserIds.value = catalog.filterUserIds ?? []
  catalogSprints.value = catalog.sprints ?? []
  let nextGroupId = selectedGroupId.value
  let nextRequirementId = selectedRequirementId.value
  if (options.focusItemId) {
    const hit = catalogGroups.value.find((group) => group.items.some((item) => item.id === options.focusItemId))
    nextGroupId = hit?.id ?? nextGroupId
    nextRequirementId = hit ? options.focusItemId : nextRequirementId
  }
  selectedGroupId.value = nextGroupId
  selectedRequirementId.value = nextRequirementId
  if (!selectedRequirementVisible()) {
    await ensureSelectedRequirementVisible()
  } else {
    await loadCases()
  }
}

function changeCatalogPage(delta: number) {
  catalogPage.value = Math.min(catalogPageCount.value, Math.max(1, catalogPage.value + delta))
  void loadCatalog()
}

function toggleSuite(batchId: number) {
  const next = new Set(expandedSuiteIds.value)
  if (next.has(batchId)) {
    next.delete(batchId)
  } else {
    next.add(batchId)
  }
  expandedSuiteIds.value = next
}

function isSuiteOpen(batchId: number) {
  return expandedSuiteIds.value.has(batchId)
}

async function exportSuiteMarkdown(suite: AssetSuiteGroup) {
  if (!selectedRequirementId.value || exportingSuiteId.value !== null) {
    return
  }
  exportingSuiteId.value = suite.batchId
  error.value = ''
  notice.value = ''
  try {
    const params = new URLSearchParams({
      requirement_item_id: String(selectedRequirementId.value),
      batch_id: String(suite.batchId),
    })
    const result = await request<CaseSuiteExportResult>(`/api/v1/case-suites/export?${params.toString()}`, {
      method: 'POST',
    })
    downloadMarkdown(result.filename, result.content)
    notice.value = `已导出测试集「${result.suiteTitle}」：${result.caseCount} 条。`
  } catch (err) {
    error.value = normalizeError(err)
  } finally {
    exportingSuiteId.value = null
  }
}

async function confirmDeleteSuite() {
  if (!selectedRequirementId.value || !deletingSuite.value || deletingSuiteId.value !== null) {
    return
  }
  const suite = deletingSuite.value
  deletingSuiteId.value = suite.batchId
  error.value = ''
  notice.value = ''
  try {
    const params = new URLSearchParams({
      requirement_item_id: String(selectedRequirementId.value),
      batch_id: String(suite.batchId),
    })
    const result = await request<CaseSuiteDeleteResult>(`/api/v1/case-suites?${params.toString()}`, {
      method: 'DELETE',
    })
    notice.value = `已删除测试集「${result.suiteTitle}」：${result.deletedCaseCount} 条。`
    if (suite.pathGroups.some((group) => groupContainsExpandedCase(group)) || suite.cases.some((item) => item.id === expandedCaseId.value)) {
      expandedCaseId.value = null
    }
    deletingSuite.value = null
    await loadCases()
  } catch (err) {
    error.value = normalizeError(err)
  } finally {
    deletingSuiteId.value = null
  }
}

function groupContainsExpandedCase(group: AssetPathGroup): boolean {
  return (
    group.cases.some((item) => item.id === expandedCaseId.value) ||
    group.children.some((child) => groupContainsExpandedCase(child))
  )
}

function downloadMarkdown(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename || 'case-suite.md'
  anchor.click()
  URL.revokeObjectURL(url)
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) {
    selectedFileName.value = ''
    selectedFileContent.value = ''
    return
  }
  selectedFileName.value = file.name
  selectedFileContent.value = await file.text()
  notice.value = ''
  error.value = ''
  input.value = ''
}

function triggerFilePicker() {
  fileInputRef.value?.click()
}

const IMPORT_JOB_POLL_INTERVAL_MS = 2500
// 允许的连续轮询失败次数：容忍偶发网络抖动，超过才判定失败。不设“碰撞总时长”上限——
// 必须完整等待后台碰撞跑完（done/error），不做变相业务超时。
const IMPORT_JOB_MAX_POLL_ERRORS = 5

async function pollImportJob<T>(taskId: string): Promise<T> {
  let consecutiveErrors = 0
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, IMPORT_JOB_POLL_INTERVAL_MS))
    let status: ImportJobStatus<T>
    try {
      status = await request<ImportJobStatus<T>>(`/api/v1/imports/markdown/jobs/${taskId}`)
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        throw new Error('导入任务已丢失（服务可能刚重启），请重新导入。')
      }
      consecutiveErrors += 1
      if (consecutiveErrors > IMPORT_JOB_MAX_POLL_ERRORS) {
        throw err
      }
      continue
    }
    consecutiveErrors = 0
    if (status.status === 'done') {
      return (status.result ?? null) as T
    }
    if (status.status === 'error') {
      throw new Error(status.error || '导入碰撞处理失败。')
    }
  }
}

async function submitImport(confirmIndependent = false) {
  if (!selectedRequirementId.value || !selectedFileContent.value) {
    error.value = '请先选择二级需求和 Markdown 文件。'
    return
  }
  importing.value = true
  startImportProgress(confirmIndependent ? '正在导入独立测试集' : '正在导入 Case')
  error.value = ''
  notice.value = ''
  try {
    const result = await request<ImportMarkdownResult>('/api/v1/imports/markdown', {
      method: 'POST',
      body: {
        requirementItemId: selectedRequirementId.value,
        filename: selectedFileName.value || 'uploaded.md',
        content: selectedFileContent.value,
        confirmIndependent,
      } as unknown as BodyInit,
    })
    if (result.mode === 'collision_pending' && result.taskId) {
      // 碰撞判断在后台跑，弹窗保持阻塞式等待，前端轮询直到出结果，中途绝不放行入库。
      switchToCollisionProgress()
      const finalResult = await pollImportJob<ImportMarkdownResult>(result.taskId)
      handleImportResult(finalResult)
    } else {
      handleImportResult(result)
    }
  } catch (err) {
    error.value = normalizeError(err)
  } finally {
    importing.value = false
    stopImportProgress()
  }
}

function handleImportResult(result: ImportMarkdownResult) {
  if (result.mode === 'collision_review' && result.review) {
    review.value = result.review
    independentConfirm.value = null
    clearDecisionState()
    return
  }
  if (result.mode === 'independent_confirm_required') {
    independentConfirm.value = result
    review.value = null
    return
  }
  notice.value = result.message || `导入完成：${result.caseCount ?? 0} 条`
  review.value = null
  independentConfirm.value = null
  void loadCases()
}

function chooseAdd(item: ImportReviewItem) {
  reviewDecisions[item.incomingKey] = { incomingKey: item.incomingKey, action: 'add' }
}

function chooseSkip(item: ImportReviewItem) {
  reviewDecisions[item.incomingKey] = { incomingKey: item.incomingKey, action: 'skip' }
}

function chooseReplace(item: ImportReviewItem, candidate: ReviewCandidate) {
  reviewDecisions[item.incomingKey] = {
    incomingKey: item.incomingKey,
    oldCaseId: candidate.caseId,
    action: 'replace',
  }
}

function chooseDelete(item: DeleteReviewItem) {
  deleteDecisions[item.oldCaseId] = { oldCaseId: item.oldCaseId, action: 'delete' }
}

function chooseKeep(item: DeleteReviewItem) {
  deleteDecisions[item.oldCaseId] = { oldCaseId: item.oldCaseId, action: 'keep' }
}

async function commitReview() {
  if (!review.value || !selectedRequirementId.value || !allReviewHandled.value) {
    return
  }
  importing.value = true
  startImportProgress('正在写入导入结果')
  importProgress.stages = ['重算碰撞校验', '模型碰撞判断', '写入用例']
  importProgress.hint = '正在按你的处理结果重算碰撞并落库，请耐心等待；失败会明确报错。'
  error.value = ''
  try {
    const decisions = [
      ...Object.values(reviewDecisions),
      ...Object.values(deleteDecisions),
    ]
    const started = await request<{ mode: string; taskId: string; message: string }>(
      '/api/v1/imports/markdown/commit',
      {
        method: 'POST',
        body: {
          requirementItemId: selectedRequirementId.value,
          filename: selectedFileName.value || 'uploaded.md',
          content: selectedFileContent.value,
          decisions,
        } as unknown as BodyInit,
      },
    )
    // 落库会重算碰撞（同样调模型），一样走后台任务 + 轮询，避免网关超时。
    const result = await pollImportJob<{ message: string }>(started.taskId)
    notice.value = result.message
    review.value = null
    clearDecisionState()
    await loadCases()
  } catch (err) {
    error.value = normalizeError(err)
  } finally {
    importing.value = false
    stopImportProgress()
  }
}

function startImportProgress(title: string) {
  stopImportProgress()
  importProgress.title = title
  importProgress.filename = selectedFileName.value || 'uploaded.md'
  importProgress.startedAt = Date.now()
  importProgress.elapsedSeconds = 0
  importProgress.stageIndex = 0
  importProgress.stages = [...IMPORT_DEFAULT_STAGES]
  importProgress.hint = ''
  importProgress.visible = true
  importProgressTimer = window.setInterval(() => {
    updateImportProgress()
  }, 1000)
}

function switchToCollisionProgress() {
  importProgress.title = '正在进行二次导入碰撞判断'
  importProgress.stages = [...COLLISION_STAGES]
  importProgress.hint = COLLISION_HINT
}

function updateImportProgress() {
  if (!importProgress.startedAt) {
    return
  }
  const elapsedSeconds = Math.max(0, Math.floor((Date.now() - importProgress.startedAt) / 1000))
  importProgress.elapsedSeconds = elapsedSeconds
  importProgress.stageIndex = elapsedSeconds < 2 ? 0 : elapsedSeconds < 5 ? 1 : 2
}

function stopImportProgress() {
  if (importProgressTimer !== null) {
    window.clearInterval(importProgressTimer)
    importProgressTimer = null
  }
  importProgress.visible = false
  importProgress.startedAt = 0
  importProgress.elapsedSeconds = 0
  importProgress.stageIndex = 0
}

onUnmounted(() => {
  stopImportProgress()
})

function resetImportState() {
  notice.value = ''
  error.value = ''
  review.value = null
  independentConfirm.value = null
  clearDecisionState()
}

function clearDecisionState() {
  for (const key of Object.keys(reviewDecisions)) {
    delete reviewDecisions[key]
  }
  for (const key of Object.keys(deleteDecisions)) {
    delete deleteDecisions[Number(key)]
  }
}

function bestCandidate(item: ImportReviewItem): ReviewCandidate | null {
  const primary = item.candidates.find((candidate) => candidate.caseId === item.primaryOldCaseId)
  return primary ?? item.candidates[0] ?? null
}

function primaryCandidate(item: ImportReviewItem): ReviewCandidate | null {
  if (!item.primaryOldCaseId) {
    return null
  }
  return item.candidates.find((candidate) => candidate.caseId === item.primaryOldCaseId) ?? null
}

function choosePrimaryReplace(item: ImportReviewItem) {
  const candidate = primaryCandidate(item)
  if (candidate) {
    chooseReplace(item, candidate)
  }
}

function reviewTitle(caseData: ReviewCaseSnapshot | null | undefined) {
  return caseData?.rawTitle || '缺少完整标题'
}

function reviewPath(caseData: ReviewCaseSnapshot | null | undefined) {
  if (!caseData) {
    return ''
  }
  const nodes = (caseData.pathNodes || [])
    .map((node) => node.displayText || node.rawText || '')
    .filter(Boolean)
  if (nodes.length) {
    return nodes.join(' / ')
  }
  return caseData.path || ''
}

function reviewFieldValue(caseData: ReviewCaseSnapshot | null | undefined, key: string) {
  if (!caseData) {
    return ''
  }
  if (key === 'moduleName') {
    return caseData.moduleName || pathPart(caseData.path, 0)
  }
  if (key === 'productFeature') {
    return caseData.productFeature || pathPart(caseData.path, 1)
  }
  if (key === 'testFeature') {
    return caseData.testFeature || pathPart(caseData.path, 2)
  }
  if (key === 'rawTitle') {
    return caseData.rawTitle || '缺少完整标题'
  }
  if (key === 'stepsText') {
    return caseData.stepsText || caseData.steps || ''
  }
  if (key === 'expectedResult') {
    return caseData.expectedResult || caseData.expected || ''
  }
  return String((caseData as unknown as Record<string, unknown>)[key] ?? '')
}

function pathPart(path: string | undefined, index: number) {
  return String(path || '')
    .split('/')
    .map((item) => item.trim())
    .filter(Boolean)[index] || ''
}

function isReviewFieldChanged(left: ReviewCaseSnapshot, right: ReviewCaseSnapshot | null, key: string) {
  return normalizeReviewText(reviewFieldValue(left, key)) !== normalizeReviewText(reviewFieldValue(right, key))
}

function normalizeReviewText(value: string) {
  return String(value || '').replace(/\s+/g, '').toLowerCase()
}

function reviewScore(item: ImportReviewItem) {
  const candidate = primaryCandidate(item) ?? bestCandidate(item)
  return item.modelSimilarity ?? item.primarySimilarity ?? item.bestSimilarity ?? candidate?.similarity ?? 0
}

function decisionLabel(action: string) {
  const labels: Record<string, string> = {
    add: '作为新增',
    replace: '替代旧 case',
    skip: '本次不导入',
    delete: '删除旧 case',
    keep: '保留旧 case',
  }
  return labels[action] ?? action
}

function isDecisionSelected(key: string, action: string, oldCaseId?: number) {
  const decision = reviewDecisions[key]
  if (!decision || decision.action !== action) {
    return false
  }
  return oldCaseId ? decision.oldCaseId === oldCaseId : true
}

function isDeleteDecisionSelected(oldCaseId: number, action: string) {
  return deleteDecisions[oldCaseId]?.action === action
}

function caseDeleteImpact(item: CaseListItem) {
  const sameSuite = cases.value.filter(
    (candidate) => candidate.id !== item.id && candidate.batchId === item.batchId,
  )
  const itemPath = itemPathNodes(item)
  const pathLevels = itemPath.map((node, index) => ({
    label: node.label,
    name: node.displayText || '层级无',
    remove: !sameSuite.some((candidate) => samePathPrefix(itemPath, itemPathNodes(candidate), index + 1)),
  }))
  const levels = [
    { label: 'Case', name: item.rawTitle, remove: true },
    ...pathLevels.reverse(),
    { label: '测试集', name: item.suiteTitle || '测试集无', remove: sameSuite.length === 0 },
  ]
  return {
    removed: levels.filter((level) => level.remove),
    kept: levels.filter((level) => !level.remove),
  }
}

function samePathPrefix(left: CasePathNode[], right: CasePathNode[], length: number) {
  if (right.length < length) {
    return false
  }
  return left.slice(0, length).every((node, index) => {
    const other = right[index]
    return sameText(node.label, other?.label) && sameText(node.displayText, other?.displayText)
  })
}

function sameText(left: string | null | undefined, right: string | null | undefined) {
  return String(left || '') === String(right || '')
}

function openEditCase(item: CaseListItem) {
  editingCase.value = item
  editForm.moduleName = item.moduleName
  editForm.productFeature = item.productFeature
  editForm.testFeature = item.testFeature
  editForm.rawTitle = item.rawTitle
  editForm.preconditions = item.preconditions
  editForm.stepsText = item.stepsText
  editForm.expectedResult = item.expectedResult
}

function openCreateCase(suite: AssetSuiteGroup) {
  const options = pathOptionsForSuite(suite)
  if (!options.length) {
    error.value = '当前测试集没有可选择的层级，不能新增 Case。'
    return
  }
  creatingSuite.value = suite
  createForm.pathKey = options[0].key
  createForm.rawTitle = ''
  createForm.preconditions = ''
  createForm.stepsText = ''
  createForm.expectedResult = ''
  error.value = ''
  notice.value = ''
}

function toggleCaseDetail(caseId: number) {
  expandedCaseId.value = expandedCaseId.value === caseId ? null : caseId
}

function syncFeatureAfterModule() {
  if (!featureOptions.value.includes(editForm.productFeature)) {
    editForm.productFeature = featureOptions.value[0] || ''
  }
  syncTestAfterFeature()
}

function syncTestAfterFeature() {
  if (!testFeatureOptions.value.includes(editForm.testFeature)) {
    editForm.testFeature = testFeatureOptions.value[0] || ''
  }
}

async function saveEditCase() {
  if (!editingCase.value) {
    return
  }
  importing.value = true
  error.value = ''
  try {
    await request(`/api/v1/cases/${editingCase.value.id}`, {
      method: 'PATCH',
      body: {
        moduleName: editForm.moduleName,
        productFeature: editForm.productFeature,
        testFeature: editForm.testFeature,
        rawTitle: editForm.rawTitle,
        preconditions: editForm.preconditions,
        stepsText: editForm.stepsText,
        expectedResult: editForm.expectedResult,
      } as unknown as BodyInit,
    })
    notice.value = 'Case 已更新。'
    editingCase.value = null
    await loadCases()
  } catch (err) {
    error.value = normalizeError(err)
  } finally {
    importing.value = false
  }
}

async function saveCreateCase() {
  if (!selectedRequirementId.value || !creatingSuite.value || !selectedCreatePathOption.value) {
    return
  }
  if (!createForm.rawTitle.trim()) {
    error.value = '请填写测试标题正文。'
    return
  }
  creatingCase.value = true
  error.value = ''
  try {
    const batchId = creatingSuite.value.batchId
    const payload: CaseAssetCreate = {
      requirementItemId: selectedRequirementId.value,
      batchId,
      pathNodes: selectedCreatePathOption.value.nodes,
      rawTitle: createForm.rawTitle,
      preconditions: createForm.preconditions,
      stepsText: createForm.stepsText,
      expectedResult: createForm.expectedResult,
    }
    const result = await request<{ caseId: number; message: string }>('/api/v1/cases', {
      method: 'POST',
      body: payload as unknown as BodyInit,
    })
    notice.value = result.message || 'Case 已新增。'
    creatingSuite.value = null
    await loadCases()
    expandedSuiteIds.value = new Set([batchId])
    expandedCaseId.value = result.caseId
  } catch (err) {
    error.value = normalizeError(err)
  } finally {
    creatingCase.value = false
  }
}

async function confirmDeleteCase() {
  if (!deletingCase.value) {
    return
  }
  const deletedCaseId = deletingCase.value.id
  importing.value = true
  error.value = ''
  try {
    await request(`/api/v1/cases/${deletedCaseId}`, {
      method: 'DELETE',
    })
    notice.value = 'Case 已删除。'
    if (expandedCaseId.value === deletedCaseId) {
      expandedCaseId.value = null
    }
    deletingCase.value = null
    await loadCases()
  } catch (err) {
    error.value = normalizeError(err)
  } finally {
    importing.value = false
  }
}

function uniqueValues(values: Array<string | null | undefined>) {
  return [...new Set(values.map((item) => item || '').filter(Boolean))]
}

function normalizeError(err: unknown) {
  if (err instanceof ApiError) {
    if (Array.isArray((err.detail as { detail?: unknown })?.detail)) {
      return ((err.detail as { detail: string[] }).detail).join('\n')
    }
    return JSON.stringify(err.detail, null, 2)
  }
  return err instanceof Error ? err.message : String(err)
}
</script>

<template>
  <ImportProgressOverlay
    :open="importProgress.visible"
    variant="standard"
    :title="importProgress.title"
    :filename="importProgress.filename"
    :elapsed-seconds="importProgress.elapsedSeconds"
    :stage-index="importProgress.stageIndex"
    :stages="importProgress.stages"
    :hint="importProgress.hint"
  />

  <section class="asset-layout">
    <aside class="asset-sidebar">
      <h2>需求目录</h2>
      <div class="catalog-search-bar">
        <input v-model="catalogFilter" class="catalog-filter" type="text" placeholder="筛选目录 / 需求 / 版本" />
        <div class="catalog-filter-row">
          <select v-model="spaceFilter" aria-label="筛选空间">
            <option value="all">全部空间</option>
            <option v-for="space in spaces" :key="space.projectKey" :value="space.projectKey">
              {{ space.name }}
            </option>
          </select>
          <UserSearchSelect
            class="catalog-user-filter"
            :model-value="personFilter"
            :users="selectableUsers"
            all-label="全部测试人员"
            all-value="all"
            aria-label="筛选测试人员"
            @update:model-value="selectPersonFilter"
          />
        </div>
        <div class="catalog-filter-row">
          <select v-if="sprintOptions.length" v-model="sprintFilter" aria-label="筛选迭代">
            <option value="all">全部迭代排期</option>
            <option v-for="sp in sprintOptions" :key="sp.id" :value="sp.id">{{ sp.name }}</option>
          </select>
          <label class="catalog-toggle">
            <input v-model="testingOnly" type="checkbox" />
            <span>只看测试中</span>
          </label>
        </div>
      </div>
      <div class="catalog-scroll">
        <section v-if="groups.length" class="catalog-section">
          <div class="catalog-section-title">
            <strong>目录内需求</strong>
            <em>{{ filteredDirectoryGroups.length }}</em>
          </div>
          <template v-if="filteredDirectoryGroups.length">
            <section v-for="group in filteredDirectoryGroups" :key="group.id" class="asset-group">
              <button type="button" class="group-toggle" @click="toggleGroup(group.id)">
                <span class="caret">{{ isGroupOpen(group.id) ? '▾' : '▸' }}</span>
                <strong>{{ group.name }}</strong>
                <em>{{ group.items.length }}</em>
              </button>
              <template v-if="isGroupOpen(group.id)">
                <button
                  v-for="item in group.items"
                  :key="item.id"
                  type="button"
                  class="req-button"
                  :data-requirement-id="item.id"
                  :class="{ selected: item.id === selectedRequirementId }"
                  @click="selectRequirement(group.id, item.id)"
                >
                  <span v-if="item.version" class="req-version">{{ item.version }}</span>
                  <strong>{{ item.title }}</strong>
                </button>
              </template>
            </section>
          </template>
          <div v-else class="empty-state catalog-empty">当前筛选无目录内需求。</div>
        </section>

        <section v-if="ungroupedItems.length" class="catalog-section">
          <div class="catalog-section-title">
            <strong>未进入目录</strong>
            <em>{{ filteredUngroupedItems.length }}</em>
          </div>
          <section v-if="filteredUngroupedItems.length" class="asset-group asset-group-ungrouped">
            <button
              v-for="item in filteredUngroupedItems"
              :key="item.id"
              type="button"
              class="req-button"
              :data-requirement-id="item.id"
              :class="{ selected: item.id === selectedRequirementId }"
              @click="selectRequirement(UNGROUPED_GROUP_ID, item.id)"
            >
              <strong>{{ item.title }}</strong>
            </button>
          </section>
          <div v-else class="empty-state catalog-empty">当前筛选无未进入目录需求。</div>
        </section>

        <div v-if="!groups.length && !ungroupedItems.length" class="empty-state">暂无需求。</div>
      </div>
      <div v-if="catalogTotal" class="catalog-pagination">
        <span>{{ catalogPageStart }}-{{ catalogPageEnd }} / {{ catalogTotal }}</span>
        <div>
          <button type="button" :disabled="currentCatalogPage <= 1" @click="changeCatalogPage(-1)">上一页</button>
          <strong>{{ currentCatalogPage }} / {{ catalogPageCount }}</strong>
          <button type="button" :disabled="currentCatalogPage >= catalogPageCount" @click="changeCatalogPage(1)">下一页</button>
        </div>
      </div>
    </aside>

    <aside class="asset-import-column">
      <section class="asset-current-panel">
        <div class="asset-panel-title-row">
          <h2>当前二级需求</h2>
          <strong v-if="selectedRequirement">已选择</strong>
        </div>
        <article class="asset-current-card">
          <strong>{{ selectedGroup?.name ?? '未选择目录' }}</strong>
          <p>
            <span v-if="selectedRequirement?.version" class="req-version">{{ selectedRequirement.version }}</span>
            {{ selectedRequirement?.title ?? '请先选择二级需求' }}
          </p>
        </article>
      </section>
      <section class="import-panel">
        <h2>Markdown 文件</h2>
        <div class="file-picker-row">
          <input
            ref="fileInputRef"
            class="file-picker-input"
            type="file"
            accept=".md,text/markdown,text/plain"
            @change="onFileChange"
          />
          <button type="button" class="file-picker" @click="triggerFilePicker">
            <span>{{ selectedFileName || '选择文件' }}</span>
          </button>
        </div>
        <button
          type="button"
          class="import-submit"
          :disabled="importing || !selectedFileContent"
          @click="submitImport(false)"
        >
          {{ importing ? (importProgressText || '处理中...') : '上传并覆盖导入' }}
        </button>
      </section>

      <div v-if="notice" class="notice-state">{{ notice }}</div>
      <div v-if="error" class="error-state">{{ error }}</div>
    </aside>

    <main class="asset-main">
      <div class="asset-head">
        <h1>Case 列表</h1>
        <strong>{{ cases.length }} 条</strong>
      </div>
      <div class="asset-case-scroll">
        <div v-if="loading" class="empty-state">加载中...</div>
        <div v-else-if="!cases.length" class="empty-state">当前二级需求暂无 Case。</div>

        <div v-else class="asset-case-tree">
          <section
            v-for="suite in suiteGroups"
            :key="suite.batchId"
            class="asset-suite"
            :class="{ collapsed: !isSuiteOpen(suite.batchId) }"
          >
            <header>
              <button
                type="button"
                class="suite-toggle"
                :title="isSuiteOpen(suite.batchId) ? '收起测试集' : '展开测试集'"
                @click="toggleSuite(suite.batchId)"
              >
                {{ isSuiteOpen(suite.batchId) ? '▾' : '▸' }}
              </button>
              <div>
                <span>测试集</span>
                <strong>{{ suite.suiteTitle }}</strong>
                <p>来源文件：{{ suite.sourceName }}</p>
              </div>
              <div class="asset-suite-actions">
                <b>{{ suite.count }} 条</b>
                <button
                  type="button"
                  class="asset-add-button"
                  :disabled="exportingSuiteId !== null || deletingSuiteId !== null"
                  @click="openCreateCase(suite)"
                >
                  新增 Case
                </button>
                <button
                  type="button"
                  class="asset-export-button"
                  :disabled="exportingSuiteId !== null || deletingSuiteId !== null"
                  @click="exportSuiteMarkdown(suite)"
                >
                  {{ exportingSuiteId === suite.batchId ? '导出中...' : '导出 Markdown' }}
                </button>
                <button
                  type="button"
                  class="asset-delete-button"
                  :disabled="exportingSuiteId !== null || deletingSuiteId !== null"
                  @click="deletingSuite = suite"
                >
                  {{ deletingSuiteId === suite.batchId ? '删除中...' : '删除测试集' }}
                </button>
              </div>
            </header>

            <template v-if="isSuiteOpen(suite.batchId)">
              <CaseAssetPathBranch
                v-for="pathGroup in suite.pathGroups"
                :key="pathGroup.nodeId"
                :group="pathGroup"
              >
                <template #case="{ item }">
                  <CaseAssetCard
                    :item="item"
                    :expanded="expandedCaseId === item.id"
                    :group-name="selectedGroup?.name ?? '无'"
                    :requirement-title="selectedRequirement?.title ?? '无'"
                    @toggle="toggleCaseDetail"
                    @edit="openEditCase"
                    @delete="deletingCase = $event"
                  />
                </template>
              </CaseAssetPathBranch>
              <CaseAssetCard
                v-for="item in suite.cases"
                :key="item.id"
                :item="item"
                :expanded="expandedCaseId === item.id"
                :group-name="selectedGroup?.name ?? '无'"
                :requirement-title="selectedRequirement?.title ?? '无'"
                @toggle="toggleCaseDetail"
                @edit="openEditCase"
                @delete="deletingCase = $event"
              />
            </template>
          </section>
        </div>
      </div>
    </main>
  </section>

  <div v-if="independentConfirm" class="modal-mask">
    <section class="import-modal compact">
      <header>
        <h2>确认独立测试集</h2>
        <button type="button" @click="independentConfirm = null">关闭</button>
      </header>
      <p>{{ independentConfirm.message }}</p>
      <div class="modal-actions">
        <button type="button" @click="independentConfirm = null">取消</button>
        <button type="button" class="primary" :disabled="importing" @click="submitImport(true)">
          {{ importing ? (importProgressText || '处理中...') : '确认新增' }}
        </button>
      </div>
    </section>
  </div>

  <div v-if="review" class="modal-mask">
    <section class="import-modal import-review-modal">
      <header>
        <div>
          <h2>二次导入碰撞处理</h2>
          <p>
            完全一致的 case 已跳过；其余差异逐条确认；旧文件中消失的 case 需选择删除或保留。
          </p>
        </div>
        <button type="button" @click="review = null">关闭</button>
      </header>

      <div class="import-review-list">
        <section class="import-review-summary">
          <div><strong>{{ review.suiteTitle }}</strong></div>
          <div class="review-summary-grid">
            <span>总 {{ review.caseCount }}</span>
            <span>完全一致 {{ review.exactCount }}</span>
            <span>待处理 {{ review.reviewCount }}</span>
            <span>旧 case 待确认 {{ review.deleteCount }}</span>
            <span>主候选阈值 {{ review.primaryMatchThreshold }}</span>
          </div>
        </section>

        <div v-if="!review.reviewItems.length && !review.deleteItems.length" class="empty-state">
          没有需要人工处理的差异。
        </div>

        <article
          v-for="item in review.reviewItems"
          :key="item.incomingKey"
          class="import-review-item"
          :class="{ 'is-decided': Boolean(reviewDecisions[item.incomingKey]) }"
        >
          <div class="review-item-head">
            <div>
              <strong>{{ item.incoming.ordinal }}. {{ reviewTitle(item.incoming) }}</strong>
              <div class="meta">{{ reviewPath(item.incoming) }}</div>
            </div>
            <span class="review-score">{{ item.modelUsed ? '模型' : '规则' }} {{ reviewScore(item) }}%</span>
          </div>

          <div class="review-diff-toolbar">
            <div>
              <h4>{{ primaryCandidate(item) ? '锁定旧 case' : '未锁定旧 case' }}</h4>
              <div v-if="primaryCandidate(item)" class="review-candidate-list">
                <div>
                  #{{ primaryCandidate(item)?.ordinal }} {{ reviewTitle(primaryCandidate(item)) }} ·
                  相似度 {{ item.primarySimilarity || primaryCandidate(item)?.similarity || 0 }}%
                </div>
                <div>{{ primaryCandidate(item)?.changeHint || '存在差异，请确认处理方式' }}</div>
              </div>
              <div v-else class="diff-empty">
                没有达到 1:1 主候选阈值的旧 case，本条只能作为新增或本次不导入。
              </div>
              <div v-if="item.modelSummary" class="model-summary">
                {{ item.modelSummary }}
              </div>
            </div>
          </div>

          <div class="review-diff-grid">
            <div class="review-diff-column">
              <h4>新导入</h4>
              <div
                v-for="field in reviewFields"
                :key="`incoming-${item.incomingKey}-${field.key}`"
                class="diff-field"
                :class="{ 'is-changed': isReviewFieldChanged(item.incoming, primaryCandidate(item), field.key) }"
              >
                <div class="diff-label">
                  {{ field.label }}
                  <span v-if="isReviewFieldChanged(item.incoming, primaryCandidate(item), field.key)">变化</span>
                </div>
                <div class="diff-value">{{ reviewFieldValue(item.incoming, field.key) || '无' }}</div>
              </div>
            </div>
            <div class="review-diff-column old">
              <h4>{{ primaryCandidate(item) ? '候选旧 case' : '旧 case' }}</h4>
              <template v-if="primaryCandidate(item)">
                <div
                  v-for="field in reviewFields"
                  :key="`old-${item.incomingKey}-${field.key}`"
                  class="diff-field"
                  :class="{ 'is-changed': isReviewFieldChanged(item.incoming, primaryCandidate(item), field.key) }"
                >
                  <div class="diff-label">
                    {{ field.label }}
                    <span v-if="isReviewFieldChanged(item.incoming, primaryCandidate(item), field.key)">变化</span>
                  </div>
                  <div class="diff-value">{{ reviewFieldValue(primaryCandidate(item), field.key) || '无' }}</div>
                </div>
              </template>
              <div v-else class="diff-empty">
                没有足够相似的旧 case，本条可作为新增处理。
              </div>
            </div>
          </div>

          <div class="review-actions">
            <button
              type="button"
              class="primary-action"
              :disabled="!primaryCandidate(item)"
              :class="{ selected: isDecisionSelected(item.incomingKey, 'replace', primaryCandidate(item)?.caseId) }"
              @click="choosePrimaryReplace(item)"
            >
              替代旧 case
            </button>
            <button
              type="button"
              class="secondary-button"
              :class="{ selected: isDecisionSelected(item.incomingKey, 'add') }"
              @click="chooseAdd(item)"
            >
              作为新增
            </button>
            <button
              type="button"
              class="secondary-button"
              :class="{ selected: isDecisionSelected(item.incomingKey, 'skip') }"
              @click="chooseSkip(item)"
            >
              本次不导入
            </button>
            <span class="review-decision-state">
              {{ reviewDecisions[item.incomingKey] ? `已选择：${decisionLabel(reviewDecisions[item.incomingKey].action)}` : '未处理' }}
            </span>
          </div>
        </article>

        <article
          v-for="item in review.deleteItems"
          :key="item.deleteKey"
          class="import-review-item import-review-delete"
          :class="{ 'is-decided': replacedOldCaseIds.has(item.oldCaseId) || Boolean(deleteDecisions[item.oldCaseId]) }"
        >
          <div class="review-item-head">
            <div>
              <strong>#{{ item.oldCase.ordinal || '' }} {{ reviewTitle(item.oldCase) }}</strong>
              <div class="meta">{{ item.oldCase.path || '路径缺失' }}</div>
            </div>
            <span class="review-score delete-score">旧 case 待确认</span>
          </div>
          <div class="review-body-grid review-delete-grid">
            <div>
              <h4>旧 case 内容</h4>
              <p>{{ item.oldCase.preconditions || '' }}</p>
              <p>{{ item.oldCase.steps || item.oldCase.stepsText || '' }}</p>
              <p>{{ item.oldCase.expected || item.oldCase.expectedResult || '' }}</p>
            </div>
            <div>
              <h4>原因</h4>
              <p>{{ item.reason || '新导入文件中未出现该旧 case' }}</p>
              <p>如果确认这条 case 已经不属于当前打磨集，选择删除；如果仍需保留，选择保留。</p>
            </div>
          </div>
          <div class="review-actions">
            <button
              type="button"
              class="danger-button"
              :disabled="replacedOldCaseIds.has(item.oldCaseId)"
              :class="{ selected: isDeleteDecisionSelected(item.oldCaseId, 'delete') }"
              @click="chooseDelete(item)"
            >
              删除旧 case
            </button>
            <button
              type="button"
              class="secondary-button"
              :disabled="replacedOldCaseIds.has(item.oldCaseId)"
              :class="{ selected: isDeleteDecisionSelected(item.oldCaseId, 'keep') }"
              @click="chooseKeep(item)"
            >
              保留旧 case
            </button>
            <span class="review-decision-state">
              {{
                replacedOldCaseIds.has(item.oldCaseId)
                  ? '已由替代动作处理'
                  : deleteDecisions[item.oldCaseId]
                    ? `已选择：${decisionLabel(deleteDecisions[item.oldCaseId].action)}`
                    : '未处理'
              }}
            </span>
          </div>
        </article>
      </div>

      <footer>
        <span>剩余未处理 {{ pendingReviewCount }} 项</span>
        <div>
          <button type="button" @click="review = null">取消</button>
          <button type="button" class="primary" :disabled="!allReviewHandled || importing" @click="commitReview">
            {{ importing ? (importProgressText || '落库中...') : '确认落库' }}
          </button>
        </div>
      </footer>
    </section>
  </div>

  <div v-if="creatingSuite" class="modal-mask">
    <section class="import-modal asset-edit-modal">
      <header>
        <div>
          <h2>新增 Case</h2>
          <p>所属测试集：{{ creatingSuite.suiteTitle }}；只填写冒号后的正文，导出时会自动保留字段前缀。</p>
        </div>
        <button type="button" @click="creatingSuite = null">关闭</button>
      </header>

      <div class="asset-edit-form">
        <label class="wide">
          <span>层级</span>
          <select v-model="createForm.pathKey">
            <option
              v-for="option in createPathOptions"
              :key="option.key"
              :value="option.key"
            >
              {{ option.label }}
            </option>
          </select>
        </label>
        <label class="wide prefixed-field">
          <span>测试标题：</span>
          <input v-model="createForm.rawTitle" placeholder="填写标题正文" />
        </label>
        <label class="wide prefixed-field">
          <span>前置条件：</span>
          <textarea v-model="createForm.preconditions" rows="4" placeholder="填写前置条件正文" />
        </label>
        <label class="wide prefixed-field">
          <span>操作步骤：</span>
          <textarea v-model="createForm.stepsText" rows="6" placeholder="填写操作步骤正文" />
        </label>
        <label class="wide prefixed-field">
          <span>预期结果：</span>
          <textarea v-model="createForm.expectedResult" rows="4" placeholder="填写预期结果正文" />
        </label>
      </div>

      <footer>
        <span>新增后会进入所选层级末尾，并标记为变更待确认。</span>
        <div>
          <button type="button" @click="creatingSuite = null">取消</button>
          <button type="button" class="primary" :disabled="creatingCase" @click="saveCreateCase">
            {{ creatingCase ? '新增中...' : '保存新增' }}
          </button>
        </div>
      </footer>
    </section>
  </div>

  <div v-if="editingCase" class="modal-mask">
    <section class="import-modal asset-edit-modal">
      <header>
          <div>
            <h2>编辑 Case 资产</h2>
          <p>可调整层级路径和四段核心内容，保存后全局同步。</p>
          </div>
        <button type="button" @click="editingCase = null">关闭</button>
      </header>

      <div class="asset-edit-form">
        <label>
          <span>模块</span>
          <select v-model="editForm.moduleName" @change="syncFeatureAfterModule">
            <option v-for="item in moduleOptions" :key="item" :value="item">{{ item }}</option>
          </select>
        </label>
        <label>
          <span>功能点</span>
          <select v-model="editForm.productFeature" @change="syncTestAfterFeature">
            <option v-for="item in featureOptions" :key="item" :value="item">{{ item }}</option>
          </select>
        </label>
        <label>
          <span>测试功能点</span>
          <select v-model="editForm.testFeature">
            <option v-for="item in testFeatureOptions" :key="item" :value="item">{{ item }}</option>
          </select>
        </label>
        <label class="wide">
          <span>测试标题</span>
          <input v-model="editForm.rawTitle" />
        </label>
        <label class="wide">
          <span>前置条件</span>
          <textarea v-model="editForm.preconditions" rows="4" />
        </label>
        <label class="wide">
          <span>操作步骤</span>
          <textarea v-model="editForm.stepsText" rows="6" />
        </label>
        <label class="wide">
          <span>预期结果</span>
          <textarea v-model="editForm.expectedResult" rows="4" />
        </label>
      </div>

      <footer>
        <span>保存后会重置为未执行，并清空旧报告与失败分析。</span>
        <div>
          <button type="button" @click="editingCase = null">取消</button>
          <button type="button" class="primary" :disabled="importing" @click="saveEditCase">
            保存并同步
          </button>
        </div>
      </footer>
    </section>
  </div>

  <div v-if="deletingSuite" class="modal-mask">
    <section class="import-modal delete-confirm-modal">
      <header>
        <div>
          <h2>确认删除测试集</h2>
          <p>删除后会同步影响 Case 资产、首页右侧列表、状态脑图和执行状态。</p>
        </div>
        <button type="button" @click="deletingSuite = null">关闭</button>
      </header>
      <div class="case-delete-summary">
        <div class="delete-warning-card">
          <h3>{{ deletingSuite.suiteTitle }}</h3>
          <div class="asset-meta-line">来源文件：{{ deletingSuite.sourceName }}</div>
          <div class="asset-meta-line">Case 数量：{{ deletingSuite.count }} 条</div>
          <div class="asset-meta-line">
            所属需求：{{ selectedGroup?.name ?? '无' }} / {{ selectedRequirement?.title ?? '无' }}
          </div>
          <div v-if="deletingSuite.runningCount" class="asset-meta-line">
            正在执行：{{ deletingSuite.runningCount }} 条
          </div>
        </div>
        <div class="delete-note">
          这是全局删除：会删除该测试集下所有 Case、本地执行状态、报告关联、失败分析和修复草稿；不会删除二级需求、一级目录或 functionMap。
        </div>
        <div v-if="deletingSuite.runningCount" class="delete-note">
          该测试集中有 Case 正在外部执行。本操作不会自动停止外部执行，后续迟到回调会按已删除资产忽略。
        </div>
      </div>
      <footer>
        <span></span>
        <div>
          <button type="button" @click="deletingSuite = null">取消</button>
          <button type="button" class="primary danger" :disabled="deletingSuiteId !== null" @click="confirmDeleteSuite">
            {{ deletingSuiteId !== null ? '删除中...' : '确认删除' }}
          </button>
        </div>
      </footer>
    </section>
  </div>

  <div v-if="deletingCase" class="modal-mask">
    <section class="import-modal delete-confirm-modal">
      <header>
        <div>
          <h2>确认删除 Case</h2>
          <p>删除后会清理空链路，并同步影响首页与脑图。</p>
        </div>
        <button type="button" @click="deletingCase = null">关闭</button>
      </header>
      <div class="case-delete-summary">
        <div class="delete-warning-card">
          <h3>{{ deletingCase.rawTitle }}</h3>
          <div class="asset-meta-line">
            路径：{{ deletingCase.suiteTitle || '测试集无' }} / {{ deletingCase.path || '层级无' }}
          </div>
          <div class="asset-meta-line">
            所属需求：{{ selectedGroup?.name ?? '无' }} / {{ selectedRequirement?.title ?? '无' }}
          </div>
        </div>
        <div class="delete-impact-grid">
          <section>
            <h4>会被删除</h4>
            <div
              v-for="level in caseDeleteImpact(deletingCase).removed"
              :key="`remove-${level.label}`"
              class="delete-impact-row is-remove"
            >
              <span>{{ level.label }}</span>
              <strong>{{ level.name }}</strong>
            </div>
          </section>
          <section>
            <h4>会保留</h4>
            <template v-if="caseDeleteImpact(deletingCase).kept.length">
              <div
                v-for="level in caseDeleteImpact(deletingCase).kept"
                :key="`keep-${level.label}`"
                class="delete-impact-row"
              >
                <span>{{ level.label }}</span>
                <strong>{{ level.name }}</strong>
              </div>
            </template>
            <div v-else class="muted">没有需要保留的上级链路。</div>
          </section>
        </div>
        <div class="delete-note">
          这是全局删除：Case 资产、首页右侧列表、状态脑图和执行状态都会同步移除。
        </div>
      </div>
      <footer>
        <span></span>
        <div>
          <button type="button" @click="deletingCase = null">取消</button>
          <button type="button" class="primary danger" :disabled="importing" @click="confirmDeleteCase">
            {{ importing ? '删除中...' : '确认删除' }}
          </button>
        </div>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.catalog-search-bar {
  display: grid;
  gap: 8px;
}

.catalog-filter-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 8px;
}

.catalog-search-bar select,
.catalog-toggle {
  width: 100%;
  height: 34px;
  min-height: 34px;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #172033;
  padding: 0 9px;
  font-size: 13px;
  font-weight: 700;
}

.catalog-search-bar select,
.catalog-user-filter {
  min-width: 0;
}

.catalog-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  justify-content: flex-start;
}

.catalog-toggle input {
  margin: 0;
}

.catalog-filter-row > .catalog-toggle:only-child {
  grid-column: 1 / -1;
}

.catalog-scroll {
  flex: 1 1 auto;
  min-height: 0;
  max-height: none;
  overflow-y: auto;
  display: grid;
  gap: 12px;
  align-content: start;
}

.catalog-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border-top: 1px solid #e2e8f0;
  padding-top: 8px;
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
}

.catalog-pagination div {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.catalog-pagination button {
  min-height: 28px;
  padding: 0 8px;
}

.catalog-empty {
  padding: 10px;
  font-size: 12px;
}

.catalog-section {
  display: grid;
  gap: 6px;
}

.catalog-section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #475569;
  font-size: 12px;
  font-weight: 800;
}

.catalog-section-title em {
  color: #64748b;
  font-size: 11px;
  font-style: normal;
}

.asset-group {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 8px 10px;
  border: 1px solid #d7dfec;
  border-left: 5px solid #0f766e;
  border-radius: 6px;
  background: #fff;
}

.asset-group-ungrouped {
  border-left-color: #64748b;
  background: #fbfdff;
}

.group-toggle {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  border: 0;
  background: transparent;
  color: #1e293b;
  margin: 0;
  min-height: 0;
  padding: 2px 0;
  font-size: 13px;
  font-weight: 800;
  cursor: pointer;
  text-align: left;
}

.group-toggle .caret {
  flex: 0 0 auto;
  min-width: 0;
  background: transparent;
  border-radius: 0;
  padding: 0;
  color: #64748b;
  font-size: 11px;
}

.group-toggle strong {
  flex: 1 1 auto;
  min-width: 0;
  overflow: visible;
  text-overflow: clip;
  white-space: normal;
  word-break: break-word;
  line-height: 1.35;
}

.group-toggle em {
  flex: 0 0 auto;
  color: #64748b;
  font-size: 11px;
  font-style: normal;
  font-weight: 700;
}

.req-button {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  margin: 0;
  min-height: 0;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #f8fafc;
  color: #334155;
  padding: 7px 9px 7px 22px;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.req-button.selected {
  border-color: #2563eb;
  background: #eff6ff;
}

.req-button strong {
  flex: 1 1 auto;
  min-width: 0;
  overflow: visible;
  text-overflow: clip;
  white-space: normal;
  word-break: break-word;
  line-height: 1.35;
  font-weight: 700;
}

.req-version {
  flex: 0 0 auto;
  border-radius: 4px;
  background: #eef2f7;
  color: #475569;
  border: 1px solid #dbe3ee;
  font-size: 11px;
  font-weight: 700;
  padding: 1px 6px;
}

</style>
