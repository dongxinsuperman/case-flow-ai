<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import BugFieldEditor from '../components/BugFieldEditor.vue'
import CaseMindMap from '../components/home/CaseMindMap.vue'
import CaseOperationPanel from '../components/home/CaseOperationPanel.vue'
import { useCaseWorkbenchStore } from '../stores/caseWorkbench'
import { useFunctionMapAssetsStore } from '../stores/functionMapAssets'
import type { FunctionMapAssetListItem, FunctionMapTarget } from '../types/functionMap'
import type {
  AIPhoneDevice,
  BugField,
  CaseListItem,
  CasePlatformResult,
  ExecutionStatus,
  ExecutionTarget,
  RepairDraft,
  RequirementTask,
  SubmittedBug,
} from '../types/case'
import { repairProcessDetails, repairProcessHead, repairProcessKey, repairProcessSummary } from '../utils/repairProcess'
import { newExecutionRequestGroupId } from '../utils/executionRequestGroup'
import { filterAiPhoneDevices, persistAiPhoneDeviceFilter, readAiPhoneDeviceFilter } from '../utils/deviceFilter'

const store = useCaseWorkbenchStore()
const router = useRouter()
const fmStore = useFunctionMapAssetsStore()
const FM_TARGET_LABEL: Record<FunctionMapTarget, string> = { app: 'App', web: 'Web', api: 'API' }
let pollingTimer: number | undefined

// 自动发现开关（检查点 4，按二级需求，默认开；本轮只持久化，不接执行）
// 检查点 6（模型自动发现）已暂缓，此开关无生效对象 → 先隐藏，避免“拨了没反应”的死开关。
// DB 列 requirement_items.auto_discovery_enabled 保留休眠，cp6 重启时把 AUTO_DISCOVERY_UI 改回 true 即可。
const AUTO_DISCOVERY_UI = false
async function onToggleAutoDiscovery(item: RequirementTask, ev: Event) {
  const enabled = (ev.target as HTMLInputElement).checked
  autoDiscoveryError.value = ''
  try {
    await store.setAutoDiscovery(item.requirementItemId, enabled)
  } catch (error) {
    autoDiscoveryError.value = error instanceof Error ? error.message : '自动发现开关保存失败'
  }
}

// 首页脑图二级需求块的 Function Map 只读浮窗
const fmPopup = ref<{ itemId: number; title: string; groupName: string } | null>(null)
const fmInherited = ref<FunctionMapAssetListItem[]>([])
const fmOwn = ref<FunctionMapAssetListItem[]>([])
const fmLoading = ref(false)
const fmError = ref('')
const fmView = ref<{ title: string; content: string } | null>(null)
const autoDiscoveryError = ref('')

async function openRequirementFunctionMap(item: RequirementTask) {
  fmPopup.value = {
    itemId: item.requirementItemId,
    title: item.requirementItemTitle,
    groupName: item.groupName || '未进入目录',
  }
  fmInherited.value = []
  fmOwn.value = []
  fmView.value = null
  fmError.value = ''
  fmLoading.value = true
  try {
    const [own, inherited] = await Promise.all([
      fmStore.listItemMounts(item.requirementItemId),
      item.groupId ? fmStore.listGroupMounts(item.groupId) : Promise.resolve([]),
    ])
    fmOwn.value = own
    fmInherited.value = inherited
  } catch (error) {
    fmError.value = error instanceof Error ? error.message : 'Function Map 加载失败'
  } finally {
    fmLoading.value = false
  }
}

function closeRequirementFunctionMap() {
  fmPopup.value = null
  fmView.value = null
  fmError.value = ''
}

async function viewFmContent(assetId: number, title: string) {
  fmView.value = { title, content: '加载中…' }
  try {
    const full = await fmStore.getAsset(assetId)
    if (fmView.value) {
      fmView.value = { title, content: full.content }
    }
  } catch (error) {
    fmView.value = null
    fmError.value = error instanceof Error ? error.message : 'Function Map 正文加载失败'
  }
}

function goRequirementMountManager(itemId: number) {
  void router.push({ name: 'function-maps', query: { itemId: String(itemId) } })
}
let clockTimer: number | undefined
const nowMs = ref(Date.now())
const contextTriggerRef = ref<HTMLElement | null>(null)
const workspaceRef = ref<HTMLElement | null>(null)
const contextPinned = ref(false)
const caseScrollTargetId = ref<number | null>(null)
const caseScrollRequestKey = ref(0)
// 顶部固定浮层（.home-sticky-context）约 48px 高，工作区滚入时给它让出空间，避免压住面板标题行。
// 与 global.css 的 html { scroll-padding-top } 保持同一数值。
const WORKSPACE_TOP_GAP = 56
const STICKY_CONTEXT_TRIGGER = 80

const devicePicker = reactive({
  open: false,
  loading: false,
  error: '',
  devices: [] as AIPhoneDevice[],
  caseItem: null as CaseListItem | null,
  target: 'app' as 'app' | 'web',
})

// 多端“查看报告”浮层：列出当前 case 各端的报告链接。
const reportChooser = reactive({
  open: false,
  caseTitle: '',
  results: [] as CasePlatformResult[],
})

const executionQueue = reactive({
  open: false,
  loading: false,
  error: '',
  devices: [] as AIPhoneDevice[],
  selectedAliases: [] as string[],
  deviceFilter: readAiPhoneDeviceFilter(),
  webDevices: [] as AIPhoneDevice[],
  webSelectedAliases: [] as string[],
  items: [] as CaseListItem[],
  draggingCaseId: null as number | null,
  retryMax: 0,
  cacheMode: 'off' as 'off' | 'v1' | 'v2' | 'v3',
})

const repairDialog = reactive({
  open: false,
  loading: false,
  error: '',
  caseIds: [] as number[],
  items: [] as RepairDraft[],
  applyingDraftId: null as number | null,
  requestId: 0,
})

const editDialog = reactive({
  open: false,
  saving: false,
  error: '',
  caseId: null as number | null,
  rawTitle: '',
  preconditions: '',
  stepsText: '',
  expectedResult: '',
})

const bugDialog = reactive({
  open: false,
  loading: false,
  submitting: false,
  imageUploading: false,
  imageDragOver: false,
  error: '',
  caseId: null as number | null,
  space: '' as string | null,
  title: '',
  description: '',
  fields: [] as BugField[],
  hasImage: false,
  keyImage: '' as string | null,
  keyImages: [] as { platform: string; image: string }[],
  // 本次提交要排除的端证据图（按 image 路径）；可叉掉/恢复，支持多次提交给不同端人时各带不同图。
  excludedImages: [] as string[],
  resultUrl: '' as string | null,
  submittedBugs: [] as SubmittedBug[],
})
const bugImageInputRef = ref<HTMLInputElement | null>(null)
const bugImageDropzoneRef = ref<HTMLElement | null>(null)

function mergedBugImages(
  ...groups: Array<Array<{ platform: string; image: string }> | null | undefined>
): { platform: string; image: string }[] {
  const seen = new Set<string>()
  const images: { platform: string; image: string }[] = []
  for (const group of groups) {
    for (const item of group ?? []) {
      const image = item.image?.trim()
      if (!image || seen.has(image)) continue
      seen.add(image)
      images.push({ platform: item.platform || '', image })
    }
  }
  return images
}

function bugDraftImages(draft: { keyImages?: { platform: string; image: string }[]; keyImage?: string | null }) {
  return mergedBugImages(
    draft.keyImages ?? [],
    draft.keyImage ? [{ platform: '诊断截图', image: draft.keyImage }] : [],
  )
}

function isKeyImageExcluded(image: string): boolean {
  return bugDialog.excludedImages.includes(image)
}

function toggleKeyImage(image: string): void {
  const idx = bugDialog.excludedImages.indexOf(image)
  if (idx >= 0) {
    bugDialog.excludedImages.splice(idx, 1)
  } else {
    bugDialog.excludedImages.push(image)
  }
}

function submitAnotherBug(): void {
  bugDialog.resultUrl = ''
  bugDialog.excludedImages = [] // 下一次提交默认重新带全部，按需再叉
}

function selectBugImages(): void {
  bugImageInputRef.value?.click()
}

function focusBugImageDropzone(): void {
  bugImageDropzoneRef.value?.focus()
}

async function uploadBugImages(files: FileList | File[]): Promise<void> {
  const imageFiles = Array.from(files).filter((file) => file.type.startsWith('image/'))
  if (!imageFiles.length) return
  bugDialog.imageUploading = true
  bugDialog.error = ''
  try {
    const uploaded = await store.uploadBugImages(imageFiles)
    bugDialog.keyImages = mergedBugImages(bugDialog.keyImages, uploaded)
    bugDialog.hasImage = bugDialog.keyImages.length > 0
  } catch (error) {
    bugDialog.error = error instanceof Error ? error.message : '上传图片失败'
  } finally {
    bugDialog.imageUploading = false
    bugDialog.imageDragOver = false
  }
}

