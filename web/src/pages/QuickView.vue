<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import QuickBugModal from '../components/quick/QuickBugModal.vue'
import QuickDevicePickerModal from '../components/quick/QuickDevicePickerModal.vue'
import QuickMindMap from '../components/quick/QuickMindMap.vue'
import QuickOperationPanel from '../components/quick/QuickOperationPanel.vue'
import QuickRepairModal from '../components/quick/QuickRepairModal.vue'
import ImportProgressOverlay from '../components/ImportProgressOverlay.vue'
import UserSearchSelect from '../components/UserSearchSelect.vue'
import { useQuickSessionStore } from '../stores/quickSession'
import { useFunctionMapAssetsStore } from '../stores/functionMapAssets'
import type { FunctionMapAssetListItem, FunctionMapTarget } from '../types/functionMap'
import type {
  AIPhoneDevice,
  BugField,
  CasePlatformResult,
  ExecutionStatus,
  ExecutionTarget,
  RepairDraft,
  SubmittedBug,
} from '../types/case'
import type { QuickCaseItem } from '../types/quick'
import { filterVisibleUsers } from '../utils/visibleUsers'
import { newExecutionRequestGroupId } from '../utils/executionRequestGroup'
import { filterAiPhoneDevices, persistAiPhoneDeviceFilter, readAiPhoneDeviceFilter } from '../utils/deviceFilter'

const store = useQuickSessionStore()
const fmStore = useFunctionMapAssetsStore()
const router = useRouter()
const FM_TARGET_LABEL: Record<FunctionMapTarget, string> = { app: 'App', web: 'Web', api: 'API' }
let pollingTimer: number | undefined
let clockTimer: number | undefined
let importProgressTimer: number | undefined
const nowMs = ref(Date.now())
const workspaceRef = ref<HTMLElement | null>(null)
const contextPinned = ref(false)
const caseScrollTargetId = ref<number | null>(null)
const caseScrollRequestKey = ref(0)
const importFileInput = ref<HTMLInputElement | null>(null)
const dragActive = ref(false)
const WORKSPACE_TOP_GAP = 56
const STICKY_CONTEXT_TRIGGER = 80

const importForm = reactive({
  filename: 'quick-cases.md',
  handoffId: '',
  loading: false,
  error: '',
})

const importProgress = reactive({
  visible: false,
  title: '正在导入 Quick Case',
  filename: '',
  startedAt: 0,
  elapsedSeconds: 0,
  stageIndex: 0,
})

const sessionTools = reactive({
  savingTarget: false,
  requirementLinkError: '',
  targetUrlDraft: '',
  targetStatus: 'idle' as 'idle' | 'checking' | 'ready' | 'failed',
  targetMessage: '',
})

// 快速会话的 Function Map：从资产库选（按引用），不再上传文件
const QUICK_FM_PAGE_SIZE = 20
const quickFm = reactive({
  mounts: [] as FunctionMapAssetListItem[],
  dialogOpen: false,
  search: '',
  results: [] as FunctionMapAssetListItem[],
  total: 0,
  page: 1,
  busy: false,
})
let quickFmSearchTimer: number | undefined
const quickFmTotalPages = computed(
  () => Math.max(1, Math.ceil(quickFm.total / QUICK_FM_PAGE_SIZE)),
)
// 正文浮层（点击眼睛，粘性；chip 与结果卡片共用）
const quickFmTip = ref<{
  id: number
  title: string
  content: string
  x: number
  y: number
  maxHeight: number
} | null>(null)
// 适用场景浮层（悬浮显示完整）
const quickFmDescTip = ref<{ text: string; x: number; y: number } | null>(null)

const editDialog = reactive({
  open: false,
  saving: false,
  error: '',
  caseId: null as number | null,
  suiteTitle: '',
  pathInfo: '',
  rawTitle: '',
  preconditions: '',
  stepsText: '',
  expectedResult: '',
})

const devicePicker = reactive({
  open: false,
  mode: 'single' as 'single' | 'queue',
  loading: false,
  error: '',
  devices: [] as AIPhoneDevice[],
  webDevices: [] as AIPhoneDevice[],
  items: [] as QuickCaseItem[],
  selectedAliases: [] as string[],
  webSelectedAliases: [] as string[],
  deviceFilter: readAiPhoneDeviceFilter(),
  retryMax: 0,
  cacheMode: 'off' as 'off' | 'v1' | 'v2' | 'v3',
})
// 只保留通过关键字筛选的 AI Phone 设备；被过滤掉的不进池、不参与调度（等于未勾选）。
const quickVisibleDevices = computed(() => filterAiPhoneDevices(devicePicker.devices, devicePicker.deviceFilter))
function onQuickDeviceFilterInput(value: string) {
  devicePicker.deviceFilter = value
  persistAiPhoneDeviceFilter(value)
}

const repairDialog = reactive({
  open: false,
  loading: false,
  error: '',
  caseIds: [] as number[],
  items: [] as RepairDraft[],
  applyingDraftId: null as number | null,
  requestId: 0,
})

const bugDialog = reactive({
  open: false,
  loading: false,
  submitting: false,
  imageUploading: false,
  error: '',
  caseId: null as number | null,
  targetUrl: '',
  title: '',
  description: '',
  fields: [] as BugField[],
  resultUrl: '' as string | null,
  hasImage: false,
  keyImage: '' as string | null,
  keyImages: [] as { platform: string; image: string }[],
  excludedImages: [] as string[],
  submittedBugs: [] as SubmittedBug[],
})

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

const bugPrereqDialog = reactive({
  open: false,
  messages: [] as string[],
})

const terminalDialog = reactive({
  open: false,
  action: 'export' as 'export' | 'exit',
  runningAction: '' as '' | 'export' | 'exportExit' | 'exit',
  loading: false,
  error: '',
})

const reportChooser = reactive({
  open: false,
  caseTitle: '',
  results: [] as CasePlatformResult[],
})
const preparingBugDraftIds = ref<Set<number>>(new Set())
const failedBugDraftIds = ref<Set<number>>(new Set())
let bugDraftWarmupRunning = false
let bugDraftWarmupRequested = false

const filteredCases = computed(() => store.filteredCases)
const selectableUsers = computed(() => filterVisibleUsers(store.users))
const currentUser = computed(() => store.users.find((item) => item.id === store.currentUserId))
const quickBugTargetReady = computed(() => {
  const savedTarget = (store.session?.feishuRequirementUrl || '').trim()
  const draftTarget = sessionTools.targetUrlDraft.trim()
  return Boolean(
    savedTarget
    && draftTarget === savedTarget
    && sessionTools.targetStatus === 'ready'
    && !sessionTools.requirementLinkError,
  )
})
const quickBugContextReady = computed(() => quickBugTargetReady.value && Boolean(store.currentUserId || store.session?.currentUserId))
const preparingBugDraftIdList = computed(() => [...preparingBugDraftIds.value])
const terminalDialogCopy = computed(() => {
  if (terminalDialog.action === 'export') {
    return {
      title: '导出 Markdown',
      description: '可以只下载当前 Markdown，也可以下载后结束这个 quick session。',
      primaryText: '导出并退出',
      primaryClass: 'primary',
      points: [
        '导出内容只包含当前 Markdown 用例文本，不包含执行记录、飞书链接和 function 文件。',
        '选择“导出”只下载文件并保留当前工作台，后续还能继续编辑和再次导出。',
        '选择“导出并退出”会在下载成功后清空 session，这个 session ID 不能再接力。',
        '导出后迟到的 AI Phone 回调会被快速模式忽略，不影响标准流程。',
      ],
    }
  }
  return {
      title: '退出快速模式',
      description: '会清空当前 quick session，不会下载 Markdown 文件。',
      primaryText: '确认退出',
    primaryClass: 'danger',
    points: [
      '当前浏览器缓存和服务端 quick session 都会清空。',
      '未导出的 Markdown 修改会丢失，这个 session ID 不能再接力。',
      '已发出的执行任务不会影响标准工作流，后续孤儿回调会被忽略。',
    ],
  }
})
const stickyContextVisible = computed(
  () =>
    contextPinned.value
    && Boolean(store.session)
    && !reportChooser.open
    && !editDialog.open
    && !devicePicker.open
    && !repairDialog.open
    && !bugDialog.open
    && !bugPrereqDialog.open
    && !terminalDialog.open,
)

