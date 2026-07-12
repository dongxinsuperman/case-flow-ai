<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useFunctionMapAssetsStore } from '../stores/functionMapAssets'
import type {
  FunctionMapAsset,
  FunctionMapAssetInput,
  FunctionMapAssetListItem,
  FunctionMapAssetMetaInput,
  FunctionMapTarget,
  MountScope,
  MountTargetGroup,
  MountTargetItem,
} from '../types/functionMap'

const store = useFunctionMapAssetsStore()
const route = useRoute()

interface MountNode {
  scope: MountScope
  id: number
  label: string
  groupId?: number
}

const TREE_PAGE_SIZE = 20
const POPUP_PAGE_SIZE = 20
const PICKER_PAGE_SIZE = 20

const activeTab = ref<'library' | 'mounts'>('library')

// 挂载管理左侧目录/需求树（服务端搜索 + 分页，含未进入目录）
const treeKeyword = ref('')
const treeGroups = ref<MountTargetGroup[]>([])
const treeUngrouped = ref<MountTargetItem[]>([])
const treeTotal = ref(0)
const treePage = ref(1)
const treeLoading = ref(false)

const treeListRef = ref<HTMLElement | null>(null)
const selectedNode = ref<MountNode | null>(null)
const nodeMounts = ref<FunctionMapAssetListItem[]>([])
const inheritedMounts = ref<FunctionMapAssetListItem[]>([])
const mountsLoading = ref(false)
const mountError = ref('')
const pickerKeyword = ref('')
const pickerResults = ref<FunctionMapAssetListItem[]>([])
const pickerPage = ref(1)
const pickerTotal = ref(0)
const createMountNode = ref<MountNode | null>(null)

const expandedGroups = ref<Set<number>>(new Set())

const mountedIds = computed(() => new Set(nodeMounts.value.map((item) => item.id)))
const inheritedIds = computed(() => new Set(inheritedMounts.value.map((item) => item.id)))
const treePageCount = computed(() => Math.max(1, Math.ceil(treeTotal.value / TREE_PAGE_SIZE)))
const pickerPageCount = computed(() => Math.max(1, Math.ceil(pickerTotal.value / PICKER_PAGE_SIZE)))
const assetsPageCount = computed(() => Math.max(1, Math.ceil(store.assetsTotal / store.assetsPageSize)))

function isGroupExpanded(groupId: number): boolean {
  return expandedGroups.value.has(groupId)
}

function toggleGroupExpand(groupId: number) {
  const next = new Set(expandedGroups.value)
  if (next.has(groupId)) {
    next.delete(groupId)
  } else {
    next.add(groupId)
  }
  expandedGroups.value = next
}

let pickerTimer: number | undefined
let treeTimer: number | undefined

const ALL_TARGETS: FunctionMapTarget[] = ['app', 'web', 'api']
const TARGET_LABEL: Record<FunctionMapTarget, string> = { app: 'App', web: 'Web', api: 'API' }
const ACCEPTED_EXT = ['md', 'txt', 'json']

const detail = ref<FunctionMapAsset | null>(null)
const detailLoading = ref(false)

// 点击“眼睛”查看该 Map 的完整正文（点击弹出、可滚动；再点或关闭按钮收起）
const contentTip = ref<{ id: number; title: string; content: string; x: number; y: number; maxHeight: number } | null>(null)

async function toggleContentTip(item: FunctionMapAssetListItem, ev: MouseEvent) {
  if (contentTip.value?.id === item.id) {
    contentTip.value = null
    return
  }
  const anchor = ev.currentTarget as HTMLElement | null
  if (!anchor) {
    contentTip.value = null
    return
  }
  const rect = anchor.getBoundingClientRect()
  const pad = 12
  const width = 400
  const maxHeight = Math.round(window.innerHeight * 0.6)
  // 放到眼睛左侧（左侧是目录树区）；放不下就贴视口左边，避免压住右侧操作区
  let x = rect.left - width - 8
  if (x < pad) {
    x = pad
  }
  let y = rect.top
  if (y + maxHeight > window.innerHeight - pad) {
    y = Math.max(pad, window.innerHeight - maxHeight - pad)
  }
  // 正文按需拉取，不随列表全量返回
  contentTip.value = { id: item.id, title: item.title, content: '加载中…', x, y, maxHeight }
  try {
    const full = await store.getAsset(item.id)
    if (contentTip.value?.id === item.id) {
      contentTip.value = { ...contentTip.value, content: full.content }
    }
  } catch {
    if (contentTip.value?.id === item.id) {
      contentTip.value = { ...contentTip.value, content: '正文加载失败' }
    }
  }
}

function closeContentTip() {
  contentTip.value = null
}

// 悬浮查看被截断的“适用场景”完整内容
const descTip = ref<{ text: string; x: number; y: number } | null>(null)

function showDescTip(text: string, ev: MouseEvent) {
  const anchor = ev.currentTarget as HTMLElement | null
  if (!anchor || !text.trim()) {
    return
  }
  if (anchor.scrollWidth <= anchor.clientWidth && anchor.scrollHeight <= anchor.clientHeight) {
    return
  }
  const rect = anchor.getBoundingClientRect()
  const pad = 12
  const width = 360
  let x = rect.left
  if (x + width > window.innerWidth - pad) {
    x = window.innerWidth - width - pad
  }
  if (x < pad) {
    x = pad
  }
  descTip.value = { text, x, y: rect.bottom + 6 }
}

function hideDescTip() {
  descTip.value = null
}

const showForm = ref(false)
const formKind = ref<'create' | 'editMeta'>('create')
const formAssetId = ref<number | null>(null)
const formError = ref('')
const submitting = ref(false)
const overwriteInput = ref<HTMLInputElement | null>(null)
const overwriting = ref(false)

const form = reactive({
  title: '',
  description: '',
  content: '',
  targets: [] as FunctionMapTarget[],
  sourceFilename: null as string | null,
})

let keywordTimer: number | undefined

async function loadTree() {
  treeLoading.value = true
  mountError.value = ''
  try {
    const catalog = await store.loadMountTargets(treeKeyword.value, treePage.value, TREE_PAGE_SIZE)
    treeGroups.value = catalog.groups
    treeUngrouped.value = catalog.ungroupedItems
    treeTotal.value = catalog.total
    treePage.value = catalog.page || 1
  } catch (err) {
    mountError.value = err instanceof Error ? err.message : '加载目录 / 需求失败'
  } finally {
    treeLoading.value = false
  }
}

function onTreeSearch() {
  window.clearTimeout(treeTimer)
  treeTimer = window.setTimeout(() => {
    treePage.value = 1
    void loadTree()
  }, 300)
}