async function handleBugImageFileChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  if (input.files) {
    await uploadBugImages(input.files)
  }
  input.value = ''
}

function handleBugImagePaste(event: ClipboardEvent): void {
  const imageFiles = Array.from(event.clipboardData?.files ?? []).filter((file) => file.type.startsWith('image/'))
  if (!imageFiles.length) return
  event.preventDefault()
  void uploadBugImages(imageFiles)
}

function handleBugImageDragOver(): void {
  bugDialog.imageDragOver = true
}

function handleBugImageDragLeave(event: DragEvent): void {
  const current = event.currentTarget as HTMLElement | null
  if (current && event.relatedTarget instanceof Node && current.contains(event.relatedTarget)) {
    return
  }
  bugDialog.imageDragOver = false
}

function handleBugImageDrop(event: DragEvent): void {
  bugDialog.imageDragOver = false
  if (event.dataTransfer?.files) {
    void uploadBugImages(event.dataTransfer.files)
  }
}

function setBugFieldSelected(field: BugField, value: string | string[] | null): void {
  field.selected = value
}

async function openBugDialog(caseId: number) {
  bugDialog.open = true
  bugDialog.loading = true
  bugDialog.imageUploading = false
  bugDialog.imageDragOver = false
  bugDialog.error = ''
  bugDialog.resultUrl = ''
  bugDialog.caseId = caseId
  bugDialog.fields = []
  bugDialog.submittedBugs = []
  try {
    const draft = await store.getBugDraft(caseId)
    bugDialog.space = draft.space ?? ''
    bugDialog.title = draft.title
    bugDialog.description = draft.description
    bugDialog.fields = draft.fields
    bugDialog.hasImage = draft.hasDiagnosisImage
    bugDialog.keyImage = draft.keyImage ?? ''
    bugDialog.keyImages = bugDraftImages(draft)
    bugDialog.excludedImages = []
    // 多次提交：始终展示表单（不因已提交过而短路），已提交的列在“已提交”区。
    bugDialog.submittedBugs = draft.submittedBugs ?? []
  } catch (error) {
    bugDialog.error = error instanceof Error ? error.message : '生成 bug 草稿失败'
  } finally {
    bugDialog.loading = false
  }
}

function closeBugDialog() {
  bugDialog.open = false
}

function toggleBugTag(field: BugField, optionName: string) {
  const current = Array.isArray(field.selected) ? [...field.selected] : []
  const idx = current.indexOf(optionName)
  if (idx >= 0) current.splice(idx, 1)
  else current.push(optionName)
  field.selected = current
}

function isBugTagSelected(field: BugField, optionName: string): boolean {
  return Array.isArray(field.selected) && field.selected.includes(optionName)
}

async function submitBugDialog() {
  if (bugDialog.caseId == null) return
  if (!bugDialog.title.trim()) {
    bugDialog.error = '标题不能为空'
    return
  }
  bugDialog.submitting = true
  bugDialog.error = ''
  const selectedKeyImages = bugDialog.keyImages.filter((img) => !bugDialog.excludedImages.includes(img.image))
  try {
    const result = await store.submitBug(bugDialog.caseId, {
      title: bugDialog.title,
      description: bugDialog.description,
      fields: bugDialog.fields,
      // 只带未被叉掉的端证据图；全叉掉则不带（[]）。
      keyImages: selectedKeyImages,
    })
    bugDialog.hasImage = selectedKeyImages.length > 0
    bugDialog.resultUrl = result.bugUrl
    // 追加到“已提交”列表（支持再提一条）。
    bugDialog.submittedBugs = [
      ...bugDialog.submittedBugs,
      { url: result.bugUrl, id: String(result.bugId) },
    ]
    // 实时联动：批量诊断弹窗里对应那条立刻翻成“已提交”，关闭后右侧卡片也已随 loadCases 刷新。
    const repairItem = repairDialog.items.find((entry) => entry.caseId === bugDialog.caseId)
    if (repairItem) {
      repairItem.bugUrl = result.bugUrl
    }
  } catch (error) {
    bugDialog.error = error instanceof Error ? error.message : '提交 bug 失败'
  } finally {
    bugDialog.submitting = false
  }
}

const failureTypeLabels: Record<string, string> = {
  assertion_failed: '断言失败',
  断言失败: '断言失败',
  business_failure: '业务失败',
  业务失败: '业务失败',
  execution_failed: '执行失败',
  environment_failure: '执行失败',
  执行失败: '执行失败',
  case_step_failure: '步骤问题',
  步骤问题: '步骤问题',
  unknown_failure: '不确定',
  missing_report: '不确定',
  report_unreadable: '不确定',
  model_unavailable: '不确定',
  model_failed: '不确定',
  模型不可用: '不确定',
  模型分析失败: '不确定',
  缺少报告: '不确定',
  报告不可读: '不确定',
  不可修复: '不确定',
}

const statusLabels: Record<ExecutionStatus, string> = {
  not_run: '未执行',
  running: '执行中',
  passed: '通过',
  failed: '失败',
}

onMounted(() => {
  void store.loadInitial()
  updateScrollY()
  window.addEventListener('scroll', updateScrollY, { passive: true })
  clockTimer = window.setInterval(() => {
    nowMs.value = Date.now()
  }, 1000)
  pollingTimer = window.setInterval(() => {
    const modalOpen = devicePicker.open || executionQueue.open || repairDialog.open || editDialog.open || bugDialog.open
    if (modalOpen) {
      return
    }
    // 后台诊断/预填进行中（失败+有报告但草稿未就绪）时也刷新，好让波浪自动停。
    const pendingPrep = store.cases.some(
      (c) =>
        c.executionStatus === 'failed'
        && !!c.reportUrl
        && (!c.diagnosisReady || !c.bugDraftReady),
    )
    if (store.summary.running > 0 || pendingPrep) {
      void store.loadHome()
    }
  }, 5000)
})

onUnmounted(() => {
  window.removeEventListener('scroll', updateScrollY)
  if (pollingTimer) {
    window.clearInterval(pollingTimer)
  }
  if (clockTimer) {
    window.clearInterval(clockTimer)
  }
})

const queueAppItems = computed(() => executionQueue.items.filter((item) => item.executionTarget === 'app'))
const queueWebItems = computed(() => executionQueue.items.filter((item) => item.executionTarget === 'web'))
const queueApiItems = computed(() => executionQueue.items.filter((item) => item.executionTarget === 'api'))
const queueMixedItems = computed(() => executionQueue.items.filter((item) => item.executionTarget === 'mixed'))
const queueManualItems = computed(() =>
  executionQueue.items.filter((item) => item.executionTarget === 'manual' || item.executionTarget === 'unknown'),
)
// 只保留通过关键字筛选的 AI Phone 设备；被过滤掉的不渲染、不进池、不参与调度（等于未勾选）。
const queueVisibleDevices = computed(() => filterAiPhoneDevices(executionQueue.devices, executionQueue.deviceFilter))
const queueDeviceGroups = computed(() => groupDevicesByPlatform(queueVisibleDevices.value))
const queueSelectedVisibleCount = computed(
  () => queueVisibleDevices.value.filter((device) => executionQueue.selectedAliases.includes(deviceAlias(device))).length,
)
function onQueueDeviceFilterInput(value: string) {
  executionQueue.deviceFilter = value
  persistAiPhoneDeviceFilter(value)
}
const queueWebDeviceGroups = computed(() => groupDevicesByPlatform(executionQueue.webDevices))
const pickerDeviceGroups = computed(() => groupDevicesByPlatform(devicePicker.devices))
const currentUser = computed(() => store.dashboard?.user ?? store.users.find((item) => item.id === store.currentUserId))
const stickyContextVisible = computed(
  () =>
    contextPinned.value
    && Boolean(store.selectedRequirement)
    && !reportChooser.open
    && !devicePicker.open
    && !executionQueue.open
    && !repairDialog.open
    && !editDialog.open
    && !bugDialog.open,
)
const executionQueueSummary = computed(() => {
  const parts = [`共 ${executionQueue.items.length} 条`]
  if (queueAppItems.value.length) {
    parts.push(`AI Phone ${queueAppItems.value.length}`)
  }
  if (queueWebItems.value.length) {
    parts.push(`AI Web ${queueWebItems.value.length}`)
  }
  if (queueApiItems.value.length) {
    parts.push(`AI API ${queueApiItems.value.length}`)
  }
  if (queueMixedItems.value.length) {
    parts.push(`AI Hybrid ${queueMixedItems.value.length}`)
  }
  if (queueManualItems.value.length) {
    parts.push(`人工 ${queueManualItems.value.length}`)
  }
  return parts.join('，')
})

function updateScrollY() {
  contextPinned.value = window.scrollY > STICKY_CONTEXT_TRIGGER
}

function scrollWorkspaceIntoView() {
  const target = workspaceRef.value
  if (!target) {
    return
  }

  const rect = target.getBoundingClientRect()
  if (rect.top <= WORKSPACE_TOP_GAP) {
    return
  }

  const top = rect.top + window.scrollY - WORKSPACE_TOP_GAP
  window.scrollTo({
    top: Math.max(0, top),
    behavior: 'smooth',
  })
}