function handleQuickUserChange(value: number | string | null) {
  if (typeof value === 'number') {
    void store.setCurrentUser(value).then(() => {
      resetBugDraftTracking()
      void warmupBugDrafts()
    })
  }
}

onMounted(() => {
  void store.loadUsers()
  void store.restore().then(() => {
    syncSessionTools()
    void validateSavedFeishuLinks()
  })
  updateScrollY()
  window.addEventListener('scroll', updateScrollY, { passive: true })
  clockTimer = window.setInterval(() => {
    nowMs.value = Date.now()
  }, 1000)
  pollingTimer = window.setInterval(() => {
    if (
      !store.session
      || editDialog.open
      || devicePicker.open
      || repairDialog.open
      || bugDialog.open
      || bugPrereqDialog.open
      || terminalDialog.open
    ) return
    const pending = store.cases.some((item) => item.executionStatus === 'running' || (
      item.executionStatus === 'failed' && item.reportUrl && (!item.diagnosisReady || !item.bugDraftReady)
    ))
    if (pending) {
      void store.loadSession().then(() => {
        void warmupBugDrafts()
      })
    }
  }, 5000)
})

onUnmounted(() => {
  window.removeEventListener('scroll', updateScrollY)
  if (clockTimer) window.clearInterval(clockTimer)
  if (pollingTimer) window.clearInterval(pollingTimer)
  stopImportProgress()
})

function syncSessionTools() {
  sessionTools.targetUrlDraft = store.session?.feishuRequirementUrl || ''
  void loadQuickFmMounts()
}

const quickSessionId = computed(() => store.session?.sessionId || '')

async function loadQuickFmMounts() {
  const sid = quickSessionId.value
  if (!sid) {
    quickFm.mounts = []
    return
  }
  try {
    quickFm.mounts = await fmStore.listQuickMounts(sid)
  } catch {
    quickFm.mounts = []
  }
}

const quickFmMountedIds = computed(() => new Set(quickFm.mounts.map((m) => m.id)))

function openQuickFmDialog() {
  if (!quickSessionId.value) {
    return
  }
  quickFm.dialogOpen = true
  quickFm.search = ''
  quickFmTip.value = null
  quickFmDescTip.value = null
  void runQuickFmSearch(1)
}

function closeQuickFmDialog() {
  quickFm.dialogOpen = false
  quickFmTip.value = null
  quickFmDescTip.value = null
}

function goToFunctionMapAssets() {
  void router.push({ name: 'function-maps' })
}

function onQuickFmSearchInput() {
  window.clearTimeout(quickFmSearchTimer)
  quickFmSearchTimer = window.setTimeout(() => void runQuickFmSearch(1), 300)
}

async function runQuickFmSearch(page: number) {
  try {
    const result = await fmStore.searchAssets(quickFm.search, page, QUICK_FM_PAGE_SIZE)
    quickFm.results = result.items
    quickFm.total = result.total
    quickFm.page = result.page
  } catch {
    quickFm.results = []
    quickFm.total = 0
    quickFm.page = 1
  }
}

function goQuickFmPage(delta: number) {
  const next = quickFm.page + delta
  if (next < 1 || next > quickFmTotalPages.value) {
    return
  }
  quickFmTip.value = null
  void runQuickFmSearch(next)
}

async function toggleQuickFmTip(item: FunctionMapAssetListItem, ev: MouseEvent) {
  if (quickFmTip.value?.id === item.id) {
    quickFmTip.value = null
    return
  }
  quickFmDescTip.value = null
  const anchor = ev.currentTarget as HTMLElement | null
  if (!anchor) {
    return
  }
  const rect = anchor.getBoundingClientRect()
  const pad = 12
  const width = 380
  const maxHeight = Math.round(window.innerHeight * 0.5)
  let x = rect.right - width
  if (x + width > window.innerWidth - pad) {
    x = window.innerWidth - width - pad
  }
  if (x < pad) {
    x = pad
  }
  let y = rect.bottom + 6
  if (y + maxHeight > window.innerHeight - pad) {
    y = Math.max(pad, rect.top - maxHeight - 6)
  }
  quickFmTip.value = { id: item.id, title: item.title, content: '加载中…', x, y, maxHeight }
  try {
    const full = await fmStore.getAsset(item.id)
    if (quickFmTip.value?.id === item.id) {
      quickFmTip.value = { ...quickFmTip.value, content: full.content }
    }
  } catch {
    if (quickFmTip.value?.id === item.id) {
      quickFmTip.value = { ...quickFmTip.value, content: '正文加载失败' }
    }
  }
}

function closeQuickFmTip() {
  quickFmTip.value = null
}

function showQuickFmDescTip(text: string, ev: MouseEvent) {
  const anchor = ev.currentTarget as HTMLElement | null
  if (!anchor || !text.trim()) {
    return
  }
  if (anchor.scrollHeight <= anchor.clientHeight && anchor.scrollWidth <= anchor.clientWidth) {
    return
  }
  const rect = anchor.getBoundingClientRect()
  const pad = 12
  const width = 340
  let x = rect.left
  if (x + width > window.innerWidth - pad) {
    x = window.innerWidth - width - pad
  }
  if (x < pad) {
    x = pad
  }
  quickFmDescTip.value = { text, x, y: rect.bottom + 6 }
}

function hideQuickFmDescTip() {
  quickFmDescTip.value = null
}

async function addQuickFm(assetId: number) {
  const sid = quickSessionId.value
  if (!sid || quickFm.busy) {
    return
  }
  quickFm.busy = true
  try {
    quickFm.mounts = await fmStore.mountToQuick(sid, assetId)
  } catch (error) {
    store.error = readableError(error, '挂载 Function Map 失败')
  } finally {
    quickFm.busy = false
  }
}

async function removeQuickFm(assetId: number) {
  const sid = quickSessionId.value
  if (!sid || quickFm.busy) {
    return
  }
  quickFm.busy = true
  try {
    quickFm.mounts = await fmStore.unmountFromQuick(sid, assetId)
  } catch (error) {
    store.error = readableError(error, '移除 Function Map 失败')
  } finally {
    quickFm.busy = false
  }
}

function resetBugDraftTracking() {
  preparingBugDraftIds.value = new Set()
  failedBugDraftIds.value = new Set()
  bugDraftWarmupRequested = true
}

function setBugDraftPreparing(caseId: number, preparing: boolean) {
  const next = new Set(preparingBugDraftIds.value)
  if (preparing) next.add(caseId)
  else next.delete(caseId)
  preparingBugDraftIds.value = next
}

function markBugDraftFailed(caseId: number) {
  const next = new Set(failedBugDraftIds.value)
  next.add(caseId)
  failedBugDraftIds.value = next
}

function bugCount(item: QuickCaseItem): number {
  return item.bugs?.length ?? 0
}