function changeTreePage(delta: number) {
  const next = treePage.value + delta
  if (next < 1 || next > treePageCount.value) {
    return
  }
  treePage.value = next
  void loadTree()
}

async function switchTab(tab: 'library' | 'mounts') {
  activeTab.value = tab
  if (tab === 'mounts' && treeGroups.value.length === 0 && treeUngrouped.value.length === 0) {
    await loadTree()
  }
}

async function loadNodeMounts() {
  const node = selectedNode.value
  if (!node) {
    return
  }
  mountsLoading.value = true
  mountError.value = ''
  try {
    if (node.scope === 'group') {
      nodeMounts.value = await store.listGroupMounts(node.id)
      inheritedMounts.value = []
    } else {
      nodeMounts.value = await store.listItemMounts(node.id)
      inheritedMounts.value = node.groupId ? await store.listGroupMounts(node.groupId) : []
    }
  } catch (err) {
    mountError.value = err instanceof Error ? err.message : '加载挂载列表失败'
  } finally {
    mountsLoading.value = false
  }
}

async function runPicker() {
  try {
    const result = await store.searchAssets(pickerKeyword.value, pickerPage.value, PICKER_PAGE_SIZE)
    pickerResults.value = result.items
    pickerTotal.value = result.total
    pickerPage.value = result.page
  } catch (err) {
    mountError.value = err instanceof Error ? err.message : '搜索资产失败'
  }
}

function onPickerInput() {
  window.clearTimeout(pickerTimer)
  pickerTimer = window.setTimeout(() => {
    pickerPage.value = 1
    void runPicker()
  }, 300)
}

function changePickerPage(delta: number) {
  const next = pickerPage.value + delta
  if (next < 1 || next > pickerPageCount.value) {
    return
  }
  pickerPage.value = next
  void runPicker()
}

async function selectNode(node: MountNode) {
  selectedNode.value = node
  pickerKeyword.value = ''
  pickerPage.value = 1
  await loadNodeMounts()
  await runPicker()
}

async function scrollSelectedIntoView() {
  await nextTick()
  const node = selectedNode.value
  if (!node) {
    return
  }
  const key = `${node.scope === 'group' ? 'g' : 'i'}-${node.id}`
  const el = treeListRef.value?.querySelector(`[data-node-key="${key}"]`)
  if (el instanceof HTMLElement) {
    el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }
}

function selectGroupNode(group: MountTargetGroup) {
  void selectNode({ scope: 'group', id: group.id, label: group.name })
}

function selectItemNode(item: MountTargetItem, groupId?: number) {
  void selectNode({ scope: 'item', id: item.id, label: item.title, groupId })
}

async function mountAsset(assetId: number) {
  const node = selectedNode.value
  if (!node) {
    return
  }
  try {
    nodeMounts.value = node.scope === 'group'
      ? await store.mountToGroup(node.id, assetId)
      : await store.mountToItem(node.id, assetId)
    void store.loadAssets()
  } catch (err) {
    mountError.value = err instanceof Error ? err.message : '挂载失败'
  }
}

async function unmountAsset(assetId: number) {
  const node = selectedNode.value
  if (!node) {
    return
  }
  try {
    nodeMounts.value = node.scope === 'group'
      ? await store.unmountFromGroup(node.id, assetId)
      : await store.unmountFromItem(node.id, assetId)
    void store.loadAssets()
  } catch (err) {
    mountError.value = err instanceof Error ? err.message : '移除挂载失败'
  }
}

function openCreateForMount() {
  openCreate()
  createMountNode.value = selectedNode.value
}

// ---- 资产视角挂载弹窗（一级 + 二级混合，服务端搜索 + 分页） ----
const mountPopupAsset = ref<{ id: number; title: string } | null>(null)
const mountPopupSearch = ref('')
const mountPopupGroupIds = ref<Set<number>>(new Set())
const mountPopupItemIds = ref<Set<number>>(new Set())
const mountPopupBusy = ref(false)
const popupGroups = ref<MountTargetGroup[]>([])
const popupUngrouped = ref<MountTargetItem[]>([])
const popupTotal = ref(0)
const popupPage = ref(1)
const popupExpanded = ref<Set<number>>(new Set())
let popupTimer: number | undefined

const popupPageCount = computed(() => Math.max(1, Math.ceil(popupTotal.value / POPUP_PAGE_SIZE)))

function isPopupGroupExpanded(groupId: number): boolean {
  return popupExpanded.value.has(groupId)
}

function togglePopupGroupExpand(groupId: number) {
  const next = new Set(popupExpanded.value)
  if (next.has(groupId)) {
    next.delete(groupId)
  } else {
    next.add(groupId)
  }
  popupExpanded.value = next
}

async function loadPopupTargets() {
  try {
    const catalog = await store.loadMountTargets(mountPopupSearch.value, popupPage.value, POPUP_PAGE_SIZE)
    popupGroups.value = catalog.groups
    popupUngrouped.value = catalog.ungroupedItems
    popupTotal.value = catalog.total
    popupPage.value = catalog.page || 1
  } catch (err) {
    store.error = err instanceof Error ? err.message : '加载目录 / 需求失败'
  }
}

function onPopupSearch() {
  window.clearTimeout(popupTimer)
  popupTimer = window.setTimeout(() => {
    popupPage.value = 1
    void loadPopupTargets()
  }, 300)
}

function changePopupPage(delta: number) {
  const next = popupPage.value + delta
  if (next < 1 || next > popupPageCount.value) {
    return
  }
  popupPage.value = next
  void loadPopupTargets()
}

async function openMountPopup(asset: FunctionMapAssetListItem | FunctionMapAsset) {
  mountPopupAsset.value = { id: asset.id, title: asset.title }
  mountPopupSearch.value = ''
  popupPage.value = 1
  popupExpanded.value = new Set()
  try {
    const full = await store.getAsset(asset.id)
    mountPopupGroupIds.value = new Set(full.mounts.filter((m) => m.scope === 'group').map((m) => m.id))
    mountPopupItemIds.value = new Set(full.mounts.filter((m) => m.scope === 'item').map((m) => m.id))
    await loadPopupTargets()
  } catch (err) {
    store.error = err instanceof Error ? err.message : '加载资产挂载失败'
  }
}

function closeMountPopup() {
  mountPopupAsset.value = null
}

async function afterMountChange() {
  void store.loadAssets()
  if (selectedNode.value) {
    await loadNodeMounts()
  }
  // 详情抽屉开着同一资产时，刷新它的挂载位置与引用数，避免展示旧值
  if (detail.value) {
    try {
      detail.value = await store.getAsset(detail.value.id)
    } catch {
      /* 详情刷新失败不影响挂载本身 */
    }
  }
}