function updateStatus(caseId: number, status: ExecutionStatus) {
  void store.updateCaseStatus(caseId, status)
}

function updateTarget(caseId: number, target: ExecutionTarget) {
  void store.updateCaseTarget(caseId, target)
}

function setFilter(filter: ExecutionStatus | 'attention' | 'all') {
  store.setStatusFilter(filter)
}

async function selectRequirementAndEnterMap(requirementItemId: number) {
  store.selectRequirement(requirementItemId)
  await nextTick()
  window.requestAnimationFrame(() => {
    scrollWorkspaceIntoView()
  })
}

function requestCaseListScroll(caseId: number) {
  caseScrollTargetId.value = caseId
  caseScrollRequestKey.value += 1
}

function selectCaseFromPanel(caseId: number) {
  store.selectCase(caseId)
}

async function selectCaseFromMindMap(caseId: number) {
  store.selectCase(caseId)
  requestCaseListScroll(caseId)
  // 点脑图节点时把工作区上滑到固定浮层下方（标题行落在浮层正下方、不被压），同时右侧 case 置顶。
  await nextTick()
  window.requestAnimationFrame(() => scrollWorkspaceIntoView())
}

async function executeCase(caseId: number) {
  const item = store.cases.find((candidate) => candidate.id === caseId)
  if (!item) {
    return
  }
  if (item.executionTarget === 'app') {
    void openDevicePicker(item, 'app')
    return
  }
  if (item.executionTarget === 'web') {
    void openDevicePicker(item, 'web')
    return
  }
  if (item.executionTarget === 'api') {
    try {
      await store.submitAIAPICases(
        [caseId],
        `Case Flow API 单条执行 ${item.displayNo || item.ordinal}. ${item.rawTitle}`,
      )
    } catch (error) {
      store.error = error instanceof Error ? error.message : String(error)
    }
    return
  }
  if (item.executionTarget === 'mixed') {
    try {
      await store.submitHybridCases(
        [caseId],
        `Case Flow 混合单条执行 ${item.displayNo || item.ordinal}. ${item.rawTitle}`,
      )
    } catch (error) {
      store.error = error instanceof Error ? error.message : String(error)
    }
    return
  }
  void store.updateCaseStatus(caseId, 'running')
}

async function openDevicePicker(item: CaseListItem, target: 'app' | 'web') {
  devicePicker.open = true
  devicePicker.loading = true
  devicePicker.error = ''
  devicePicker.devices = []
  devicePicker.caseItem = item
  devicePicker.target = target
  try {
    const result = target === 'web' ? await store.listAIWebDevices() : await store.listAIPhoneDevices()
    devicePicker.devices = result.devices
    devicePicker.error = result.error || ''
  } catch (error) {
    devicePicker.error = error instanceof Error ? error.message : String(error)
  } finally {
    devicePicker.loading = false
  }
}

function closeDevicePicker() {
  devicePicker.open = false
  devicePicker.loading = false
  devicePicker.error = ''
  devicePicker.devices = []
  devicePicker.caseItem = null
  devicePicker.target = 'app'
}

async function executeSingleDevice(device: AIPhoneDevice) {
  if (!devicePicker.caseItem) {
    return
  }
  const alias = deviceAlias(device)
  if (!alias) {
    devicePicker.error = '设备缺少 alias 或 serial，无法提交。'
    return
  }
  const platform = normalizePlatform(device.platform || (devicePicker.target === 'web' ? 'chrome' : 'android'))
  devicePicker.loading = true
  devicePicker.error = ''
  try {
    const title = `Case Flow 单条执行 ${devicePicker.caseItem.displayNo || devicePicker.caseItem.ordinal}. ${devicePicker.caseItem.rawTitle}`
    if (devicePicker.target === 'web') {
      await store.submitAIWebCases(
        [devicePicker.caseItem.id],
        { [platform || 'chrome']: [alias] },
        `Case Flow Web 单条执行 ${devicePicker.caseItem.displayNo || devicePicker.caseItem.ordinal}. ${devicePicker.caseItem.rawTitle}`,
      )
    } else {
      await store.submitAIPhoneCases(
        [devicePicker.caseItem.id],
        { [platform]: [alias] },
        title,
      )
    }
    closeDevicePicker()
  } catch (error) {
    devicePicker.error = error instanceof Error ? error.message : String(error)
  } finally {
    devicePicker.loading = false
  }
}

async function executeSelected() {
  const selected = store.selectedVisibleCases
  if (!selected.length) {
    return
  }
  executionQueue.open = true
  executionQueue.error = ''
  executionQueue.items = [...selected]
  executionQueue.selectedAliases = []
  executionQueue.devices = []
  executionQueue.webSelectedAliases = []
  executionQueue.webDevices = []
  const loaders: Promise<void>[] = []
  if (selected.some((item) => item.executionTarget === 'app')) {
    loaders.push(loadQueueDevices())
  }
  if (selected.some((item) => item.executionTarget === 'web')) {
    loaders.push(loadQueueWebDevices())
  }
  if (loaders.length) {
    await Promise.all(loaders)
  }
}

async function loadQueueDevices() {
  executionQueue.loading = true
  executionQueue.error = ''
  try {
    const result = await store.listAIPhoneDevices()
    executionQueue.devices = result.devices
    executionQueue.selectedAliases = result.devices.map(deviceAlias).filter(Boolean)
    executionQueue.error = result.error || ''
  } catch (error) {
    executionQueue.error = error instanceof Error ? error.message : String(error)
  } finally {
    executionQueue.loading = false
  }
}

async function loadQueueWebDevices() {
  executionQueue.loading = true
  executionQueue.error = ''
  try {
    const result = await store.listAIWebDevices()
    executionQueue.webDevices = result.devices
    executionQueue.webSelectedAliases = result.devices.map(deviceAlias).filter(Boolean)
    executionQueue.error = result.error || ''
  } catch (error) {
    executionQueue.error = error instanceof Error ? error.message : String(error)
  } finally {
    executionQueue.loading = false
  }
}

function closeExecutionQueue() {
  executionQueue.open = false
  executionQueue.loading = false
  executionQueue.error = ''
  executionQueue.devices = []
  executionQueue.selectedAliases = []
  executionQueue.webDevices = []
  executionQueue.webSelectedAliases = []
  executionQueue.items = []
  executionQueue.draggingCaseId = null
  executionQueue.retryMax = 0
  executionQueue.cacheMode = 'off'
}

async function confirmExecutionQueue() {
  if (!executionQueue.items.length) {
    closeExecutionQueue()
    return
  }
  executionQueue.loading = true
  executionQueue.error = ''
  const groupId = newExecutionRequestGroupId()
  try {
    if (queueAppItems.value.length) {
      const pools = selectedDeviceAliasPools(queueVisibleDevices.value, executionQueue.selectedAliases)
      if (!Object.keys(pools).length) {
        executionQueue.error = 'AI Phone 执行需要至少选择一台在线设备（注意当前设备筛选）。'
        return
      }
      await store.submitAIPhoneCases(
        queueAppItems.value.map((item) => item.id),
        pools,
        `Case Flow 批量执行 ${new Date().toLocaleString('zh-CN', { hour12: false })}`,
        { cacheMode: executionQueue.cacheMode, retryMax: executionQueue.retryMax, executionRequestGroupId: groupId },
      )
    }
    if (queueWebItems.value.length) {
      const pools = selectedDeviceAliasPools(executionQueue.webDevices, executionQueue.webSelectedAliases, 'chrome')
      if (!Object.keys(pools).length) {
        executionQueue.error = 'AI Web 执行需要至少选择一个浏览器槽。'
        return
      }
      await store.submitAIWebCases(
        queueWebItems.value.map((item) => item.id),
        pools,
        `Case Flow Web 批量执行 ${new Date().toLocaleString('zh-CN', { hour12: false })}`,
        { executionRequestGroupId: groupId },
      )
    }
    if (queueApiItems.value.length) {
      await store.submitAIAPICases(
        queueApiItems.value.map((item) => item.id),
        `Case Flow API 批量执行 ${new Date().toLocaleString('zh-CN', { hour12: false })}`,
        { executionRequestGroupId: groupId },
      )
    }
    if (queueMixedItems.value.length) {
      await store.submitHybridCases(
        queueMixedItems.value.map((item) => item.id),
        `Case Flow 混合批量执行 ${new Date().toLocaleString('zh-CN', { hour12: false })}`,
        { executionRequestGroupId: groupId },
      )
    }
    if (queueManualItems.value.length) {
      await store.updateCasesStatus(queueManualItems.value.map((item) => item.id), 'running')
    }
    closeExecutionQueue()
  } catch (error) {
    executionQueue.error = error instanceof Error ? error.message : String(error)
  } finally {
    executionQueue.loading = false
  }
}