function bugDraftWarmupCandidates(): QuickCaseItem[] {
  if (!quickBugContextReady.value) return []
  const preparing = preparingBugDraftIds.value
  const failed = failedBugDraftIds.value
  return store.cases.filter((item) => (
    item.executionStatus === 'failed'
    && Boolean(item.reportUrl)
    && Boolean(item.diagnosisReady)
    && !item.bugDraftReady
    && bugCount(item) === 0
    && !preparing.has(item.id)
    && !failed.has(item.id)
  ))
}

async function warmupBugDrafts() {
  if (!quickBugContextReady.value) return
  if (bugDraftWarmupRunning) {
    bugDraftWarmupRequested = true
    return
  }
  bugDraftWarmupRunning = true
  try {
    do {
      bugDraftWarmupRequested = false
      const candidates = bugDraftWarmupCandidates()
      if (!candidates.length) break
      candidates.forEach((item) => setBugDraftPreparing(item.id, true))
      await Promise.allSettled(candidates.map(async (item) => {
        try {
          await store.getBugDraft(item.id)
        } catch {
          markBugDraftFailed(item.id)
        } finally {
          setBugDraftPreparing(item.id, false)
        }
      }))
      await store.loadSession()
    } while (bugDraftWarmupRequested && quickBugContextReady.value)
  } finally {
    bugDraftWarmupRunning = false
  }
}

function updateScrollY() {
  contextPinned.value = window.scrollY > STICKY_CONTEXT_TRIGGER
}

function scrollWorkspaceIntoView() {
  const target = workspaceRef.value
  if (!target) return
  const rect = target.getBoundingClientRect()
  if (rect.top <= WORKSPACE_TOP_GAP) return
  window.scrollTo({
    top: Math.max(0, rect.top + window.scrollY - WORKSPACE_TOP_GAP),
    behavior: 'smooth',
  })
}

async function importMarkdownFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  await importMarkdownFileObject(file)
  input.value = ''
}

function openImportPicker() {
  if (importForm.loading) return
  importFileInput.value?.click()
}

function handleDragEnter() {
  if (!importForm.loading) dragActive.value = true
}

function handleDragLeave(event: DragEvent) {
  const current = event.currentTarget as HTMLElement | null
  const related = event.relatedTarget as Node | null
  if (current && related && current.contains(related)) return
  dragActive.value = false
}

async function dropMarkdownFile(event: DragEvent) {
  dragActive.value = false
  const file = event.dataTransfer?.files?.[0]
  if (!file) return
  await importMarkdownFileObject(file)
}

async function importMarkdownFileObject(file: File) {
  importForm.error = ''
  if (!/\.(md|markdown)$/i.test(file.name)) {
    importForm.error = '请上传 .md 格式的 Markdown 测试用例。'
    return
  }
  importForm.filename = file.name
  const content = await file.text()
  await importMarkdownContent(file.name, content)
}

async function importMarkdownContent(filename: string, content: string) {
  importForm.loading = true
  importForm.error = ''
  startImportProgress(filename || 'quick-cases.md')
  try {
    await store.importMarkdown({
      filename: filename || 'quick-cases.md',
      content,
      functionFiles: [],
    })
    syncSessionTools()
  } catch (error) {
    importForm.error = readableError(error, '导入失败')
  } finally {
    importForm.loading = false
    stopImportProgress()
  }
}

function startImportProgress(filename: string) {
  stopImportProgress()
  importProgress.title = '正在导入 Quick Case'
  importProgress.filename = filename
  importProgress.startedAt = Date.now()
  importProgress.elapsedSeconds = 0
  importProgress.stageIndex = 0
  importProgress.visible = true
  importProgressTimer = window.setInterval(() => {
    updateImportProgress()
  }, 1000)
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
  if (importProgressTimer) {
    window.clearInterval(importProgressTimer)
    importProgressTimer = undefined
  }
  importProgress.visible = false
  importProgress.startedAt = 0
  importProgress.elapsedSeconds = 0
  importProgress.stageIndex = 0
}

async function openHandoffSession() {
  if (!importForm.handoffId.trim()) return
  importForm.loading = true
  importForm.error = ''
  try {
    await store.loadSession(importForm.handoffId.trim())
    syncSessionTools()
  } catch (error) {
    importForm.error = readableError(error, '接力失败')
  } finally {
    importForm.loading = false
  }
}

async function saveFeishuTarget() {
  sessionTools.savingTarget = true
  sessionTools.requirementLinkError = ''
  sessionTools.targetStatus = 'checking'
  sessionTools.targetMessage = '正在读取需求并校验空间配置…'
  try {
    const nextRequirementUrl = sessionTools.targetUrlDraft.trim()
    if (!nextRequirementUrl) {
      const currentRequirementUrl = store.session?.feishuRequirementUrl || ''
      if (currentRequirementUrl) {
        await store.patchSession({ feishuRequirementUrl: '' })
      }
      syncSessionTools()
      sessionTools.targetStatus = 'idle'
      sessionTools.targetMessage = ''
      resetBugDraftTracking()
      return
    }
    const result = await store.bindFeishuTarget(nextRequirementUrl)
    syncSessionTools()
    sessionTools.targetStatus = result.readable ? 'ready' : 'failed'
    sessionTools.targetMessage = result.message || (result.readable ? '需求已读取，可提交 bug。' : '当前需求链接不可用于提交 bug。')
    sessionTools.requirementLinkError = result.readable ? '' : sessionTools.targetMessage
    resetBugDraftTracking()
    if (result.readable) void warmupBugDrafts()
  } catch (error) {
    sessionTools.targetStatus = 'failed'
    sessionTools.targetMessage = readableError(error, '当前需求链接不可用于提交 bug')
    sessionTools.requirementLinkError = sessionTools.targetMessage
  } finally {
    sessionTools.savingTarget = false
  }
}

async function validateSavedFeishuLinks() {
  const requirementUrl = (store.session?.feishuRequirementUrl || '').trim()
  sessionTools.requirementLinkError = ''
  if (!requirementUrl) {
    sessionTools.targetStatus = 'idle'
    sessionTools.targetMessage = ''
    return
  }
  sessionTools.targetStatus = 'checking'
  sessionTools.targetMessage = '正在校验需求链接…'
  try {
    const result = await store.checkFeishuLink(requirementUrl, 'requirement')
    sessionTools.targetStatus = result.readable ? 'ready' : 'failed'
    sessionTools.targetMessage = result.message || (result.readable ? '需求已读取，可提交 bug。' : '当前需求链接不可用于提交 bug。')
    sessionTools.requirementLinkError = result.readable ? '' : sessionTools.targetMessage
    if (result.readable) void warmupBugDrafts()
  } catch (error) {
    sessionTools.targetStatus = 'failed'
    sessionTools.targetMessage = readableError(error, '当前飞书需求链接无法解析')
    sessionTools.requirementLinkError = sessionTools.targetMessage
  }
}

function openTerminalDialog(action: 'export' | 'exit') {
  terminalDialog.open = true
  terminalDialog.action = action
  terminalDialog.runningAction = ''
  terminalDialog.loading = false
  terminalDialog.error = ''
}

function closeTerminalDialog() {
  if (terminalDialog.loading) return
  terminalDialog.open = false
  terminalDialog.error = ''
}

async function confirmTerminalDialog() {
  if (terminalDialog.loading) return
  terminalDialog.loading = true
  terminalDialog.runningAction = terminalDialog.action === 'export' ? 'exportExit' : 'exit'
  terminalDialog.error = ''
  try {
    if (terminalDialog.action === 'export') {
      await downloadMarkdown({ clear: true })
    } else {
      await store.clearRemote()
      syncSessionTools()
    }
    terminalDialog.open = false
  } catch (error) {
    terminalDialog.error = readableError(error, terminalDialog.action === 'export' ? '导出失败' : '退出失败')
  } finally {
    terminalDialog.loading = false
    terminalDialog.runningAction = ''
  }
}