async function toggleGroupMount(groupId: number) {
  const asset = mountPopupAsset.value
  if (!asset) {
    return
  }
  mountPopupBusy.value = true
  try {
    if (mountPopupGroupIds.value.has(groupId)) {
      await store.unmountFromGroup(groupId, asset.id)
      const next = new Set(mountPopupGroupIds.value)
      next.delete(groupId)
      mountPopupGroupIds.value = next
    } else {
      await store.mountToGroup(groupId, asset.id)
      mountPopupGroupIds.value = new Set([...mountPopupGroupIds.value, groupId])
    }
    await afterMountChange()
  } catch (err) {
    store.error = err instanceof Error ? err.message : '挂载操作失败'
  } finally {
    mountPopupBusy.value = false
  }
}

async function toggleItemMount(itemId: number) {
  const asset = mountPopupAsset.value
  if (!asset) {
    return
  }
  mountPopupBusy.value = true
  try {
    if (mountPopupItemIds.value.has(itemId)) {
      await store.unmountFromItem(itemId, asset.id)
      const next = new Set(mountPopupItemIds.value)
      next.delete(itemId)
      mountPopupItemIds.value = next
    } else {
      await store.mountToItem(itemId, asset.id)
      mountPopupItemIds.value = new Set([...mountPopupItemIds.value, itemId])
    }
    await afterMountChange()
  } catch (err) {
    store.error = err instanceof Error ? err.message : '挂载操作失败'
  } finally {
    mountPopupBusy.value = false
  }
}

onMounted(async () => {
  void store.loadAssets()
  const rawGroup = route.query.groupId
  const rawItem = route.query.itemId
  const groupId = rawGroup ? Number(rawGroup) : Number.NaN
  const itemId = rawItem ? Number(rawItem) : Number.NaN
  if ((Number.isFinite(groupId) && groupId > 0) || (Number.isFinite(itemId) && itemId > 0)) {
    activeTab.value = 'mounts'
    const focus = Number.isFinite(itemId) && itemId > 0 ? { itemId } : { groupId }
    treeLoading.value = true
    try {
      const catalog = await store.loadMountTargets('', 1, TREE_PAGE_SIZE, focus)
      treeGroups.value = catalog.groups
      treeUngrouped.value = catalog.ungroupedItems
      treeTotal.value = catalog.total
      treePage.value = catalog.page || 1
    } catch (err) {
      mountError.value = err instanceof Error ? err.message : '加载目录 / 需求失败'
    } finally {
      treeLoading.value = false
    }
    if (Number.isFinite(itemId) && itemId > 0) {
      const group = treeGroups.value.find((g) => g.items.some((it) => it.id === itemId))
      const item = group?.items.find((it) => it.id === itemId)
        ?? treeUngrouped.value.find((it) => it.id === itemId)
      if (group) {
        expandedGroups.value = new Set([...expandedGroups.value, group.id])
      }
      if (item) {
        selectItemNode(item, group?.id)
        await scrollSelectedIntoView()
      }
    } else {
      const group = treeGroups.value.find((g) => g.id === groupId)
      if (group) {
        selectGroupNode(group)
        await scrollSelectedIntoView()
      }
    }
  }
})

function onKeywordInput() {
  window.clearTimeout(keywordTimer)
  keywordTimer = window.setTimeout(() => {
    store.assetsPage = 1
    void store.loadAssets()
  }, 300)
}

function setTargetFilter(value: '' | FunctionMapTarget) {
  store.targetFilter = value
  store.assetsPage = 1
  void store.loadAssets()
}

function changeAssetsPage(delta: number) {
  const next = store.assetsPage + delta
  if (next < 1 || next > assetsPageCount.value) {
    return
  }
  store.assetsPage = next
  void store.loadAssets()
}

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString('zh-CN', { hour12: false })
}

async function openDetail(item: FunctionMapAssetListItem) {
  detailLoading.value = true
  try {
    detail.value = await store.getAsset(item.id)
  } catch (err) {
    store.error = err instanceof Error ? err.message : '加载资产详情失败'
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detail.value = null
}

function resetForm() {
  form.title = ''
  form.description = ''
  form.content = ''
  form.targets = []
  form.sourceFilename = null
  formError.value = ''
}

function openCreate() {
  resetForm()
  formKind.value = 'create'
  formAssetId.value = null
  createMountNode.value = null
  showForm.value = true
}

function openEditMeta(asset: FunctionMapAsset) {
  resetForm()
  formKind.value = 'editMeta'
  formAssetId.value = asset.id
  form.title = asset.title
  form.description = asset.description
  form.targets = [...asset.targets]
  showForm.value = true
}

function triggerOverwrite() {
  overwriteInput.value?.click()
}

async function onOverwriteFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !detail.value) {
    return
  }
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!ACCEPTED_EXT.includes(ext)) {
    store.error = `只支持 .md / .txt / .json 文件，当前是 .${ext}`
    input.value = ''
    return
  }
  const current = detail.value
  overwriting.value = true
  try {
    // 导入覆盖只替换正文，标题/适用场景/适用端保持原资产不变。
    const updated = await store.overwriteContent(current.id, {
      content: await file.text(),
      sourceFilename: file.name,
    })
    detail.value = updated
    store.error = ''
  } catch (err) {
    store.error = err instanceof Error ? err.message : '导入覆盖失败'
  } finally {
    overwriting.value = false
    input.value = ''
  }
}

function closeForm() {
  showForm.value = false
}

function toggleFormTarget(target: FunctionMapTarget) {
  const index = form.targets.indexOf(target)
  if (index >= 0) {
    form.targets.splice(index, 1)
  } else {
    form.targets.push(target)
  }
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) {
    return
  }
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!ACCEPTED_EXT.includes(ext)) {
    formError.value = `只支持 .md / .txt / .json 文件，当前是 .${ext}`
    input.value = ''
    return
  }
  // 导入只替换正文，元信息（标题/适用场景/适用端）由用户在表单里填写。
  form.content = await file.text()
  form.sourceFilename = file.name
  formError.value = ''
  input.value = ''
}

function buildInput(): FunctionMapAssetInput {
  return {
    title: form.title.trim(),
    description: form.description.trim(),
    content: form.content,
    targets: [...form.targets],
    sourceFilename: form.sourceFilename,
  }
}

function validateForm(): string {
  if (!form.title.trim()) {
    return '标题不能为空'
  }
  if (!form.description.trim()) {
    return '适用场景不能为空'
  }
  if (form.targets.length === 0) {
    return '适用端至少选择一个'
  }
  if (formKind.value === 'create' && !form.content.trim()) {
    return '正文不能为空（请导入本地文件）'
  }
  return ''
}