function stopSelected() {
  const runningIds = store.selectedVisibleCases
    .filter((item) => item.executionStatus === 'running')
    .map((item) => item.id)
  if (runningIds.length) {
    void store.updateCasesStatus(runningIds, 'not_run')
  }
}

function repairSelected() {
  const failedIds = store.selectedVisibleCases
    .filter((item) => item.executionStatus === 'failed')
    .map((item) => item.id)
  void openRepairDialog(failedIds)
}

function repairCase(caseId: number) {
  void openRepairDialog([caseId])
}

async function openRepairDialog(caseIds: number[]) {
  if (!caseIds.length) {
    return
  }
  const requestId = repairDialog.requestId + 1
  repairDialog.requestId = requestId
  repairDialog.open = true
  repairDialog.loading = true
  repairDialog.error = ''
  repairDialog.caseIds = [...caseIds]
  repairDialog.items = []
  try {
    const result = await store.previewRepairs(caseIds)
    if (repairDialog.requestId !== requestId) {
      return
    }
    repairDialog.items = result.items
  } catch (error) {
    if (repairDialog.requestId !== requestId) {
      return
    }
    repairDialog.error = error instanceof Error ? error.message : String(error)
  } finally {
    if (repairDialog.requestId === requestId) {
      repairDialog.loading = false
    }
  }
}

function closeRepairDialog() {
  repairDialog.requestId += 1
  repairDialog.open = false
  repairDialog.loading = false
  repairDialog.error = ''
  repairDialog.caseIds = []
  repairDialog.items = []
  repairDialog.applyingDraftId = null
}

async function applyRepairDraft(item: RepairDraft) {
  if (!item.draftId || !repairGateCanRepair(item)) {
    return
  }
  repairDialog.applyingDraftId = item.draftId
  repairDialog.error = ''
  try {
    await store.applyRepairDraft(item.draftId, {
      stepsText: item.proposedSteps,
      preconditions: item.proposedPreconditions,
      expectedResult: item.proposedExpected,
    })
    repairDialog.items = repairDialog.items.filter((candidate) => candidate.draftId !== item.draftId)
    if (!repairDialog.items.length) {
      closeRepairDialog()
    }
  } catch (error) {
    repairDialog.error = error instanceof Error ? error.message : String(error)
  } finally {
    repairDialog.applyingDraftId = null
  }
}

function skipRepairItem(caseId: number) {
  repairDialog.items = repairDialog.items.filter((candidate) => candidate.caseId !== caseId)
  if (!repairDialog.items.length) {
    closeRepairDialog()
  }
}

function repairGateLabel(item: RepairDraft) {
  const gate = item.gate as { label?: unknown; canRepair?: unknown; allowed?: unknown } | null
  const label = String(gate?.label || '').trim()
  if (label) {
    return failureTypeLabels[label] ?? (repairGateCanRepair(item) ? '可修复' : '不确定')
  }
  if (repairGateCanRepair(item)) {
    return '可修复'
  }
  return '不确定'
}

function repairGateCanRepair(item: RepairDraft) {
  const gate = item.gate as { canRepair?: unknown; can_repair?: unknown; allowed?: unknown } | null
  if (typeof gate?.canRepair === 'boolean') {
    return gate.canRepair
  }
  if (typeof gate?.can_repair === 'boolean') {
    return gate.can_repair
  }
  return item.repairable
}

function repairGateReason(item: RepairDraft) {
  const gate = item.gate as { reason?: unknown } | null
  return String(gate?.reason || item.reason || '').trim()
}

function repairReportSummary(item: RepairDraft) {
  return item.reportSummary || repairGateReason(item) || '当前 case 没有关联执行报告，不能生成诊断修复候选。'
}

function repairResultTitle(item: RepairDraft) {
  return repairGateCanRepair(item) ? '修复后步骤' : '无法修复'
}

function repairResultText(item: RepairDraft) {
  return repairGateCanRepair(item)
    ? (item.proposedSteps || '模型没有返回可用的候选步骤。')
    : (repairGateReason(item) || '当前无法自动修复。')
}

async function openReport(caseId: number) {
  const item = store.cases.find((candidate) => candidate.id === caseId)
  // 多端：先看各端报告，多于一个就弹浮层让用户选；否则直接开。
  let results: CasePlatformResult[] = []
  try {
    results = await store.getCasePlatformResults(caseId)
  } catch {
    results = []
  }
  const withReports = results.filter((r) => !!r.reportUrl)
  if (withReports.length > 1) {
    reportChooser.caseTitle = item ? `${item.displayNo || item.ordinal}. ${item.rawTitle}` : ''
    reportChooser.results = withReports
    reportChooser.open = true
    return
  }
  const url = withReports[0]?.reportUrl || item?.reportUrl
  if (url) {
    window.open(url, '_blank', 'noopener')
  }
}

const platformResultLabels: Record<string, string> = {
  android: 'Android',
  ios: 'iOS',
  harmony: 'Harmony',
  chrome: 'Chrome',
  safari: 'Safari',
  webkit: 'Safari',
  firefox: 'Firefox',
  mixed: 'AI Hybrid',
}

function platformResultTitle(result: CasePlatformResult) {
  const name = platformResultLabels[result.platform] || result.platform
  const stateText = result.state === 'passed' ? '通过' : result.state === 'failed' ? '失败' : '未执行'
  return `${name} · ${stateText}`
}

function openReportLink(result: CasePlatformResult) {
  if (result.reportUrl) {
    window.open(result.reportUrl, '_blank', 'noopener')
  }
}

function closeReportChooser() {
  reportChooser.open = false
  reportChooser.results = []
}

function editCase(caseId: number) {
  const item = store.cases.find((candidate) => candidate.id === caseId)
  if (!item) {
    return
  }
  editDialog.open = true
  editDialog.error = ''
  editDialog.caseId = item.id
  editDialog.rawTitle = item.rawTitle
  editDialog.preconditions = item.preconditions
  editDialog.stepsText = item.stepsText
  editDialog.expectedResult = item.expectedResult
}

function closeEditDialog() {
  editDialog.open = false
  editDialog.saving = false
  editDialog.error = ''
  editDialog.caseId = null
  editDialog.rawTitle = ''
  editDialog.preconditions = ''
  editDialog.stepsText = ''
  editDialog.expectedResult = ''
}

async function saveEditDialog() {
  if (!editDialog.caseId) {
    return
  }
  editDialog.saving = true
  editDialog.error = ''
  try {
    await store.updateCaseAsset(editDialog.caseId, {
      rawTitle: editDialog.rawTitle,
      preconditions: editDialog.preconditions,
      stepsText: editDialog.stepsText,
      expectedResult: editDialog.expectedResult,
    })
    closeEditDialog()
  } catch (error) {
    editDialog.error = error instanceof Error ? error.message : String(error)
  } finally {
    editDialog.saving = false
  }
}

function deviceAlias(device: AIPhoneDevice) {
  return String(device.alias || device.serial || '').trim()
}

function occupancyMeta(device: AIPhoneDevice) {
  return device.occupancy === 'busy'
    ? { label: '占用中', className: 'occupancy-busy' }
    : { label: '空闲', className: 'occupancy-idle' }
}

function deviceGroupSummary(devices: AIPhoneDevice[]) {
  const idle = devices.filter((device) => device.occupancy !== 'busy').length
  const busy = devices.length - idle
  return busy ? `空闲 ${idle} / 占用 ${busy}` : `${idle} 台空闲`
}

function groupDevicesByPlatform(devices: AIPhoneDevice[]) {
  return devices.reduce<Record<string, AIPhoneDevice[]>>((groups, device) => {
    const platform = normalizePlatform(device.platform || 'unknown')
    groups[platform] = groups[platform] || []
    groups[platform].push(device)
    return groups
  }, {})
}

function normalizePlatform(platform: unknown) {
  const value = String(platform || '').toLowerCase()
  if (value === 'webkit') return 'safari'
  if (value === 'chromium') return 'chrome'
  return value || 'unknown'
}

function platformLabel(platform: string) {
  const labels: Record<string, string> = {
    android: 'Android',
    ios: 'iOS',
    harmony: 'Harmony',
    chrome: 'Chrome',
    safari: 'Safari',
    webkit: 'Safari',
    firefox: 'Firefox',
    unknown: '未知平台',
  }
  return labels[platform] || platform
}

function selectedDeviceAliasPools(devices: AIPhoneDevice[], aliases: string[], fallbackPlatform = 'android') {
  const selected = new Set(aliases)
  return devices.reduce<Record<string, string[]>>((pools, device) => {
    const alias = deviceAlias(device)
    if (!alias || !selected.has(alias)) {
      return pools
    }
    const platform = normalizePlatform(device.platform || fallbackPlatform)
    pools[platform] = pools[platform] || []
    pools[platform].push(alias)
    return pools
  }, {})
}