async function exportMarkdownOnly() {
  if (terminalDialog.loading) return
  terminalDialog.loading = true
  terminalDialog.runningAction = 'export'
  terminalDialog.error = ''
  try {
    await downloadMarkdown({ clear: false })
    terminalDialog.open = false
  } catch (error) {
    terminalDialog.error = readableError(error, '导出失败')
  } finally {
    terminalDialog.loading = false
    terminalDialog.runningAction = ''
  }
}

async function downloadMarkdown(options: { clear: boolean }) {
  try {
    const result = await store.exportMarkdown({ clear: options.clear })
    const blob = new Blob([result.content], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = result.filename
    anchor.click()
    URL.revokeObjectURL(url)
    if (!result.cleared) await store.loadSession()
    syncSessionTools()
  } catch (error) {
    throw new Error(readableError(error, '导出失败'))
  }
}

function selectCase(caseId: number) {
  store.selectCase(caseId)
}

async function selectCaseFromMindMap(caseId: number) {
  store.selectCase(caseId)
  caseScrollTargetId.value = caseId
  caseScrollRequestKey.value += 1
  await nextTick()
  window.requestAnimationFrame(() => scrollWorkspaceIntoView())
}

function updateStatus(caseId: number, status: ExecutionStatus) {
  void store.updateCaseStatus(caseId, status)
}

function updateTarget(caseId: number, target: ExecutionTarget) {
  void store.updateCaseTarget(caseId, target)
}

async function executeCase(caseId: number) {
  const item = store.cases.find((candidate) => candidate.id === caseId)
  if (!item) return
  if (item.executionTarget === 'app') {
    void openDevicePicker([item], 'single')
  } else if (item.executionTarget === 'web') {
    void openDevicePicker([item], 'single')
  } else if (item.executionTarget === 'api') {
    try {
      await store.submitAIAPICases({
        sessionId: store.sessionId,
        caseIds: [caseId],
        submissionName: `Case Flow Quick API 单条执行 ${item.displayNo || item.ordinal}. ${item.rawTitle}`,
      })
    } catch (error) {
      store.error = readableError(error, '提交 AI API 执行失败')
    }
  } else if (item.executionTarget === 'mixed') {
    try {
      await store.submitHybridCases({
        sessionId: store.sessionId,
        caseIds: [caseId],
        submissionName: `Case Flow Quick 混合单条执行 ${item.displayNo || item.ordinal}. ${item.rawTitle}`,
      })
    } catch (error) {
      store.error = readableError(error, '提交 AI Hybrid 执行失败')
    }
  } else {
    void store.updateCaseStatus(caseId, 'running')
  }
}

function executeSelected() {
  const selected = store.selectedVisibleCases
  if (!selected.length) return
  void openDevicePicker(selected, 'queue')
}

function stopSelected() {
  const runningIds = store.selectedVisibleCases.filter((item) => item.executionStatus === 'running').map((item) => item.id)
  if (runningIds.length) void store.updateCasesStatus(runningIds, 'not_run')
}

function repairSelected() {
  const failedIds = store.selectedVisibleCases.filter((item) => item.executionStatus === 'failed').map((item) => item.id)
  void openRepairDialog(failedIds)
}

function repairCase(caseId: number) {
  void openRepairDialog([caseId])
}

async function openDevicePicker(items: QuickCaseItem[], mode: 'single' | 'queue') {
  devicePicker.open = true
  devicePicker.mode = mode
  devicePicker.items = [...items]
  devicePicker.retryMax = 0
  devicePicker.cacheMode = 'off'
  devicePicker.devices = []
  devicePicker.webDevices = []
  devicePicker.selectedAliases = []
  devicePicker.webSelectedAliases = []
  const loaders: Promise<void>[] = []
  if (items.some((item) => item.executionTarget === 'app')) {
    loaders.push(loadDevicePickerDevices())
  }
  if (items.some((item) => item.executionTarget === 'web')) {
    loaders.push(loadDevicePickerWebDevices())
  }
  if (loaders.length) {
    await Promise.all(loaders)
  } else {
    devicePicker.loading = false
    devicePicker.error = ''
  }
}

async function loadDevicePickerDevices() {
  devicePicker.loading = true
  devicePicker.error = ''
  devicePicker.devices = []
  devicePicker.selectedAliases = []
  try {
    const result = await store.listAIPhoneDevices()
    devicePicker.devices = result.devices
    devicePicker.selectedAliases = result.devices.map(deviceAlias).filter(Boolean)
    devicePicker.error = result.error || ''
  } catch (error) {
    devicePicker.error = readableError(error, '加载设备失败')
  } finally {
    devicePicker.loading = false
  }
}

async function loadDevicePickerWebDevices() {
  devicePicker.loading = true
  devicePicker.error = ''
  devicePicker.webDevices = []
  devicePicker.webSelectedAliases = []
  try {
    const result = await store.listAIWebDevices()
    devicePicker.webDevices = result.devices
    devicePicker.webSelectedAliases = result.devices.map(deviceAlias).filter(Boolean)
    devicePicker.error = result.error || ''
  } catch (error) {
    devicePicker.error = readableError(error, '加载浏览器槽失败')
  } finally {
    devicePicker.loading = false
  }
}

function closeDevicePicker() {
  devicePicker.open = false
  devicePicker.mode = 'single'
  devicePicker.loading = false
  devicePicker.error = ''
  devicePicker.devices = []
  devicePicker.webDevices = []
  devicePicker.items = []
  devicePicker.selectedAliases = []
  devicePicker.webSelectedAliases = []
  devicePicker.retryMax = 0
  devicePicker.cacheMode = 'off'
}

async function confirmDevicePicker() {
  const appItems = devicePicker.items.filter((item) => item.executionTarget === 'app')
  const webItems = devicePicker.items.filter((item) => item.executionTarget === 'web')
  const apiItems = devicePicker.items.filter((item) => item.executionTarget === 'api')
  const mixedItems = devicePicker.items.filter((item) => item.executionTarget === 'mixed')
  const manualItems = devicePicker.items.filter((item) => !['app', 'web', 'api', 'mixed'].includes(item.executionTarget))
  devicePicker.loading = true
  devicePicker.error = ''
  const groupId = newExecutionRequestGroupId()
  try {
    if (appItems.length) {
      const pools = selectedDeviceAliasPools(quickVisibleDevices.value, devicePicker.selectedAliases)
      if (!Object.keys(pools).length) {
        devicePicker.error = 'AI Phone 执行需要至少选择一台在线设备（注意当前设备筛选）。'
        return
      }
      await store.submitAIPhoneCases({
        sessionId: store.sessionId,
        caseIds: appItems.map((item) => item.id),
        deviceAliasPools: pools,
        submissionName: `Case Flow Quick 批量执行 ${new Date().toLocaleString('zh-CN', { hour12: false })}`,
        cacheMode: devicePicker.cacheMode,
        retryMax: devicePicker.retryMax,
        executionRequestGroupId: groupId,
      })
    }
    if (webItems.length) {
      const pools = selectedDeviceAliasPools(devicePicker.webDevices, devicePicker.webSelectedAliases, 'chrome')
      if (!Object.keys(pools).length) {
        devicePicker.error = 'AI Web 执行需要至少选择一个浏览器槽。'
        return
      }
      await store.submitAIWebCases({
        sessionId: store.sessionId,
        caseIds: webItems.map((item) => item.id),
        deviceAliasPools: pools,
        submissionName: `Case Flow Quick Web 批量执行 ${new Date().toLocaleString('zh-CN', { hour12: false })}`,
        cacheMode: 'off',
        retryMax: 0,
        executionRequestGroupId: groupId,
      })
    }
    if (apiItems.length) {
      await store.submitAIAPICases({
        sessionId: store.sessionId,
        caseIds: apiItems.map((item) => item.id),
        submissionName: `Case Flow Quick API 批量执行 ${new Date().toLocaleString('zh-CN', { hour12: false })}`,
        executionRequestGroupId: groupId,
      })
    }
    if (mixedItems.length) {
      await store.submitHybridCases({
        sessionId: store.sessionId,
        caseIds: mixedItems.map((item) => item.id),
        submissionName: `Case Flow Quick 混合批量执行 ${new Date().toLocaleString('zh-CN', { hour12: false })}`,
        executionRequestGroupId: groupId,
      })
    }
    if (manualItems.length) {
      await store.updateCasesStatus(manualItems.map((item) => item.id), 'running')
    }
    closeDevicePicker()
  } catch (error) {
    devicePicker.error = readableError(error, '提交执行失败')
  } finally {
    devicePicker.loading = false
  }
}

function reorderDevicePickerItems(items: QuickCaseItem[]) {
  devicePicker.items = [...items]
}

async function executeSingleDevice(device: AIPhoneDevice) {
  const alias = deviceAlias(device)
  if (!alias) {
    devicePicker.error = '设备缺少 alias 或 serial，无法提交。'
    return
  }
  devicePicker.loading = true
  devicePicker.error = ''
  try {
    const target = devicePicker.items[0]?.executionTarget
    const platform = normalizePlatform(device.platform || (target === 'web' ? 'chrome' : 'android'))
    const payload = {
      sessionId: store.sessionId,
      caseIds: devicePicker.items.map((item) => item.id),
      deviceAliasPools: { [platform]: [alias] },
      submissionName: `Case Flow Quick 单条执行 ${devicePicker.items[0]?.displayNo || devicePicker.items[0]?.ordinal || ''}. ${devicePicker.items[0]?.rawTitle || ''}`,
      cacheMode: devicePicker.cacheMode,
      retryMax: devicePicker.retryMax,
    }
    if (target === 'web') {
      await store.submitAIWebCases({
        ...payload,
        submissionName: `Case Flow Quick Web 单条执行 ${devicePicker.items[0]?.displayNo || devicePicker.items[0]?.ordinal || ''}. ${devicePicker.items[0]?.rawTitle || ''}`,
      })
    } else {
      await store.submitAIPhoneCases(payload)
    }
    closeDevicePicker()
  } catch (error) {
    devicePicker.error = readableError(error, '提交执行失败')
  } finally {
    devicePicker.loading = false
  }
}

async function openRepairDialog(caseIds: number[]) {
  if (!caseIds.length) return
  const requestId = repairDialog.requestId + 1
  repairDialog.requestId = requestId
  repairDialog.open = true
  repairDialog.loading = true
  repairDialog.error = ''
  repairDialog.caseIds = [...caseIds]
  repairDialog.items = []
  try {
    const result = await store.previewRepairs(caseIds)
    if (repairDialog.requestId !== requestId) return
    repairDialog.items = result.items
  } catch (error) {
    if (repairDialog.requestId !== requestId) return
    repairDialog.error = readableError(error, '诊断修复失败')
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
  if (!item.draftId || !repairGateCanRepair(item)) return
  repairDialog.applyingDraftId = item.draftId
  repairDialog.error = ''
  try {
    await store.applyRepairDraft(item.draftId, {
      stepsText: item.proposedSteps,
      preconditions: item.proposedPreconditions,
      expectedResult: item.proposedExpected,
    })
    repairDialog.items = repairDialog.items.filter((candidate) => candidate.draftId !== item.draftId)
    if (!repairDialog.items.length) closeRepairDialog()
  } catch (error) {
    repairDialog.error = readableError(error, '应用修复失败')
  } finally {
    repairDialog.applyingDraftId = null
  }
}

function skipRepairItem(caseId: number) {
  repairDialog.items = repairDialog.items.filter((candidate) => candidate.caseId !== caseId)
  if (!repairDialog.items.length) closeRepairDialog()
}

function repairGateCanRepair(item: RepairDraft) {
  const gate = item.gate as { canRepair?: unknown; can_repair?: unknown; allowed?: unknown } | null
  if (typeof gate?.canRepair === 'boolean') return gate.canRepair
  if (typeof gate?.can_repair === 'boolean') return gate.can_repair
  if (typeof gate?.allowed === 'boolean') return gate.allowed
  return item.repairable
}

function editCase(caseId: number) {
  const item = store.cases.find((candidate) => candidate.id === caseId)
  if (!item || !store.session) return
  editDialog.open = true
  editDialog.error = ''
  editDialog.caseId = item.id
  editDialog.suiteTitle = item.suiteTitle || store.session.suiteTitle
  editDialog.pathInfo = quickCasePathInfo(item)
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
  editDialog.suiteTitle = ''
  editDialog.pathInfo = ''
  editDialog.rawTitle = ''
  editDialog.preconditions = ''
  editDialog.stepsText = ''
  editDialog.expectedResult = ''
}

async function saveEditDialog() {
  if (!editDialog.caseId) return
  editDialog.saving = true
  editDialog.error = ''
  try {
    await store.updateCase(editDialog.caseId, {
      rawTitle: editDialog.rawTitle,
      preconditions: editDialog.preconditions,
      stepsText: editDialog.stepsText,
      expectedResult: editDialog.expectedResult,
    })
    closeEditDialog()
  } catch (error) {
    editDialog.error = readableError(error, '保存失败')
  } finally {
    editDialog.saving = false
  }
}

function quickCasePathInfo(item: QuickCaseItem) {
  const parts = [
    item.suiteTitle || store.session?.suiteTitle || '',
    ...(item.pathNodes || [])
      .map((node) => String(node.displayText || node.rawText || '').trim())
      .filter(Boolean),
  ].filter(Boolean)
  return parts.length ? parts.join(' / ') : '无路径信息'
}

async function openBugDialog(caseId: number) {
  const missing = bugPrerequisiteMessages()
  if (missing.length) {
    bugPrereqDialog.messages = missing
    bugPrereqDialog.open = true
    return
  }
  bugDialog.open = true
  bugDialog.loading = true
  bugDialog.submitting = false
  bugDialog.imageUploading = false
  bugDialog.error = ''
  bugDialog.resultUrl = ''
  bugDialog.caseId = caseId
  bugDialog.targetUrl = store.session?.feishuRequirementUrl?.trim() || ''
  bugDialog.title = ''
  bugDialog.description = ''
  bugDialog.fields = []
  bugDialog.hasImage = false
  bugDialog.keyImage = ''
  bugDialog.keyImages = []
  bugDialog.excludedImages = []
  bugDialog.submittedBugs = []
  try {
    const draft = await store.getBugDraft(caseId)
    bugDialog.title = draft.title
    bugDialog.description = draft.description
    bugDialog.fields = draft.fields
    bugDialog.hasImage = draft.hasDiagnosisImage
    bugDialog.keyImage = draft.keyImage ?? ''
    bugDialog.keyImages = bugDraftImages(draft)
    bugDialog.submittedBugs = draft.submittedBugs ?? []
  } catch (error) {
    bugDialog.error = readableError(error, '生成 bug 草稿失败')
  } finally {
    bugDialog.loading = false
  }
}

function bugPrerequisiteMessages() {
  const messages: string[] = []
  const userId = store.currentUserId || store.session?.currentUserId || 0
  const savedTarget = (store.session?.feishuRequirementUrl || '').trim()
  const draftTarget = sessionTools.targetUrlDraft.trim()
  if (!userId) {
    messages.push('请先在右上角选择提交人。')
  }
  if (!savedTarget) {
    messages.push(
      draftTarget
        ? '飞书需求链接已填写但还未保存，请先点击顶部“保存链接”完成绑定。'
        : '请先在顶部填写飞书需求链接，并点击“保存链接”完成绑定。',
    )
  } else if (draftTarget && draftTarget !== savedTarget) {
    messages.push('顶部飞书需求链接有未保存变更，请先点击“保存链接”。')
  } else if (sessionTools.requirementLinkError) {
    messages.push('当前飞书需求链接无法解析，请先修正后重新保存。')
  } else if (sessionTools.targetStatus === 'checking') {
    messages.push('飞书需求链接正在解析，请稍后再提交 bug。')
  }
  return messages
}

function closeBugPrereqDialog() {
  bugPrereqDialog.open = false
  bugPrereqDialog.messages = []
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

function setBugFieldSelected(field: BugField, value: string | string[] | null) {
  field.selected = value
}

function toggleKeyImage(image: string) {
  const idx = bugDialog.excludedImages.indexOf(image)
  if (idx >= 0) bugDialog.excludedImages.splice(idx, 1)
  else bugDialog.excludedImages.push(image)
}

async function uploadBugImages(files: File[]) {
  const imageFiles = files.filter((file) => file.type.startsWith('image/'))
  if (!imageFiles.length) return
  bugDialog.imageUploading = true
  bugDialog.error = ''
  try {
    const uploaded = await store.uploadBugImages(imageFiles)
    bugDialog.keyImages = mergedBugImages(bugDialog.keyImages, uploaded)
    bugDialog.hasImage = bugDialog.keyImages.length > 0
  } catch (error) {
    bugDialog.error = readableError(error, '上传图片失败')
  } finally {
    bugDialog.imageUploading = false
  }
}

async function submitBugDialog() {
  if (!bugDialog.caseId) return
  if (!bugDialog.title.trim()) {
    bugDialog.error = '标题不能为空'
    return
  }
  bugDialog.submitting = true
  bugDialog.error = ''
  const selectedKeyImages = bugDialog.keyImages.filter((item) => !bugDialog.excludedImages.includes(item.image))
  try {
    const result = await store.submitBug(bugDialog.caseId, {
      title: bugDialog.title,
      description: bugDialog.description,
      fields: bugDialog.fields,
      keyImages: selectedKeyImages,
    })
    bugDialog.hasImage = selectedKeyImages.length > 0
    bugDialog.resultUrl = result.bugUrl
    bugDialog.submittedBugs = [...bugDialog.submittedBugs, { url: result.bugUrl, id: String(result.bugId) }]
    const repairItem = repairDialog.items.find((entry) => entry.caseId === bugDialog.caseId)
    if (repairItem) repairItem.bugUrl = result.bugUrl
  } catch (error) {
    bugDialog.error = readableError(error, '提交 bug 失败')
  } finally {
    bugDialog.submitting = false
  }
}

function submitAnotherBug() {
  bugDialog.resultUrl = ''
  bugDialog.excludedImages = []
}

async function openReport(caseId: number) {
  const item = store.cases.find((candidate) => candidate.id === caseId)
  let results: CasePlatformResult[] = []
  try {
    results = await store.getCasePlatformResults(caseId)
  } catch {
    results = []
  }
  const withReports = results.filter((result) => !!result.reportUrl)
  if (withReports.length > 1) {
    reportChooser.caseTitle = item ? `${item.displayNo || item.ordinal}. ${item.rawTitle}` : ''
    reportChooser.results = withReports
    reportChooser.open = true
    return
  }
  const url = withReports[0]?.reportUrl || item?.reportUrl
  if (url) window.open(url, '_blank', 'noopener')
}

const platformResultLabels: Record<string, string> = {
  android: 'Android',
  ios: 'iOS',
  harmony: 'Harmony',
  chrome: 'Chrome',
  safari: 'Safari',
  webkit: 'Safari',
  firefox: 'Firefox',
}

function platformResultTitle(result: CasePlatformResult) {
  const name = platformResultLabels[result.platform] || result.platform
  const stateText = result.state === 'passed' ? '通过' : result.state === 'failed' ? '失败' : '未执行'
  return `${name} · ${stateText}`
}

function openReportLink(result: CasePlatformResult) {
  if (result.reportUrl) window.open(result.reportUrl, '_blank', 'noopener')
}

function closeReportChooser() {
  reportChooser.open = false
  reportChooser.caseTitle = ''
  reportChooser.results = []
}

function setPlatformDevices(platform: string, checked: boolean) {
  const selected = new Set(devicePicker.selectedAliases)
  for (const device of quickVisibleDevices.value.filter((item) => String(item.platform || 'unknown').toLowerCase() === platform)) {
    const alias = deviceAlias(device)
    if (!alias) continue
    if (checked) selected.add(alias)
    else selected.delete(alias)
  }
  devicePicker.selectedAliases = [...selected]
}

function setWebPlatformDevices(platform: string, checked: boolean) {
  const selected = new Set(devicePicker.webSelectedAliases)
  for (const device of devicePicker.webDevices.filter((item) => String(item.platform || 'unknown').toLowerCase() === platform)) {
    const alias = deviceAlias(device)
    if (!alias) continue
    if (checked) selected.add(alias)
    else selected.delete(alias)
  }
  devicePicker.webSelectedAliases = [...selected]
}

function toggleDevice(alias: string, checked: boolean) {
  if (!alias) return
  const selected = new Set(devicePicker.selectedAliases)
  if (checked) selected.add(alias)
  else selected.delete(alias)
  devicePicker.selectedAliases = [...selected]
}

function toggleWebDevice(alias: string, checked: boolean) {
  if (!alias) return
  const selected = new Set(devicePicker.webSelectedAliases)
  if (checked) selected.add(alias)
  else selected.delete(alias)
  devicePicker.webSelectedAliases = [...selected]
}

function deviceAlias(device: AIPhoneDevice) {
  return String(device.alias || device.serial || '').trim()
}

function selectedDeviceAliasPools(devices: AIPhoneDevice[], aliases: string[], fallbackPlatform = 'android') {
  const selected = new Set(aliases)
  return devices.reduce<Record<string, string[]>>((pools, device) => {
    const alias = deviceAlias(device)
    if (!alias || !selected.has(alias)) return pools
    const platform = normalizePlatform(device.platform || fallbackPlatform)
    pools[platform] = pools[platform] || []
    pools[platform].push(alias)
    return pools
  }, {})
}

function normalizePlatform(platform: unknown) {
  const value = String(platform || '').toLowerCase()
  if (value === 'webkit') return 'safari'
  if (value === 'chromium') return 'chrome'
  return value || 'unknown'
}

function readableError(error: unknown, fallback: string) {
  const detail = (error as { detail?: unknown })?.detail
  if (Array.isArray(detail)) return detail.join('；')
  if (typeof detail === 'string') return detail
  if (error instanceof Error) return error.message || fallback
  return fallback
}
</script>

<template>
  <div class="quick-page">
    <ImportProgressOverlay
      :open="importProgress.visible"
      variant="quick"
      :title="importProgress.title"
      :filename="importProgress.filename"
      :elapsed-seconds="importProgress.elapsedSeconds"
      :stage-index="importProgress.stageIndex"
    />

    <section v-if="!store.session" class="quick-import-screen">
      <input
        ref="importFileInput"
        class="quick-file-input"
        type="file"
        accept=".md,.markdown,text/markdown,text/plain"
        @change="importMarkdownFile"
      />
      <div class="quick-import-core">
        <button
          type="button"
          class="quick-dropzone"
          :class="{ 'is-dragging': dragActive, 'is-loading': importForm.loading }"
          :disabled="importForm.loading"
          @click="openImportPicker"
          @dragenter.prevent="handleDragEnter"
          @dragover.prevent="handleDragEnter"
          @dragleave.prevent="handleDragLeave"
          @drop.prevent="dropMarkdownFile"
        >
          <span class="quick-dropzone-mark">Case Flow</span>
          <strong>{{ importForm.loading ? '正在解析 Markdown' : '请上传或拖入 MD 测试用例' }}</strong>
          <em>{{ importForm.loading ? importForm.filename : 'AI 执行 · 快速打磨 · 并发能力' }}</em>
        </button>
        <p v-if="importForm.error" class="quick-import-error">{{ importForm.error }}</p>
      </div>
      <form class="quick-handoff-bar" @submit.prevent="openHandoffSession">
        <span>Session 接力</span>
        <input v-model="importForm.handoffId" type="text" placeholder="输入 session ID" />
        <button type="submit" :disabled="importForm.loading || !importForm.handoffId.trim()">进入</button>
        <RouterLink class="quick-handoff-standard-link" to="/">回到标准模式</RouterLink>
      </form>
    </section>

    <template v-else>
      <header class="quick-topbar">
        <div class="quick-title-block">
          <strong>{{ store.session.suiteTitle }}</strong>
          <span>Session {{ store.session.sessionId }}</span>
        </div>
        <div class="quick-topbar-actions">
          <label class="quick-user-control">
            <span>当前用户</span>
            <UserSearchSelect
              class="quick-user-select"
              :model-value="store.currentUserId"
              :users="selectableUsers"
              placeholder="请选择"
              aria-label="选择快速模式当前用户"
              @update:model-value="handleQuickUserChange"
            />
          </label>
          <button type="button" @click="openTerminalDialog('export')">导出 Markdown</button>
          <button type="button" class="ghost-danger" @click="openTerminalDialog('exit')">退出</button>
        </div>
      </header>

      <section class="quick-session-strip">
        <label class="quick-target-control">
          <span>飞书需求链接</span>
          <em
            v-if="sessionTools.targetMessage"
            class="quick-link-status"
            :class="sessionTools.targetStatus"
          >
            {{ sessionTools.targetMessage }}
          </em>
          <input v-model="sessionTools.targetUrlDraft" type="url" placeholder="粘贴需求详情页链接，保存后可提交 bug" />
        </label>
        <button type="button" :disabled="sessionTools.savingTarget" @click="saveFeishuTarget">
          {{ sessionTools.savingTarget ? '保存中…' : '保存链接' }}
        </button>
        <label class="quick-fm-control">
          <span>Function Map</span>
          <button
            type="button"
            class="quick-fm-entry"
            :class="{ empty: !quickFm.mounts.length }"
            @click="openQuickFmDialog"
          >
            <span class="quick-fm-entry-text">
              {{ quickFm.mounts.length ? `已挂 ${quickFm.mounts.length} 份` : '未挂载' }}
            </span>
            <em>管理</em>
          </button>
        </label>
      </section>

      <section class="home-layout quick-workspace" :class="{ 'context-pinned': stickyContextVisible }">
        <div class="overview-card quick-overview-card">
          <div class="overview-head">
            <div>
              <h1>快速模式执行总览</h1>
            </div>
            <span>{{ store.session.caseCount }} 条 quick case</span>
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

        <div class="home-context-trigger" aria-hidden="true"></div>

        <div class="home-sticky-context" :class="{ visible: stickyContextVisible }">
          <div class="sticky-context-main">
            <strong>Quick Session</strong>
            <em>/</em>
            <span>{{ store.session.suiteTitle }}</span>
          </div>
          <div class="sticky-context-pills">
            <span class="context-pill context-neutral">总 {{ store.summary.caseCount }}</span>
            <span class="context-pill context-not-run">未 {{ store.summary.notRun }}</span>
            <span class="context-pill context-running">执 {{ store.summary.running }}</span>
            <span class="context-pill context-passed">过 {{ store.summary.passed }}</span>
            <span class="context-pill context-failed">错 {{ store.summary.failed }}</span>
          </div>
          <div class="sticky-context-user">{{ currentUser?.displayName || '未选择用户' }}</div>
        </div>

        <div ref="workspaceRef" class="workspace-grid">
          <QuickMindMap
            :cases="store.cases"
            :session-title="store.session.suiteTitle"
            :selected-case-id="store.selectedCaseId"
            @select-case="selectCaseFromMindMap"
          />
          <QuickOperationPanel
            :cases="filteredCases"
            :selected-case-id="store.selectedCaseId"
            :scroll-to-case-id="caseScrollTargetId"
            :scroll-request-key="caseScrollRequestKey"
            :selected-case-ids="store.selectedCaseIds"
            :status-filter="store.statusFilter"
            :total-case-count="store.cases.length"
            :now-ms="nowMs"
            :bug-context-ready="quickBugContextReady"
            :preparing-bug-draft-ids="preparingBugDraftIdList"
            @select-case="selectCase"
            @set-filter="store.setStatusFilter"
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
      </section>
    </template>

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

    <div v-if="editDialog.open" class="modal-mask">
      <section class="import-modal asset-edit-modal">
        <div class="modal-head">
          <div>
            <h2>编辑执行内容</h2>
            <p>快速模式只允许修改测试标题、前置条件、操作步骤、预期结果；测试集和路径保持导入结构。</p>
          </div>
          <button type="button" @click="closeEditDialog">关闭</button>
        </div>
        <div class="asset-edit-form">
          <label class="wide quick-readonly-field">
            <span>测试集标题</span>
            <strong>{{ editDialog.suiteTitle }}</strong>
          </label>
          <label class="wide quick-readonly-field">
            <span>路径信息</span>
            <strong>{{ editDialog.pathInfo }}</strong>
          </label>
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

    <div v-if="quickFm.dialogOpen" class="modal-mask" @click.self="closeQuickFmDialog">
      <section class="import-modal quick-fm-modal">
        <div class="modal-head">
          <div>
            <h2>Function Map</h2>
            <p>从资产库按引用选，带入本次快速会话；要新增/改正文请去 Function Map 资产库。</p>
          </div>
          <div class="quick-fm-head-actions">
            <button type="button" @click="goToFunctionMapAssets">去资产库上传</button>
            <button type="button" @click="closeQuickFmDialog">关闭</button>
          </div>
        </div>
        <div class="quick-fm-modal-body">
          <div class="quick-fm-selected">
            <div class="quick-fm-section-title">已挂载（{{ quickFm.mounts.length }}）</div>
            <div v-if="quickFm.mounts.length" class="quick-fm-chips">
              <span v-for="m in quickFm.mounts" :key="m.id" class="quick-fm-chip">
                {{ m.title }}
                <button
                  type="button"
                  class="quick-fm-chip-eye"
                  :class="{ active: quickFmTip?.id === m.id }"
                  title="查看正文"
                  @click="toggleQuickFmTip(m, $event)"
                >正文</button>
                <button type="button" class="quick-fm-chip-x" :disabled="quickFm.busy" @click="removeQuickFm(m.id)">×</button>
              </span>
            </div>
            <p v-else class="quick-session-hint">未选择任何 Function Map</p>
          </div>
          <div class="quick-fm-picker-modal">
            <input
              v-model="quickFm.search"
              type="search"
              placeholder="按标题 / 适用场景模糊搜索资产库"
              @input="onQuickFmSearchInput"
            />
            <div class="quick-fm-results">
              <div v-for="item in quickFm.results" :key="item.id" class="quick-fm-result">
                <div class="quick-fm-result-main">
                  <strong>{{ item.title }}</strong>
                  <span
                    class="quick-fm-result-desc"
                    @mouseenter="showQuickFmDescTip(item.description, $event)"
                    @mouseleave="hideQuickFmDescTip"
                  >{{ item.description || '（无适用场景）' }}</span>
                </div>
                <span class="quick-fm-targets">
                  <span v-for="t in item.targets" :key="t" class="quick-fm-tag">{{ FM_TARGET_LABEL[t] }}</span>
                </span>
                <button
                  type="button"
                  class="quick-fm-eye"
                  :class="{ active: quickFmTip?.id === item.id }"
                  title="查看正文"
                  @click="toggleQuickFmTip(item, $event)"
                >
                  正文
                </button>
                <button
                  type="button"
                  :disabled="quickFm.busy || quickFmMountedIds.has(item.id)"
                  @click="addQuickFm(item.id)"
                >
                  {{ quickFmMountedIds.has(item.id) ? '已选' : '选' }}
                </button>
              </div>
              <p v-if="!quickFm.results.length" class="quick-session-hint">无匹配资产</p>
            </div>
            <div v-if="quickFm.total > QUICK_FM_PAGE_SIZE" class="quick-fm-pager">
              <button type="button" :disabled="quickFm.page <= 1" @click="goQuickFmPage(-1)">上一页</button>
              <span>第 {{ quickFm.page }} / {{ quickFmTotalPages }} 页 · 共 {{ quickFm.total }} 条</span>
              <button type="button" :disabled="quickFm.page >= quickFmTotalPages" @click="goQuickFmPage(1)">下一页</button>
            </div>
          </div>
        </div>
        <div class="modal-actions">
          <button type="button" class="primary" @click="closeQuickFmDialog">完成</button>
        </div>
      </section>
    </div>

    <Teleport to="body">
      <div
        v-if="quickFmTip"
        class="quick-fm-tip"
        :style="{ left: `${quickFmTip.x}px`, top: `${quickFmTip.y}px`, maxHeight: `${quickFmTip.maxHeight}px` }"
      >
        <div class="quick-fm-tip-head">
          <span>{{ quickFmTip.title }}</span>
          <button type="button" title="关闭" @click="closeQuickFmTip">×</button>
        </div>
        <pre class="quick-fm-tip-body">{{ quickFmTip.content }}</pre>
      </div>
      <div
        v-if="quickFmDescTip"
        class="quick-fm-desc-tip"
        :style="{ left: `${quickFmDescTip.x}px`, top: `${quickFmDescTip.y}px` }"
      >{{ quickFmDescTip.text }}</div>
    </Teleport>

    <QuickDevicePickerModal
      :open="devicePicker.open"
      :mode="devicePicker.mode"
      :loading="devicePicker.loading"
      :error="devicePicker.error"
      :items="devicePicker.items"
      :devices="devicePicker.devices"
      :web-devices="devicePicker.webDevices"
      :selected-aliases="devicePicker.selectedAliases"
      :web-selected-aliases="devicePicker.webSelectedAliases"
      :device-filter="devicePicker.deviceFilter"
      :retry-max="devicePicker.retryMax"
      :cache-mode="devicePicker.cacheMode"
      @close="closeDevicePicker"
      @refresh="loadDevicePickerDevices"
      @refresh-web="loadDevicePickerWebDevices"
      @confirm="confirmDevicePicker"
      @execute-device="executeSingleDevice"
      @toggle-platform="setPlatformDevices"
      @toggle-web-platform="setWebPlatformDevices"
      @toggle-device="toggleDevice"
      @toggle-web-device="toggleWebDevice"
      @update-device-filter="onQuickDeviceFilterInput"
      @update-retry-max="devicePicker.retryMax = $event"
      @update-cache-mode="devicePicker.cacheMode = $event"
      @reorder-items="reorderDevicePickerItems"
    />

    <QuickRepairModal
      :open="repairDialog.open"
      :loading="repairDialog.loading"
      :error="repairDialog.error"
      :case-ids="repairDialog.caseIds"
      :items="repairDialog.items"
      :applying-draft-id="repairDialog.applyingDraftId"
      @close="closeRepairDialog"
      @apply="applyRepairDraft"
      @skip="skipRepairItem"
      @submit-bug="openBugDialog"
    />

    <QuickBugModal
      :open="bugDialog.open"
      :loading="bugDialog.loading"
      :submitting="bugDialog.submitting"
      :image-uploading="bugDialog.imageUploading"
      :error="bugDialog.error"
      :result-url="bugDialog.resultUrl"
      :title="bugDialog.title"
      :description="bugDialog.description"
      :fields="bugDialog.fields"
      :has-image="bugDialog.hasImage"
      :key-image="bugDialog.keyImage"
      :key-images="bugDialog.keyImages"
      :excluded-images="bugDialog.excludedImages"
      :submitted-bugs="bugDialog.submittedBugs"
      @close="closeBugDialog"
      @submit="submitBugDialog"
      @submit-another="submitAnotherBug"
      @update-title="bugDialog.title = $event"
      @update-description="bugDialog.description = $event"
      @set-field-selected="setBugFieldSelected"
      @toggle-key-image="toggleKeyImage"
      @upload-images="uploadBugImages"
    />

    <div v-if="bugPrereqDialog.open" class="modal-mask">
      <section class="import-modal compact quick-prereq-modal">
        <div class="modal-head">
          <div>
            <h2>提交 bug 前需要绑定信息</h2>
            <p>快速模式提交 bug 需要先确定提交人，并绑定一个已完整接入标准版的飞书需求。</p>
          </div>
          <button type="button" @click="closeBugPrereqDialog">关闭</button>
        </div>
        <div class="quick-prereq-body">
          <p v-for="message in bugPrereqDialog.messages" :key="message">{{ message }}</p>
        </div>
        <div class="modal-actions">
          <button type="button" class="primary" @click="closeBugPrereqDialog">知道了</button>
        </div>
      </section>
    </div>

    <div v-if="terminalDialog.open" class="modal-mask">
      <section class="import-modal compact quick-terminal-modal">
        <div class="modal-head">
          <div>
            <h2>{{ terminalDialogCopy.title }}</h2>
            <p>{{ terminalDialogCopy.description }}</p>
          </div>
          <button type="button" :disabled="terminalDialog.loading" @click="closeTerminalDialog">关闭</button>
        </div>
        <div class="quick-terminal-body">
          <p v-for="point in terminalDialogCopy.points" :key="point">{{ point }}</p>
          <div v-if="terminalDialog.error" class="error-state">{{ terminalDialog.error }}</div>
        </div>
        <div class="modal-actions">
          <button type="button" :disabled="terminalDialog.loading" @click="closeTerminalDialog">取消</button>
          <button
            v-if="terminalDialog.action === 'export'"
            type="button"
            :disabled="terminalDialog.loading"
            @click="exportMarkdownOnly"
          >
            {{ terminalDialog.runningAction === 'export' ? '导出中...' : '导出' }}
          </button>
          <button
            type="button"
            :class="terminalDialogCopy.primaryClass"
            :disabled="terminalDialog.loading"
            @click="confirmTerminalDialog"
          >
            {{
              terminalDialog.runningAction === 'exportExit'
                ? '导出中...'
                : terminalDialog.runningAction === 'exit'
                  ? '退出中...'
                  : terminalDialogCopy.primaryText
            }}
          </button>
        </div>
      </section>
    </div>
  </div>
</template>