async function submitForm() {
  const message = validateForm()
  if (message) {
    formError.value = message
    return
  }
  submitting.value = true
  formError.value = ''
  try {
    if (formKind.value === 'editMeta' && formAssetId.value != null) {
      const meta: FunctionMapAssetMetaInput = {
        title: form.title.trim(),
        description: form.description.trim(),
        targets: [...form.targets],
      }
      const updated = await store.updateMeta(formAssetId.value, meta)
      if (detail.value?.id === updated.id) {
        detail.value = updated
      }
    } else {
      const created = await store.createAsset(buildInput())
      const node = createMountNode.value
      if (node) {
        nodeMounts.value = node.scope === 'group'
          ? await store.mountToGroup(node.id, created.id)
          : await store.mountToItem(node.id, created.id)
        createMountNode.value = null
        void store.loadAssets()
      }
    }
    showForm.value = false
  } catch (err) {
    formError.value = err instanceof Error ? err.message : '提交失败'
  } finally {
    submitting.value = false
  }
}

function toFileName(item: FunctionMapAssetListItem | FunctionMapAsset): string {
  const cleaned = item.title.replace(/[\\/:*?"<>|\r\n]+/g, '_').trim()
  return `${cleaned || `function-map-${item.id}`}.md`
}

async function exportAsset(item: FunctionMapAssetListItem | FunctionMapAsset) {
  try {
    const data = await store.exportAsset(item.id)
    const blob = new Blob([data.content], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = toFileName(item)
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    store.error = err instanceof Error ? err.message : '导出失败'
  }
}

async function deleteAsset(item: FunctionMapAssetListItem | FunctionMapAsset) {
  const referenced = item.referenceCount > 0
    ? `\n该资产已被 ${item.referenceCount} 处挂载引用，删除后这些引用会失效。`
    : ''
  if (!window.confirm(`确认删除「${item.title}」？${referenced}`)) {
    return
  }
  try {
    await store.deleteAsset(item.id)
    if (detail.value?.id === item.id) {
      detail.value = null
    }
  } catch (err) {
    store.error = err instanceof Error ? err.message : '删除失败'
  }
}
</script>

<template>
  <section class="fm-page">
    <div class="fm-panel">
      <div class="fm-subtabs">
        <button
          type="button"
          :class="['fm-subtab', { active: activeTab === 'library' }]"
          @click="switchTab('library')"
        >
          资产库
        </button>
        <button
          type="button"
          :class="['fm-subtab', { active: activeTab === 'mounts' }]"
          @click="switchTab('mounts')"
        >
          挂载管理
        </button>
      </div>

      <div v-show="activeTab === 'library'" class="fm-tab-body">
      <header class="fm-header">
      <div class="fm-header-title">
        <h1>Function Map 资产库</h1>
        <p>全局可复用的上下文卡片：标题、适用场景、正文、适用端。标题/适用场景/适用端可在线编辑，正文只能本地文本导入覆盖。</p>
      </div>
      <button type="button" class="fm-primary" @click="openCreate">新建 Function Map</button>
    </header>

    <div class="fm-toolbar">
      <div class="fm-filter-group" role="group" aria-label="按适用端筛选">
        <button
          type="button"
          :class="['fm-chip', { active: store.targetFilter === '' }]"
          @click="setTargetFilter('')"
        >
          全部
        </button>
        <button
          v-for="target in ALL_TARGETS"
          :key="target"
          type="button"
          :class="['fm-chip', { active: store.targetFilter === target }]"
          @click="setTargetFilter(target)"
        >
          {{ TARGET_LABEL[target] }}
        </button>
      </div>
      <input
        v-model="store.keyword"
        class="fm-search"
        type="search"
        placeholder="按标题或适用场景模糊搜索"
        @input="onKeywordInput"
      />
    </div>

    <p v-if="store.error" class="fm-error">{{ store.error }}</p>

    <div class="fm-list">
      <div class="fm-list-head">
        <span class="col-title">标题</span>
        <span class="col-desc">适用场景</span>
        <span class="col-targets">适用端</span>
        <span class="col-updated">更新时间</span>
        <span class="col-ref">引用</span>
        <span class="col-actions">操作</span>
      </div>

      <p v-if="store.loading" class="fm-empty">加载中…</p>
      <p v-else-if="store.assets.length === 0" class="fm-empty">
        暂无 Function Map 资产，点击右上角「新建 Function Map」导入第一张卡片。
      </p>

      <div
        v-for="item in store.assets"
        v-else
        :key="item.id"
        class="fm-row"
        @click="openDetail(item)"
      >
        <span class="col-title" :title="item.title">{{ item.title }}</span>
        <span
          class="col-desc"
          @mouseenter="showDescTip(item.description, $event)"
          @mouseleave="hideDescTip"
        >{{ item.description }}</span>
        <span class="col-targets">
          <span v-for="target in item.targets" :key="target" class="fm-tag">{{ TARGET_LABEL[target] }}</span>
        </span>
        <span class="col-updated">{{ formatTime(item.updatedAt) }}</span>
        <span class="col-ref">{{ item.referenceCount }}</span>
        <span class="col-actions" @click.stop>
          <button type="button" class="fm-link" @click="openDetail(item)">详情</button>
          <button type="button" class="fm-link" @click="openMountPopup(item)">挂载</button>
          <button type="button" class="fm-link" @click="exportAsset(item)">导出</button>
          <button type="button" class="fm-link danger" @click="deleteAsset(item)">删除</button>
        </span>
      </div>
      </div>
      <div v-if="store.assetsTotal > store.assetsPageSize" class="fm-tree-pager">
        <button type="button" :disabled="store.assetsPage <= 1" @click="changeAssetsPage(-1)">上一页</button>
        <span>{{ store.assetsPage }} / {{ assetsPageCount }}（共 {{ store.assetsTotal }}）</span>
        <button type="button" :disabled="store.assetsPage >= assetsPageCount" @click="changeAssetsPage(1)">下一页</button>
      </div>
      </div>

      <div v-show="activeTab === 'mounts'" class="fm-mounts">
        <aside class="fm-mounts-tree">
          <input
            v-model="treeKeyword"
            class="fm-search fm-tree-search"
            type="search"
            placeholder="搜索目录 / 二级需求"
            @input="onTreeSearch"
          />
          <div ref="treeListRef" class="fm-group-list">
            <p v-if="treeLoading" class="fm-empty">加载中…</p>
            <div v-for="group in treeGroups" :key="group.id" class="fm-tree-group">
              <div class="fm-tree-group-row">
                <button type="button" class="fm-tree-caret" @click="toggleGroupExpand(group.id)">
                  {{ isGroupExpanded(group.id) ? '▾' : '▸' }}
                </button>
                <button
                  type="button"
                  :data-node-key="`g-${group.id}`"
                  :class="['fm-group-item', { active: selectedNode?.scope === 'group' && selectedNode.id === group.id }]"
                  @click="selectGroupNode(group)"
                >
                  {{ group.name }}
                  <span class="fm-item-count">{{ group.items.length }}</span>
                </button>
              </div>
              <div v-if="isGroupExpanded(group.id)" class="fm-tree-items">
                <button
                  v-for="item in group.items"
                  :key="item.id"
                  type="button"
                  :data-node-key="`i-${item.id}`"
                  :class="['fm-tree-item', { active: selectedNode?.scope === 'item' && selectedNode.id === item.id }]"
                  @click="selectItemNode(item, group.id)"
                >
                  <span v-if="item.version" class="fm-item-version">{{ item.version }}</span>
                  {{ item.title }}
                </button>
                <p v-if="!group.items.length" class="fm-empty fm-tree-empty">无二级需求</p>
              </div>
            </div>
            <div v-if="treeUngrouped.length" class="fm-tree-group">
              <span class="fm-group-item ungrouped">未进入目录</span>
              <div class="fm-tree-items">
                <button
                  v-for="item in treeUngrouped"
                  :key="item.id"
                  type="button"
                  :data-node-key="`i-${item.id}`"
                  :class="['fm-tree-item', { active: selectedNode?.scope === 'item' && selectedNode.id === item.id }]"
                  @click="selectItemNode(item)"
                >
                  <span v-if="item.version" class="fm-item-version">{{ item.version }}</span>
                  {{ item.title }}
                </button>
              </div>
            </div>
            <p v-if="!treeLoading && !treeGroups.length && !treeUngrouped.length" class="fm-empty">
              暂无目录 / 二级需求
            </p>
          </div>
          <div v-if="treeTotal > TREE_PAGE_SIZE" class="fm-tree-pager">
            <button type="button" :disabled="treePage <= 1" @click="changeTreePage(-1)">上一页</button>
            <span>{{ treePage }} / {{ treePageCount }}</span>
            <button type="button" :disabled="treePage >= treePageCount" @click="changeTreePage(1)">下一页</button>
          </div>
        </aside>
        <section class="fm-mounts-detail">
          <p v-if="mountError" class="fm-error">{{ mountError }}</p>
          <template v-if="selectedNode">
            <div class="fm-mounts-head">
              <h2>
                <span class="fm-scope-tag">{{ selectedNode.scope === 'group' ? '一级目录' : '二级需求' }}</span>
                {{ selectedNode.label }} · 已挂载 Map
              </h2>
              <button type="button" class="fm-secondary" @click="openCreateForMount">新建并挂上</button>
            </div>

            <p v-if="mountsLoading" class="fm-empty">加载中…</p>
            <p v-else-if="!nodeMounts.length" class="fm-empty">这里还没挂载任何 Function Map。</p>
            <div v-else class="fm-mounted-list">
              <div v-for="item in nodeMounts" :key="item.id" class="fm-mounted-item">
                <div class="fm-mounted-main">
                  <strong>{{ item.title }}</strong>
                  <span class="fm-muted">{{ item.description }}</span>
                </div>
                <span class="fm-mounted-targets">
                  <span v-for="target in item.targets" :key="target" class="fm-tag">{{ TARGET_LABEL[target] }}</span>
                </span>
                <button
                  type="button"
                  class="fm-eye"
                  :class="{ active: contentTip?.id === item.id }"
                  title="查看正文"
                  @click.stop="toggleContentTip(item, $event)"
                >
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>
                </button>
                <button type="button" class="fm-link danger" @click="unmountAsset(item.id)">移除</button>
              </div>
            </div>

            <div class="fm-picker">
              <span class="fm-label">从资产库选已有挂上</span>
              <input
                v-model="pickerKeyword"
                class="fm-search"
                type="search"
                placeholder="按标题 / 适用场景模糊搜索"
                @input="onPickerInput"
              />
              <div class="fm-picker-list">
                <div v-for="item in pickerResults" :key="item.id" class="fm-picker-item">
                  <div class="fm-mounted-main">
                    <strong>{{ item.title }}</strong>
                    <span class="fm-muted">{{ item.description }}</span>
                  </div>
                  <span class="fm-mounted-targets">
                    <span v-for="target in item.targets" :key="target" class="fm-tag">{{ TARGET_LABEL[target] }}</span>
                  </span>
                  <span v-if="inheritedIds.has(item.id)" class="fm-inherit-hint" title="一级目录已挂这份，执行时会自动去重；仍可独立挂到本级">一级已挂</span>
                  <button
                    type="button"
                    class="fm-eye"
                    :class="{ active: contentTip?.id === item.id }"
                    title="查看正文"
                    @click.stop="toggleContentTip(item, $event)"
                  >
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>
                  </button>
                  <button
                    type="button"
                    class="fm-link"
                    :disabled="mountedIds.has(item.id)"
                    @click="mountAsset(item.id)"
                  >
                    {{ mountedIds.has(item.id) ? '已挂载' : '挂上' }}
                  </button>
                </div>
                <p v-if="!pickerResults.length" class="fm-empty">无匹配资产</p>
              </div>
              <div v-if="pickerTotal > PICKER_PAGE_SIZE" class="fm-tree-pager">
                <button type="button" :disabled="pickerPage <= 1" @click="changePickerPage(-1)">上一页</button>
                <span>{{ pickerPage }} / {{ pickerPageCount }}</span>
                <button type="button" :disabled="pickerPage >= pickerPageCount" @click="changePickerPage(1)">下一页</button>
              </div>
            </div>
          </template>
          <p v-else class="fm-empty">从左侧选择一级目录或二级需求来管理它的挂载。</p>
        </section>
      </div>
    </div>

    <input
      ref="overwriteInput"
      type="file"
      accept=".md,.txt,.json"
      class="fm-hidden-file"
      @change="onOverwriteFile"
    />

    <!-- 详情 -->
    <div v-if="detail" class="fm-overlay" @click.self="closeDetail">
      <div class="fm-drawer">
        <header class="fm-drawer-head">
          <h2>{{ detail.title }}</h2>
          <button type="button" class="fm-close" @click="closeDetail">×</button>
        </header>
        <div class="fm-drawer-body">
          <div class="fm-field">
            <span class="fm-label">适用端</span>
            <span>
              <span v-for="target in detail.targets" :key="target" class="fm-tag">{{ TARGET_LABEL[target] }}</span>
            </span>
          </div>
          <div class="fm-field">
            <span class="fm-label">适用场景</span>
            <p class="fm-text">{{ detail.description }}</p>
          </div>
          <div class="fm-field">
            <span class="fm-label">来源</span>
            <span class="fm-muted">
              本地导入{{ detail.sourceFilename ? `（${detail.sourceFilename}）` : '' }} · 更新于 {{ formatTime(detail.updatedAt) }}
            </span>
          </div>
          <div class="fm-field">
            <span class="fm-label">挂载位置</span>
            <span v-if="detail.mounts.length">
              <span v-for="m in detail.mounts" :key="`${m.scope}-${m.id}`" class="fm-tag">
                {{ m.scope === 'group' ? '一级' : '二级' }}·{{ m.name }}
              </span>
            </span>
            <span v-else class="fm-muted">未被任何一级目录 / 二级需求挂载。</span>
          </div>
          <div class="fm-field">
            <span class="fm-label">正文</span>
            <pre class="fm-content">{{ detail.content }}</pre>
          </div>
        </div>
        <footer class="fm-drawer-foot">
          <button type="button" class="fm-secondary" @click="openEditMeta(detail)">编辑信息</button>
          <button type="button" class="fm-secondary" @click="openMountPopup(detail)">挂载</button>
          <button type="button" class="fm-secondary" :disabled="overwriting" @click="triggerOverwrite">
            {{ overwriting ? '覆盖中…' : '导入覆盖正文' }}
          </button>
          <button type="button" class="fm-secondary" @click="exportAsset(detail)">导出</button>
          <button type="button" class="fm-secondary danger" @click="deleteAsset(detail)">删除</button>
        </footer>
      </div>
    </div>

    <!-- 新建 / 编辑信息表单 -->
    <div v-if="showForm" class="fm-overlay" @click.self="closeForm">
      <div class="fm-modal">
        <header class="fm-drawer-head">
          <h2>{{ formKind === 'editMeta' ? '编辑信息' : '新建 Function Map' }}</h2>
          <button type="button" class="fm-close" @click="closeForm">×</button>
        </header>
        <div class="fm-form">
          <label class="fm-form-row">
            <span class="fm-label">标题 <em>*</em></span>
            <input v-model="form.title" type="text" placeholder="例如：App 账号与登录态说明" />
          </label>
          <label class="fm-form-row">
            <span class="fm-label">适用场景 <em>*</em></span>
            <textarea
              v-model="form.description"
              rows="2"
              placeholder="说明这份资产适合什么场景，是自动发现的核心判断依据"
            ></textarea>
          </label>
          <div class="fm-form-row">
            <span class="fm-label">适用端 <em>*</em></span>
            <div class="fm-filter-group">
              <button
                v-for="target in ALL_TARGETS"
                :key="target"
                type="button"
                :class="['fm-chip', { active: form.targets.includes(target) }]"
                @click="toggleFormTarget(target)"
              >
                {{ TARGET_LABEL[target] }}
              </button>
            </div>
          </div>
          <div v-if="formKind === 'create'" class="fm-form-row">
            <span class="fm-label">正文 <em>*</em></span>
            <div class="fm-upload">
              <input type="file" accept=".md,.txt,.json" @change="onFileChange" />
              <span v-if="form.sourceFilename" class="fm-muted">已导入：{{ form.sourceFilename }}</span>
              <span class="fm-muted">支持 .md / .txt / .json 文本文件</span>
            </div>
            <textarea
              v-model="form.content"
              rows="8"
              placeholder="导入本地文件解析后的正文，或直接粘贴"
            ></textarea>
          </div>
          <p v-else class="fm-muted">正文不在这里编辑：关闭后用「导入覆盖正文」替换。</p>
          <p v-if="formError" class="fm-error">{{ formError }}</p>
        </div>
        <footer class="fm-drawer-foot">
          <button type="button" class="fm-secondary" @click="closeForm">取消</button>
          <button type="button" class="fm-primary" :disabled="submitting" @click="submitForm">
            {{ submitting ? '提交中…' : (formKind === 'editMeta' ? '保存' : '创建资产') }}
          </button>
        </footer>
      </div>
    </div>

    <!-- 资产视角挂载弹窗 -->
    <div v-if="mountPopupAsset" class="fm-overlay" @click.self="closeMountPopup">
      <div class="fm-modal">
        <header class="fm-drawer-head">
          <h2>挂载「{{ mountPopupAsset.title }}」到</h2>
          <button type="button" class="fm-close" @click="closeMountPopup">×</button>
        </header>
        <div class="fm-form">
          <input
            v-model="mountPopupSearch"
            class="fm-search fm-popup-search"
            type="search"
            placeholder="搜索一级目录 / 二级需求"
            @input="onPopupSearch"
          />
          <div class="fm-target-list">
            <template v-for="group in popupGroups" :key="group.id">
              <div class="fm-target-row" :class="{ mounted: mountPopupGroupIds.has(group.id) }">
                <button type="button" class="fm-tree-caret" @click="togglePopupGroupExpand(group.id)">
                  {{ isPopupGroupExpanded(group.id) ? '▾' : '▸' }}
                </button>
                <span class="fm-scope-tag">一级</span>
                <span class="fm-target-name">{{ group.name }}<span class="fm-item-count">{{ group.items.length }}</span></span>
                <span v-if="mountPopupGroupIds.has(group.id)" class="fm-mounted-flag">已挂</span>
                <button
                  type="button"
                  class="fm-link"
                  :class="{ danger: mountPopupGroupIds.has(group.id) }"
                  :disabled="mountPopupBusy"
                  @click="toggleGroupMount(group.id)"
                >
                  {{ mountPopupGroupIds.has(group.id) ? '取消挂载' : '挂载' }}
                </button>
              </div>
              <div
                v-for="item in (isPopupGroupExpanded(group.id) ? group.items : [])"
                :key="`i-${item.id}`"
                class="fm-target-row indent"
                :class="{ mounted: mountPopupItemIds.has(item.id) }"
              >
                <span class="fm-scope-tag item">二级</span>
                <span class="fm-target-name">{{ item.title }}</span>
                <span v-if="mountPopupItemIds.has(item.id)" class="fm-mounted-flag">已挂</span>
                <button
                  type="button"
                  class="fm-link"
                  :class="{ danger: mountPopupItemIds.has(item.id) }"
                  :disabled="mountPopupBusy"
                  @click="toggleItemMount(item.id)"
                >
                  {{ mountPopupItemIds.has(item.id) ? '取消挂载' : '挂载' }}
                </button>
              </div>
            </template>
            <div
              v-for="item in popupUngrouped"
              :key="`u-${item.id}`"
              class="fm-target-row"
              :class="{ mounted: mountPopupItemIds.has(item.id) }"
            >
              <span class="fm-scope-tag item">二级·未进目录</span>
              <span class="fm-target-name">{{ item.title }}</span>
              <span v-if="mountPopupItemIds.has(item.id)" class="fm-mounted-flag">已挂</span>
              <button
                type="button"
                class="fm-link"
                :class="{ danger: mountPopupItemIds.has(item.id) }"
                :disabled="mountPopupBusy"
                @click="toggleItemMount(item.id)"
              >
                {{ mountPopupItemIds.has(item.id) ? '取消挂载' : '挂载' }}
              </button>
            </div>
            <p v-if="!popupGroups.length && !popupUngrouped.length" class="fm-empty">无匹配一级目录 / 二级需求</p>
          </div>
          <div v-if="popupTotal > POPUP_PAGE_SIZE" class="fm-tree-pager">
            <button type="button" :disabled="popupPage <= 1" @click="changePopupPage(-1)">上一页</button>
            <span>{{ popupPage }} / {{ popupPageCount }}</span>
            <button type="button" :disabled="popupPage >= popupPageCount" @click="changePopupPage(1)">下一页</button>
          </div>
        </div>
        <footer class="fm-drawer-foot">
          <button type="button" class="fm-secondary" @click="closeMountPopup">完成</button>
        </footer>
      </div>
    </div>

    <Teleport to="body">
      <div
        v-if="contentTip"
        class="fm-content-tip"
        :style="{ left: `${contentTip.x}px`, top: `${contentTip.y}px`, maxHeight: `${contentTip.maxHeight}px` }"
      >
        <div class="fm-content-tip-title">
          <span>{{ contentTip.title }}</span>
          <button type="button" class="fm-content-tip-close" title="关闭" @click="closeContentTip">×</button>
        </div>
        <pre class="fm-content-tip-body">{{ contentTip.content }}</pre>
      </div>
      <div
        v-if="descTip"
        class="fm-desc-tip"
        :style="{ left: `${descTip.x}px`, top: `${descTip.y}px` }"
      >{{ descTip.text }}</div>
    </Teleport>
  </section>
</template>

<style scoped>
.fm-page {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: 12px 16px;
  height: calc(100dvh - 92px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.fm-panel {
  flex: 1;
  min-height: 0;
  background: #fff;
  border: 1px solid #d7dfec;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.fm-subtabs {
  flex: none;
}

.fm-tab-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.fm-tab-body .fm-header,
.fm-tab-body .fm-toolbar {
  flex: none;
}

.fm-tab-body .fm-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.fm-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.fm-header-title h1 {
  margin: 0 0 4px;
  font-size: 20px;
}

.fm-header-title p {
  margin: 0;
  color: #5b6478;
  font-size: 13px;
}

.fm-primary {
  border: 0;
  border-radius: 6px;
  background: #2563eb;
  color: #fff;
  padding: 9px 16px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}

.fm-primary:hover {
  background: #1d4ed8;
}

.fm-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.fm-secondary {
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  background: #fff;
  color: #172033;
  padding: 8px 14px;
  cursor: pointer;
}

.fm-secondary:hover {
  border-color: #2563eb;
  color: #2563eb;
}

.fm-secondary.danger:hover {
  border-color: #dc2626;
  color: #dc2626;
}

.fm-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 18px 0 12px;
}

.fm-filter-group {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.fm-chip {
  border: 1px solid #cbd5e1;
  border-radius: 999px;
  background: #fff;
  color: #475569;
  padding: 6px 14px;
  cursor: pointer;
  font-size: 13px;
}

.fm-chip.active {
  background: #2563eb;
  border-color: #2563eb;
  color: #fff;
}

.fm-search {
  flex: 0 0 320px;
  max-width: 40%;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 14px;
}

.fm-error {
  color: #dc2626;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
  padding: 8px 12px;
  margin: 8px 0;
  font-size: 13px;
}

.fm-list {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
}

.fm-list-head,
.fm-row {
  display: grid;
  grid-template-columns: minmax(140px, 1.2fr) minmax(220px, 2.6fr) 132px 148px 56px 176px;
  gap: 12px;
  align-items: center;
  padding: 12px 16px;
}

.fm-list-head {
  position: sticky;
  top: 0;
  z-index: 2;
  background: #f8fafc;
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
  border-bottom: 1px solid #e2e8f0;
}

.fm-row {
  border-bottom: 1px solid #f1f5f9;
  cursor: pointer;
}

.fm-row:hover {
  background: #f8fafc;
}

.fm-row .col-title {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fm-row .col-desc,
.fm-list-head .col-desc {
  color: #5b6478;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fm-row .col-desc {
  cursor: default;
}

.col-ref {
  text-align: center;
}

.fm-tag {
  display: inline-block;
  background: #e0edff;
  color: #1d4ed8;
  border-radius: 4px;
  padding: 1px 7px;
  font-size: 12px;
  margin-right: 4px;
}

.col-actions {
  display: flex;
  gap: 10px;
}

.fm-link {
  border: 0;
  background: none;
  color: #2563eb;
  cursor: pointer;
  padding: 0;
  font-size: 13px;
}

.fm-link.danger {
  color: #dc2626;
}

.fm-empty {
  padding: 28px 16px;
  text-align: center;
  color: #94a3b8;
}

.fm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  justify-content: flex-end;
  z-index: 50;
}

.fm-drawer {
  width: min(560px, 92vw);
  height: 100%;
  background: #fff;
  display: flex;
  flex-direction: column;
}

.fm-modal {
  width: min(640px, 94vw);
  max-height: 92vh;
  margin: auto;
  background: #fff;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
}

.fm-drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid #e2e8f0;
}

.fm-drawer-head h2 {
  margin: 0;
  font-size: 17px;
}

.fm-close {
  border: 0;
  background: none;
  font-size: 24px;
  line-height: 1;
  cursor: pointer;
  color: #94a3b8;
}

.fm-drawer-body,
.fm-form {
  padding: 18px 20px;
  overflow-y: auto;
  flex: 1;
}

.fm-field {
  margin-bottom: 16px;
}

.fm-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
  margin-bottom: 6px;
}

.fm-label em {
  color: #dc2626;
  font-style: normal;
}

.fm-text {
  margin: 0;
  white-space: pre-wrap;
}

.fm-muted {
  color: #94a3b8;
  font-size: 13px;
}

.fm-content {
  margin: 0;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px;
  max-height: 340px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 12.5px;
}

.fm-drawer-foot {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  padding: 14px 20px;
  border-top: 1px solid #e2e8f0;
}

.fm-form-row {
  display: block;
  margin-bottom: 16px;
}

.fm-form-row input[type='text'],
.fm-form-row input[type='search'],
.fm-form-row textarea {
  width: 100%;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 14px;
  font-family: inherit;
}

.fm-upload {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.fm-hidden-file {
  display: none;
}

.fm-subtabs {
  display: flex;
  gap: 8px;
  border-bottom: 1px solid #e2e8f0;
  margin-bottom: 16px;
}

.fm-subtab {
  border: 0;
  background: none;
  padding: 8px 4px;
  margin-bottom: -1px;
  border-bottom: 2px solid transparent;
  color: #64748b;
  font-weight: 600;
  font-size: 14px;
  cursor: pointer;
}

.fm-subtab.active {
  color: #2563eb;
  border-bottom-color: #2563eb;
}

.fm-mounts {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(220px, 300px) minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
  overflow: hidden;
}

.fm-mounts-tree {
  min-height: 0;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.fm-tree-search {
  flex: none;
  max-width: none;
  width: 100%;
}

.fm-group-list {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
}

.fm-group-item {
  border: 1px solid transparent;
  border-radius: 6px;
  background: none;
  text-align: left;
  padding: 8px 10px;
  cursor: pointer;
  color: #172033;
  font-size: 13px;
}

.fm-group-item:hover {
  background: #f1f5f9;
}

.fm-group-item.active {
  background: #e0edff;
  color: #1d4ed8;
  font-weight: 600;
}

.fm-tree-group-row {
  display: flex;
  align-items: center;
  gap: 2px;
}

.fm-tree-caret {
  border: 0;
  background: none;
  cursor: pointer;
  color: #94a3b8;
  width: 18px;
  flex: none;
  font-size: 12px;
}

.fm-tree-group-row .fm-group-item {
  flex: 1;
  font-weight: 600;
}

.fm-tree-items {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-left: 20px;
}

.fm-tree-item {
  border: 1px solid transparent;
  border-radius: 6px;
  background: none;
  text-align: left;
  padding: 6px 10px;
  cursor: pointer;
  color: #475569;
  font-size: 13px;
}

.fm-tree-item:hover {
  background: #f1f5f9;
}

.fm-tree-item.active {
  background: #e0edff;
  color: #1d4ed8;
  font-weight: 600;
}

.fm-item-version {
  color: #64748b;
  font-size: 11px;
  margin-right: 4px;
}

.fm-item-count {
  color: #94a3b8;
  font-size: 11px;
  margin-left: 6px;
}

.fm-tree-empty {
  padding: 4px 10px;
  text-align: left;
}

.fm-scope-tag {
  display: inline-block;
  background: #f1f5f9;
  color: #475569;
  border-radius: 4px;
  padding: 1px 7px;
  font-size: 12px;
  margin-right: 6px;
  vertical-align: middle;
}

.fm-popup-search {
  width: 100%;
  max-width: none;
  margin-bottom: 10px;
}

.fm-target-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 56vh;
  overflow-y: auto;
}

.fm-target-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.fm-target-row.indent {
  margin-left: 22px;
}

.fm-target-row.mounted {
  background: #f0f7ff;
  border-color: #bfdbfe;
}

.fm-target-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fm-scope-tag.item {
  background: #eef2f7;
  color: #64748b;
}

.fm-mounted-flag {
  flex: none;
  color: #2563eb;
  font-size: 12px;
}

.fm-group-item.ungrouped {
  display: block;
  padding: 8px 10px;
  color: #64748b;
  font-weight: 600;
}

.fm-tree-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding-top: 10px;
  margin-top: 8px;
  border-top: 1px solid #e2e8f0;
  font-size: 12px;
  color: #64748b;
}

.fm-tree-pager button {
  border: 1px solid #cbd5e1;
  background: #fff;
  border-radius: 6px;
  padding: 4px 10px;
  cursor: pointer;
}

.fm-tree-pager button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.fm-mounts-detail {
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
}

.fm-mounts-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.fm-mounts-head h2 {
  margin: 0;
  font-size: 16px;
}

.fm-mounted-list,
.fm-picker-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.fm-mounted-item,
.fm-picker-item {
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  cursor: default;
}

.fm-mounted-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.fm-mounted-main strong {
  font-size: 14px;
}

.fm-mounted-main .fm-muted {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
  word-break: break-word;
}

.fm-inherit-hint {
  flex: 0 0 auto;
  border-radius: 4px;
  background: #f1f5f9;
  color: #64748b;
  font-size: 11px;
  font-weight: 700;
  padding: 1px 6px;
  white-space: nowrap;
}

.fm-desc-tip {
  position: fixed;
  z-index: 95;
  max-width: 360px;
  max-height: 40vh;
  overflow: auto;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #0f172a;
  color: #f1f5f9;
  font-size: 12px;
  line-height: 1.6;
  padding: 8px 10px;
  white-space: pre-wrap;
  word-break: break-word;
  box-shadow: 0 14px 34px rgba(15, 23, 42, 0.28);
  pointer-events: none;
}

.fm-content-tip {
  position: fixed;
  z-index: 90;
  width: 400px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.24);
}

.fm-content-tip-title {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid #eef2f7;
  font-size: 13px;
  font-weight: 800;
  color: #0f172a;
}

.fm-content-tip-title > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fm-content-tip-close {
  flex: 0 0 auto;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 6px;
  background: #f1f5f9;
  color: #475569;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}

.fm-content-tip-close:hover {
  background: #e2e8f0;
}

.fm-eye {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 24px;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #64748b;
  padding: 0;
  cursor: pointer;
}

.fm-eye:hover {
  color: #1d4ed8;
  border-color: #bfdbfe;
  background: #eff6ff;
}

.fm-eye.active {
  color: #1d4ed8;
  border-color: #1d4ed8;
  background: #eff6ff;
}

.fm-content-tip-body {
  margin: 0;
  overflow: auto;
  padding: 10px 12px;
  white-space: pre-wrap;
  word-break: break-word;
  font: 12px/1.6 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  color: #1e293b;
}

.fm-mounted-targets {
  flex: none;
}

.fm-picker {
  margin-top: 20px;
  border-top: 1px dashed #e2e8f0;
  padding-top: 14px;
}

.fm-picker .fm-search {
  width: 100%;
  max-width: none;
  margin: 6px 0 10px;
}

.fm-link:disabled {
  color: #94a3b8;
  cursor: default;
}
</style>