function setQueuePlatformDevices(platform: string, checked: boolean) {
  const selected = new Set(executionQueue.selectedAliases)
  for (const device of queueVisibleDevices.value.filter((item) => String(item.platform || 'unknown').toLowerCase() === platform)) {
    const alias = deviceAlias(device)
    if (!alias) {
      continue
    }
    if (checked) {
      selected.add(alias)
    } else {
      selected.delete(alias)
    }
  }
  executionQueue.selectedAliases = [...selected]
}

function setQueueWebPlatformDevices(platform: string, checked: boolean) {
  const selected = new Set(executionQueue.webSelectedAliases)
  for (const device of executionQueue.webDevices.filter((item) => String(item.platform || 'unknown').toLowerCase() === platform)) {
    const alias = deviceAlias(device)
    if (!alias) {
      continue
    }
    if (checked) {
      selected.add(alias)
    } else {
      selected.delete(alias)
    }
  }
  executionQueue.webSelectedAliases = [...selected]
}

function toggleQueueDevice(alias: string, checked: boolean) {
  const selected = new Set(executionQueue.selectedAliases)
  if (checked) {
    selected.add(alias)
  } else {
    selected.delete(alias)
  }
  executionQueue.selectedAliases = [...selected]
}

function toggleQueueWebDevice(alias: string, checked: boolean) {
  const selected = new Set(executionQueue.webSelectedAliases)
  if (checked) {
    selected.add(alias)
  } else {
    selected.delete(alias)
  }
  executionQueue.webSelectedAliases = [...selected]
}

function queueExecutorLabel(item: CaseListItem) {
  const labels: Record<ExecutionTarget, string> = {
    app: 'AI Phone',
    web: 'AI Web',
    api: 'AI API',
    mixed: 'AI Hybrid',
    manual: '人工',
    unknown: '人工',
  }
  return labels[item.executionTarget] || '人工'
}

function statusClass(status: ExecutionStatus) {
  return `status-${status.replace('_', '-')}`
}

function dragQueueItem(caseId: number) {
  executionQueue.draggingCaseId = caseId
}

function dropQueueItem(targetCaseId: number) {
  const draggedCaseId = executionQueue.draggingCaseId
  executionQueue.draggingCaseId = null
  if (!draggedCaseId || draggedCaseId === targetCaseId) {
    return
  }
  const nextItems = [...executionQueue.items]
  const fromIndex = nextItems.findIndex((item) => item.id === draggedCaseId)
  const toIndex = nextItems.findIndex((item) => item.id === targetCaseId)
  if (fromIndex < 0 || toIndex < 0) {
    return
  }
  const [item] = nextItems.splice(fromIndex, 1)
  nextItems.splice(toIndex, 0, item)
  executionQueue.items = nextItems
}

function moveQueueItem(caseId: number, direction: -1 | 1) {
  const nextItems = [...executionQueue.items]
  const fromIndex = nextItems.findIndex((item) => item.id === caseId)
  const toIndex = fromIndex + direction
  if (fromIndex < 0 || toIndex < 0 || toIndex >= nextItems.length) {
    return
  }
  const [item] = nextItems.splice(fromIndex, 1)
  nextItems.splice(toIndex, 0, item)
  executionQueue.items = nextItems
}
</script>

<template>
  <section class="home-layout" :class="{ 'context-pinned': stickyContextVisible }">
    <div class="overview-card">
      <div class="overview-head">
        <div>
          <h1>当前需求执行概览</h1>
        </div>
          <span>{{ store.dashboard?.requirements.length ?? 0 }} 个进行中需求</span>
      </div>
      <div class="stats-row">
        <div class="stat-card">
          <span>总 case</span>
          <strong>{{ store.summary.caseCount }}</strong>
        </div>
        <div class="stat-card">
          <span>未执行</span>
          <strong>{{ store.summary.notRun }}</strong>
        </div>
        <div class="stat-card running">
          <span>执行中</span>
          <strong>{{ store.summary.running }}</strong>
        </div>
        <div class="stat-card passed">
          <span>通过</span>
          <strong>{{ store.summary.passed }}</strong>
        </div>
        <div class="stat-card failed">
          <span>失败</span>
          <strong>{{ store.summary.failed }}</strong>
        </div>
      </div>
    </div>

    <div class="task-card">
      <div class="task-head">
        <div>
          <h2>我的进行中任务</h2>
          <p>按当前人员展示已挂载的二级需求与 case 状态。</p>
        </div>
      </div>
      <div v-if="autoDiscoveryError" class="error-state">{{ autoDiscoveryError }}</div>
      <div class="task-list">
        <div
          v-for="item in store.dashboard?.requirements ?? []"
          :key="item.requirementItemId"
          class="home-requirement-item"
          :class="{ active: item.requirementItemId === store.selectedRequirementId }"
          role="button"
          tabindex="0"
          @click="selectRequirementAndEnterMap(item.requirementItemId)"
          @keydown.enter="selectRequirementAndEnterMap(item.requirementItemId)"
        >
          <div class="home-task-primary">
            <span class="task-key">{{ item.groupName || '未进入目录' }}</span>
            <span class="task-title">{{ item.requirementItemTitle }}</span>
          </div>
          <div class="mini-status-row">
            <span>总 {{ item.caseCount }}</span>
            <span class="mini-not-run">未 {{ item.notRun }}</span>
            <span class="mini-running">执 {{ item.running }}</span>
            <span class="mini-passed">过 {{ item.passed }}</span>
            <span class="mini-failed">错 {{ item.failed }}</span>
            <span class="mini-attention">变 {{ item.attentionChanged }}</span>
          </div>
          <div class="home-task-controls" @click.stop>
            <label
              v-if="AUTO_DISCOVERY_UI"
              class="auto-discovery-toggle"
              title="自动发现：开启时执行会按 case 自动匹配 Function Map（发现本体后续检查点接入）；关闭后只用显式挂载"
            >
              <input
                type="checkbox"
                :checked="item.autoDiscoveryEnabled"
                @change="onToggleAutoDiscovery(item, $event)"
              />
              <span>自动发现</span>
            </label>
            <button
              type="button"
              class="task-fm-btn"
              title="查看该需求挂载的 Function Map（只读）"
              @click="openRequirementFunctionMap(item)"
            >
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>
              Function Map
            </button>
          </div>
        </div>
      </div>
    </div>

    <div ref="contextTriggerRef" class="home-context-trigger" aria-hidden="true"></div>

    <div class="home-sticky-context" :class="{ visible: stickyContextVisible }">
      <div class="sticky-context-main">
        <strong>{{ store.selectedRequirement?.groupName || '未进入目录' }}</strong>
        <em>/</em>
        <span>{{ store.selectedRequirement?.requirementItemTitle || '当前需求' }}</span>
      </div>
      <div class="sticky-context-pills">
        <span class="context-pill context-neutral">总 {{ store.selectedRequirement?.caseCount || 0 }}</span>
        <span class="context-pill context-not-run">未 {{ store.selectedRequirement?.notRun || 0 }}</span>
        <span class="context-pill context-running">执 {{ store.selectedRequirement?.running || 0 }}</span>
        <span class="context-pill context-passed">过 {{ store.selectedRequirement?.passed || 0 }}</span>
        <span class="context-pill context-failed">错 {{ store.selectedRequirement?.failed || 0 }}</span>
        <span class="context-pill context-changed">变 {{ store.selectedRequirement?.attentionChanged || 0 }}</span>
      </div>
      <div class="sticky-context-user">{{ currentUser?.displayName || '未选择用户' }}</div>
    </div>

    <div ref="workspaceRef" class="workspace-grid">
      <CaseMindMap
        :cases="store.cases"
        :requirement="store.selectedRequirement"
        :selected-case-id="store.selectedCaseId"
        @select-case="selectCaseFromMindMap"
      />
      <CaseOperationPanel
        :cases="store.filteredCases"
        :selected-case-id="store.selectedCaseId"
        :scroll-to-case-id="caseScrollTargetId"
        :scroll-request-key="caseScrollRequestKey"
        :selected-case-ids="store.selectedCaseIds"
        :status-filter="store.statusFilter"
        :total-case-count="store.cases.length"
        :now-ms="nowMs"
        @select-case="selectCaseFromPanel"
        @set-filter="setFilter"
        @toggle-case="store.toggleCaseSelection"
        @toggle-all="store.toggleAllFilteredCases"
        @execute-selected="executeSelected"
        @stop-selected="stopSelected"
        @repair-selected="repairSelected"
        @repair-case="repairCase"
        @submit-bug="openBugDialog"
        @execute-case="executeCase"
        @cycle-coverage="store.cycleCoverage"
        @update-status="updateStatus"
        @update-target="updateTarget"
        @open-report="openReport"
        @edit-case="editCase"
      />
    </div>

    <div v-if="reportChooser.open" class="modal-mask">
      <section class="import-modal report-chooser-modal">
        <div class="modal-head">
          <div>
            <h2>选择端报告</h2>
            <p v-if="reportChooser.caseTitle">{{ reportChooser.caseTitle }}</p>
          </div>
          <button type="button" @click="closeReportChooser">关闭</button>
        </div>
        <div class="report-chooser-list">
          <button
            v-for="result in reportChooser.results"
            :key="result.platform"
            type="button"
            class="report-chooser-item"
            :class="`report-${result.state}`"
            :disabled="!result.reportUrl"
            @click="openReportLink(result)"
          >
            <strong>{{ platformResultTitle(result) }}</strong>
            <span v-if="result.statusReason">{{ result.statusReason }}</span>
            <small>{{ result.reportUrl ? '点击查看报告' : '暂无报告' }}</small>
          </button>
        </div>
      </section>
    </div>

    <div v-if="devicePicker.open" class="modal-mask">
      <section class="import-modal device-modal device-picker-modal">
        <div class="modal-head">
          <div>
            <h2>{{ devicePicker.target === 'web' ? '选择浏览器槽' : '选择执行设备' }}</h2>
            <p v-if="devicePicker.caseItem">
              {{ devicePicker.caseItem.displayNo || devicePicker.caseItem.ordinal }}. {{ devicePicker.caseItem.rawTitle }} · {{ devicePicker.target === 'web' ? '在线浏览器槽' : '在线设备' }}
            </p>
          </div>
          <button type="button" @click="closeDevicePicker">关闭</button>
        </div>
        <div v-if="devicePicker.loading" class="notice-state">
          {{ devicePicker.target === 'web' ? '正在读取浏览器槽...' : '正在读取在线设备...' }}
        </div>
        <div v-else-if="!devicePicker.devices.length" class="notice-state">
          {{ devicePicker.error || (devicePicker.target === 'web' ? 'AI Web 当前没有返回浏览器槽。' : 'AI Phone 当前没有返回在线设备。') }}
        </div>
        <div v-else class="device-group-list">
          <section v-for="(devices, platform) in pickerDeviceGroups" :key="platform" class="device-group device-picker-group">
            <div class="device-group-head">
              <h3>{{ platformLabel(platform) }}</h3>
              <span>{{ deviceGroupSummary(devices) }}</span>
            </div>
            <div class="device-grid">
              <button
                v-for="device in devices"
                :key="deviceAlias(device)"
                type="button"
                class="device-card device-pick-card"
                @click="executeSingleDevice(device)"
              >
                <strong>
                  {{ deviceAlias(device) || '未命名设备' }}
                  <span class="device-occupancy-pill" :class="occupancyMeta(device).className">{{ occupancyMeta(device).label }}</span>
                </strong>
                <span>{{ [device.brand, device.model, device.osVersion].filter(Boolean).join(' · ') || platformLabel(platform) }}</span>
                <small>{{ device.serial }}</small>
              </button>
            </div>
          </section>
        </div>
        <div v-if="devicePicker.error && devicePicker.devices.length" class="error-state">
          {{ devicePicker.error }}
        </div>
        <div class="modal-actions">
          <button type="button" @click="closeDevicePicker">取消</button>
        </div>
      </section>
    </div>

    <div v-if="executionQueue.open" class="modal-mask">
      <section class="import-modal execution-modal execution-queue-modal">
        <div class="modal-head">
          <div>
            <h2>本次执行队列</h2>
            <p>{{ executionQueueSummary }}</p>
          </div>
          <button type="button" @click="closeExecutionQueue">关闭</button>
        </div>

        <div class="queue-hint">
          拖动左侧手柄调整本次推送顺序；这个顺序只影响本次执行，不改变 Case 列表和脑图。
        </div>

        <div class="execution-strategy-panel">
          <div v-if="queueAppItems.length" class="strategy-card">
            <div class="strategy-head">
              <div>
                <strong>App / AI Phone 执行策略</strong>
                <div class="meta">{{ queueAppItems.length }} 条 App case · 在线设备</div>
              </div>
              <div class="strategy-actions">
                <span class="count-pill">已选 {{ queueSelectedVisibleCount }} 台</span>
                <button type="button" :disabled="executionQueue.loading" @click="loadQueueDevices">刷新设备</button>
              </div>
            </div>
            <div class="strategy-note">
              所选设备会作为 AI Phone 的 deviceAliasPools；多条 App case 共享同一设备池，由 AI Phone 调度器自动排队和并行。
            </div>
            <div class="device-filter-row">
              <input
                :value="executionQueue.deviceFilter"
                type="search"
                placeholder="按设备名筛选（如：学习工具），只看/只调度匹配的设备，本地保存"
                @input="onQueueDeviceFilterInput(($event.target as HTMLInputElement).value)"
              />
            </div>
            <div class="strategy-options">
              <label>
                <span>失败重试次数</span>
                <input v-model.number="executionQueue.retryMax" type="number" min="0" max="10" />
              </label>
              <label>
                <span>轨迹缓存模式</span>
                <select v-model="executionQueue.cacheMode">
                  <option value="off">off（不开启）</option>
                  <option value="v1">v1</option>
                  <option value="v2">v2</option>
                  <option value="v3">v3</option>
                </select>
              </label>
            </div>
            <div v-if="executionQueue.loading" class="notice-state">正在读取在线设备...</div>
            <div v-else-if="!executionQueue.devices.length" class="notice-state">
              {{ executionQueue.error || 'AI Phone 当前没有返回在线设备。' }}
            </div>
            <div v-else-if="!queueVisibleDevices.length" class="notice-state">
              当前筛选「{{ executionQueue.deviceFilter }}」没有匹配的在线设备，请调整或清空筛选。
            </div>
            <div v-else class="device-group-list strategy-device-list">
              <section v-for="(devices, platform) in queueDeviceGroups" :key="platform" class="device-group strategy-device-group">
                <div class="device-group-head">
                  <h3>{{ platformLabel(platform) }}</h3>
                  <span>{{ deviceGroupSummary(devices) }}</span>
                  <button type="button" @click="setQueuePlatformDevices(platform, true)">全选</button>
                  <button type="button" @click="setQueuePlatformDevices(platform, false)">清空</button>
                </div>
                <div class="device-grid strategy-device-grid">
                  <label v-for="device in devices" :key="deviceAlias(device)" class="device-check device-chip">
                    <input
                      type="checkbox"
                      :checked="executionQueue.selectedAliases.includes(deviceAlias(device))"
                      @change="toggleQueueDevice(deviceAlias(device), ($event.target as HTMLInputElement).checked)"
                    />
                    <span>
                      <strong>
                        {{ deviceAlias(device) || '未命名设备' }}
                        <span class="device-occupancy-pill" :class="occupancyMeta(device).className">{{ occupancyMeta(device).label }}</span>
                      </strong>
                      <small>{{ [device.brand, device.model, device.osVersion].filter(Boolean).join(' · ') || platformLabel(platform) }}</small>
                    </span>
                  </label>
                </div>
              </section>
            </div>
          </div>

          <div v-if="queueWebItems.length" class="strategy-card">
            <div class="strategy-head">
              <div>
                <strong>Web / AI Web 执行策略</strong>
                <div class="meta">{{ queueWebItems.length }} 条 case · 在线浏览器槽</div>
              </div>
              <div class="strategy-actions">
                <span class="count-pill">已选 {{ executionQueue.webSelectedAliases.length }} 个</span>
                <button type="button" :disabled="executionQueue.loading" @click="loadQueueWebDevices">刷新浏览器槽</button>
              </div>
            </div>
            <div class="strategy-note">所选浏览器槽会作为 AI Web 的 deviceAliasPools；多条 Web case 共享同一浏览器槽池。</div>
            <div v-if="executionQueue.loading" class="notice-state">正在读取浏览器槽...</div>
            <div v-else-if="!executionQueue.webDevices.length" class="notice-state">
              {{ executionQueue.error || 'AI Web 当前没有返回浏览器槽。' }}
            </div>
            <div v-else class="device-group-list strategy-device-list">
              <section v-for="(devices, platform) in queueWebDeviceGroups" :key="platform" class="device-group strategy-device-group">
                <div class="device-group-head">
                  <h3>{{ platformLabel(platform) }}</h3>
                  <span>{{ deviceGroupSummary(devices) }}</span>
                  <button type="button" @click="setQueueWebPlatformDevices(platform, true)">全选</button>
                  <button type="button" @click="setQueueWebPlatformDevices(platform, false)">清空</button>
                </div>
                <div class="device-grid strategy-device-grid">
                  <label v-for="device in devices" :key="deviceAlias(device)" class="device-check device-chip">
                    <input
                      type="checkbox"
                      :checked="executionQueue.webSelectedAliases.includes(deviceAlias(device))"
                      @change="toggleQueueWebDevice(deviceAlias(device), ($event.target as HTMLInputElement).checked)"
                    />
                    <span>
                      <strong>
                        {{ deviceAlias(device) || '未命名浏览器槽' }}
                        <span class="device-occupancy-pill" :class="occupancyMeta(device).className">{{ occupancyMeta(device).label }}</span>
                      </strong>
                      <small>{{ [device.brand, device.model, device.osVersion].filter(Boolean).join(' · ') || platformLabel(platform) }}</small>
                    </span>
                  </label>
                </div>
              </section>
            </div>
          </div>

          <div v-if="queueApiItems.length" class="strategy-card">
            <div class="strategy-head">
              <div>
                <strong>API / AI API 执行策略</strong>
                <div class="meta">{{ queueApiItems.length }} 条 case</div>
              </div>
            </div>
            <div class="strategy-note">AI API 是内置执行器，不需要设备池；确认后后端会编译请求、执行接口并生成报告。</div>
          </div>

          <div v-if="queueMixedItems.length" class="strategy-card">
            <div class="strategy-head">
              <div>
                <strong>混合 / AI Hybrid 执行策略</strong>
                <div class="meta">{{ queueMixedItems.length }} 条 case</div>
              </div>
            </div>
            <div class="strategy-note">AI Hybrid 是内置编排器，不需要设备池；确认后会按 case 内容自动拆分到 API、Web、Phone 等子工具。</div>
          </div>

          <div v-if="queueManualItems.length" class="strategy-card is-placeholder">
            <div class="strategy-head">
              <div>
                <strong>人工执行策略</strong>
                <div class="meta">{{ queueManualItems.length }} 条 case</div>
              </div>
            </div>
            <div class="strategy-note">人工 case 不推送执行器，当前只会进入执行中状态，后续再接人工任务流。</div>
          </div>
        </div>

        <div class="queue-list">
          <article
            v-for="(item, index) in executionQueue.items"
            :key="item.id"
            class="queue-item execution-queue-item"
            :class="statusClass(item.executionStatus)"
            draggable="true"
            @dragstart="dragQueueItem(item.id)"
            @dragover.prevent
            @drop="dropQueueItem(item.id)"
          >
            <div class="queue-order-tools">
              <button type="button" class="queue-handle" title="拖动调整本次执行顺序">↕</button>
              <button
                type="button"
                data-control="queue-move-up"
                title="上移"
                :disabled="index === 0"
                @click="moveQueueItem(item.id, -1)"
              >
                ↑
              </button>
              <button
                type="button"
                data-control="queue-move-down"
                title="下移"
                :disabled="index === executionQueue.items.length - 1"
                @click="moveQueueItem(item.id, 1)"
              >
                ↓
              </button>
            </div>
            <strong class="queue-order">{{ index + 1 }}</strong>
            <div class="queue-case-body">
              <b>{{ item.rawTitle }}</b>
              <span class="meta">{{ item.path }}</span>
              <div class="case-tag-row">
                <span class="status-pill" :class="statusClass(item.executionStatus)">
                  {{ statusLabels[item.executionStatus] }}
                </span>
                <span class="executor-pill">{{ queueExecutorLabel(item) }}</span>
              </div>
            </div>
          </article>
        </div>

        <div v-if="executionQueue.error" class="error-state">{{ executionQueue.error }}</div>
        <div class="modal-actions">
          <button type="button" @click="closeExecutionQueue">取消</button>
          <button type="button" class="primary" :disabled="executionQueue.loading" @click="confirmExecutionQueue">
            确认执行
          </button>
        </div>
      </section>
    </div>

    <div v-if="repairDialog.open" class="modal-mask">
      <section class="import-modal repair-modal">
        <div class="modal-head">
          <div>
            <h2>诊断修复</h2>
            <p>基于执行报告判断是否可修复；只有点击采用修复后才会写入 Case。</p>
          </div>
          <button type="button" @click="closeRepairDialog">关闭</button>
        </div>

        <div v-if="repairDialog.loading" class="repair-list case-repair-list">
          <div class="repair-loading-card">
            <strong>正在解读报告</strong>
            <span>共 {{ repairDialog.caseIds.length }} 条失败 case。系统会逐条读取报告、提取证据、判断是否可以给出修复步骤。</span>
          </div>
        </div>
        <div v-else class="repair-list case-repair-list">
          <article v-for="(item, index) in repairDialog.items" :key="item.caseId" class="case-repair-item">
            <div class="case-repair-head">
              <div>
                <strong>{{ index + 1 }}. {{ item.caseTitle }}</strong>
                <div class="meta">{{ item.path || '层级无' }}</div>
              </div>
              <div class="repair-status-stack">
                <span class="status-pill status-failed">失败</span>
                <span class="repair-gate-pill" :class="{ 'can-repair': repairGateCanRepair(item), blocked: !repairGateCanRepair(item) }">
                  {{ repairGateLabel(item) }}
                </span>
              </div>
            </div>
            <div class="repair-report-box">
              <strong>失败原因</strong>
              <p>{{ item.reason || repairReportSummary(item) }}</p>
              <template v-if="item.evidence">
                <strong>证据指证</strong>
                <p>{{ item.evidence }}</p>
              </template>
            </div>

            <details v-if="item.process && item.process.length" class="repair-process">
              <summary>{{ repairProcessSummary(item) }}</summary>
              <ol>
                <li v-for="(p, pIndex) in item.process" :key="repairProcessKey(p, pIndex)">
                  <span class="proc-head">{{ repairProcessHead(p, pIndex) }}</span>
                  <div v-for="line in repairProcessDetails(p)" :key="line" class="proc-note">{{ line }}</div>
                </li>
              </ol>
            </details>
            <div v-if="item.keyImages && item.keyImages.length" class="repair-evidence-grid">
              <figure v-for="(img, ei) in item.keyImages" :key="ei" class="repair-evidence-cell">
                <a :href="img.image" target="_blank" rel="noopener">
                  <img :src="img.image" :alt="img.platform || '关键失败截图'" />
                </a>
                <figcaption>{{ img.platform || '证据图' }}</figcaption>
              </figure>
            </div>
            <div v-else-if="item.keyImage" class="repair-evidence-image">
              <a :href="item.keyImage" target="_blank" rel="noopener">
                <img :src="item.keyImage" alt="关键失败截图" />
              </a>
            </div>

            <template v-if="repairGateCanRepair(item)">
              <div v-if="item.fixReason" class="repair-fix-reason">
                <strong>修改理由</strong>
                <p>{{ item.fixReason }}</p>
              </div>
              <div v-if="(item.proposedPreconditions || '') !== (item.originalPreconditions || '')" class="repair-diff-grid">
                <section>
                  <h4>原前置条件</h4>
                  <pre>{{ item.originalPreconditions || '无' }}</pre>
                </section>
                <section>
                  <h4>修复后前置（可编辑）</h4>
                  <textarea v-model="item.proposedPreconditions" rows="4" class="repair-edit-area"></textarea>
                </section>
              </div>
              <div v-if="(item.proposedExpected || '') !== (item.originalExpected || '')" class="repair-diff-grid">
                <section>
                  <h4>原预期结果</h4>
                  <pre>{{ item.originalExpected || '无' }}</pre>
                </section>
                <section>
                  <h4>修复后预期（可编辑）</h4>
                  <textarea v-model="item.proposedExpected" rows="4" class="repair-edit-area"></textarea>
                </section>
              </div>
              <div class="repair-diff-grid">
                <section>
                  <h4>原操作步骤</h4>
                  <pre>{{ item.originalSteps }}</pre>
                </section>
                <section>
                  <h4>修复后步骤（可编辑）</h4>
                  <textarea v-model="item.proposedSteps" rows="7" class="repair-edit-area"></textarea>
                </section>
              </div>
            </template>
            <div v-else class="repair-diff-grid">
              <section>
                <h4>原操作步骤</h4>
                <pre>{{ item.originalSteps }}</pre>
              </section>
              <section>
                <h4>无法修复</h4>
                <pre>{{ repairResultText(item) }}</pre>
              </section>
            </div>
            <div class="case-repair-actions">
              <button
                v-if="repairGateCanRepair(item)"
                type="button"
                class="primary"
                :disabled="repairDialog.applyingDraftId === item.draftId"
                @click="applyRepairDraft(item)"
              >
                采用修复
              </button>
              <a
                v-if="item.bugUrl"
                class="secondary-button bug-submitted-link"
                :href="item.bugUrl"
                target="_blank"
                rel="noopener"
              >
                已提交
              </a>
              <button v-else type="button" class="secondary-button" @click="openBugDialog(item.caseId)">
                提交 bug
              </button>
              <button type="button" class="secondary-button" @click="skipRepairItem(item.caseId)">
                本次不改
              </button>
            </div>
          </article>
          <div v-if="!repairDialog.items.length" class="notice-state">没有可展示的诊断修复项。</div>
        </div>

        <div v-if="repairDialog.error" class="error-state">{{ repairDialog.error }}</div>
        <div class="case-repair-footer">
          <button type="button" class="secondary-button" @click="closeRepairDialog">关闭</button>
        </div>
      </section>
    </div>

    <div v-if="bugDialog.open" class="modal-mask">
      <section class="import-modal bug-modal" @paste="handleBugImagePaste">
        <div class="modal-head">
          <div>
            <h2>提交 bug 到飞书项目</h2>
            <p>已按 case 与诊断预填，确认/修改后提交；提交后图片在后台异步渲染到描述，不用等待。</p>
          </div>
          <button type="button" @click="closeBugDialog">关闭</button>
        </div>

        <div class="bug-modal-body">
          <div v-if="bugDialog.submittedBugs.length" class="bug-submitted-list">
            <span class="bug-field-label">已提交 {{ bugDialog.submittedBugs.length }} 条 bug（可继续再提一条）</span>
            <a
              v-for="(b, idx) in bugDialog.submittedBugs"
              :key="b.id || idx"
              :href="b.url"
              target="_blank"
              rel="noopener"
            >#{{ idx + 1 }} {{ b.url }}</a>
          </div>

          <div v-if="bugDialog.loading" class="repair-loading-card">
            <strong>正在生成 bug 草稿</strong>
            <span>读取 case、关联需求与诊断结论，并由模型预填缺陷优先级/严重度/标签。</span>
          </div>

          <div v-else-if="bugDialog.resultUrl" class="bug-success">
            <strong>本次已提交（链接见上方列表）</strong>
            <span v-if="bugDialog.hasImage" class="bug-async-note">关键截图正在后台渲染到 bug 描述。</span>
          </div>

          <template v-else>
            <div class="asset-edit-form">
              <label class="wide">
                标题
                <input v-model="bugDialog.title" />
              </label>
              <label class="wide">
                描述（可编辑）
                <textarea v-model="bugDialog.description" rows="10" />
              </label>
              <div
                ref="bugImageDropzoneRef"
                class="bug-image-dropzone"
                :class="{ active: bugDialog.imageDragOver, uploading: bugDialog.imageUploading }"
                tabindex="0"
                @click="focusBugImageDropzone"
                @dragenter.prevent="handleBugImageDragOver"
                @dragover.prevent="handleBugImageDragOver"
                @dragleave="handleBugImageDragLeave"
                @drop.prevent="handleBugImageDrop"
              >
                <input
                  ref="bugImageInputRef"
                  type="file"
                  accept="image/*"
                  multiple
                  hidden
                  @change="handleBugImageFileChange"
                />
                <strong>补充截图</strong>
                <span>把问题现场、对比图或手动截图放在这里。提交时会和诊断截图一起带上。</span>
                <small>{{ bugDialog.imageUploading ? '上传中…' : '点击此区域后直接粘贴截图，或把图片拖进来' }}</small>
                <button type="button" class="bug-image-upload-button" @click.stop="selectBugImages">上传图片</button>
              </div>
              <div v-if="bugDialog.keyImages.length" class="bug-keyimage">
                <span class="bug-field-label">待随 bug 提交的截图（诊断图 + 手动补图；叉掉则本次不带）</span>
                <div class="bug-keyimage-grid">
                  <div
                    v-for="(img, i) in bugDialog.keyImages"
                    :key="i"
                    class="bug-keyimage-item"
                    :class="{ excluded: isKeyImageExcluded(img.image) }"
                  >
                    <a :href="img.image" target="_blank" rel="noopener">
                      <img :src="img.image" :alt="img.platform || '诊断关键截图'" />
                    </a>
                    <button
                      type="button"
                      class="bug-keyimage-x"
                      :title="isKeyImageExcluded(img.image) ? '点击恢复，本次提交带上' : '叉掉，本次提交不带'"
                      @click="toggleKeyImage(img.image)"
                    >
                      {{ isKeyImageExcluded(img.image) ? '↺' : '×' }}
                    </button>
                    <small>{{ img.platform || '证据图' }}{{ isKeyImageExcluded(img.image) ? '（不带）' : '' }}</small>
                  </div>
                </div>
              </div>
              <div v-else-if="bugDialog.keyImage" class="bug-keyimage">
                <span class="bug-field-label">诊断关键截图（提交后随描述附上）</span>
                <a :href="bugDialog.keyImage" target="_blank" rel="noopener">
                  <img :src="bugDialog.keyImage" alt="诊断关键截图" />
                </a>
              </div>
              <BugFieldEditor
                v-for="field in bugDialog.fields"
                :key="field.fieldKey"
                :field="field"
                @update:selected="setBugFieldSelected(field, $event)"
              />
            </div>
            <div v-if="bugDialog.error" class="error-state">{{ bugDialog.error }}</div>
          </template>
        </div>

        <div class="modal-actions">
          <template v-if="bugDialog.loading" />
          <template v-else-if="bugDialog.resultUrl">
            <button type="button" @click="submitAnotherBug">再提一条</button>
            <button type="button" class="primary" @click="closeBugDialog">完成</button>
          </template>
          <template v-else>
            <button type="button" @click="closeBugDialog">取消</button>
            <button type="button" class="primary" :disabled="bugDialog.submitting" @click="submitBugDialog">
              {{ bugDialog.submitting ? '提交中…' : '提交 bug' }}
            </button>
          </template>
        </div>
      </section>
    </div>

    <div v-if="editDialog.open" class="modal-mask">
      <section class="import-modal asset-edit-modal">
        <div class="modal-head">
          <div>
            <h2>编辑执行内容</h2>
            <p>首页只允许修改测试标题、前置条件、操作步骤、预期结果；层级移动在 Case 资产中处理。</p>
          </div>
          <button type="button" @click="closeEditDialog">关闭</button>
        </div>
        <div class="asset-edit-form">
          <label class="wide">
            测试标题
            <input v-model="editDialog.rawTitle" />
          </label>
          <label class="wide">
            前置条件
            <textarea v-model="editDialog.preconditions" rows="4" />
          </label>
          <label class="wide">
            操作步骤
            <textarea v-model="editDialog.stepsText" rows="7" />
          </label>
          <label class="wide">
            预期结果
            <textarea v-model="editDialog.expectedResult" rows="4" />
          </label>
        </div>
        <div v-if="editDialog.error" class="error-state">{{ editDialog.error }}</div>
        <div class="modal-actions">
          <button type="button" @click="closeEditDialog">取消</button>
          <button type="button" class="primary" :disabled="editDialog.saving" @click="saveEditDialog">
            保存并同步
          </button>
        </div>
      </section>
    </div>

    <div v-if="fmPopup" class="fm-ro-mask" @click.self="closeRequirementFunctionMap">
      <section class="fm-ro-modal">
        <header class="fm-ro-head">
          <div class="fm-ro-title">
            <strong>{{ fmPopup.title }}</strong>
            <span>{{ fmPopup.groupName }} · Function Map（只读）</span>
          </div>
          <button type="button" class="fm-ro-close" @click="closeRequirementFunctionMap">×</button>
        </header>
        <div class="fm-ro-body">
          <p v-if="fmLoading" class="fm-ro-empty">加载中…</p>
          <div v-else-if="fmError" class="error-state">{{ fmError }}</div>
          <template v-else>
            <section v-if="fmInherited.length" class="fm-ro-section">
              <span class="fm-ro-label">从一级目录继承</span>
              <div v-for="m in fmInherited" :key="`g-${m.id}`" class="fm-ro-row">
                <div class="fm-ro-main">
                  <strong>{{ m.title }}</strong>
                  <span class="fm-ro-desc">{{ m.description }}</span>
                </div>
                <span class="fm-ro-targets">
                  <span v-for="t in m.targets" :key="t" class="fm-ro-tag">{{ FM_TARGET_LABEL[t] }}</span>
                </span>
                <button type="button" class="fm-ro-view" @click="viewFmContent(m.id, m.title)">看正文</button>
              </div>
            </section>
            <section class="fm-ro-section">
              <span class="fm-ro-label">本需求挂载</span>
              <div v-if="!fmOwn.length" class="fm-ro-empty">本需求没有直接挂载。</div>
              <div v-for="m in fmOwn" :key="`i-${m.id}`" class="fm-ro-row">
                <div class="fm-ro-main">
                  <strong>{{ m.title }}</strong>
                  <span class="fm-ro-desc">{{ m.description }}</span>
                </div>
                <span class="fm-ro-targets">
                  <span v-for="t in m.targets" :key="t" class="fm-ro-tag">{{ FM_TARGET_LABEL[t] }}</span>
                </span>
                <button type="button" class="fm-ro-view" @click="viewFmContent(m.id, m.title)">看正文</button>
              </div>
            </section>
            <p v-if="!fmInherited.length && !fmOwn.length" class="fm-ro-empty">该需求还没有任何 Function Map。</p>
          </template>
        </div>
        <footer class="fm-ro-foot">
          <span class="fm-ro-hint">只读；增删挂载请到挂载管理</span>
          <button type="button" class="fm-ro-jump" @click="goRequirementMountManager(fmPopup.itemId)">去挂载管理</button>
        </footer>

        <div v-if="fmView" class="fm-ro-view-layer" @click.self="fmView = null">
          <div class="fm-ro-viewer">
            <header>
              <strong>{{ fmView.title }}</strong>
              <button type="button" @click="fmView = null">×</button>
            </header>
            <pre>{{ fmView.content }}</pre>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>
