<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, request } from '../api/client'
import UserSearchSelect from '../components/UserSearchSelect.vue'
import { useCaseWorkbenchStore } from '../stores/caseWorkbench'
import type {
  RequirementGroup,
  RequirementItem,
  RequirementPoolPage,
  RequirementPoolItem,
} from '../types/case'
import { filterVisibleUsers } from '../utils/visibleUsers'

const store = useCaseWorkbenchStore()
const router = useRouter()

const groups = ref<RequirementGroup[]>([])
const groupFilter = ref('')
const expandedGroups = ref<Set<number>>(new Set())
const poolItems = ref<RequirementPoolItem[]>([])
const poolPage = ref(1)
const poolPageSize = 20
const poolTotal = ref(0)
const poolAttachableTotal = ref(0)
const poolFilterUserIds = ref<number[]>([])
const poolSprints = ref<{ id: string; name: string }[]>([])
const spaces = ref<{ projectKey: string; name: string }[]>([])
const spaceFilter = ref<string>('all')
const personFilter = ref<number | 'all'>('all')
const sprintFilter = ref<string>('all')
const testingOnly = ref(true)
const pulling = ref(false)
const selectedPoolIds = ref<number[]>([])
const selectedGroupId = ref<number | null>(null)
const targetGroupId = ref<number | null>(null)
const groupPickerOpen = ref(false)
const newGroupName = ref('')
const loading = ref(false)
const saving = ref(false)
const notice = ref('')
const error = ref('')
const visibleUsers = computed(() => filterVisibleUsers(store.users))

const spaceNameOf = (projectKey?: string | null) =>
  spaces.value.find((space) => space.projectKey === projectKey)?.name ?? projectKey ?? '未知空间'

const selectableUsers = computed(() => {
  const userIds = new Set<number>(poolFilterUserIds.value)
  return visibleUsers.value.filter((user) => userIds.has(user.id))
})

const sprintOptions = computed(() => poolSprints.value)

const visiblePoolItems = computed(() => poolItems.value)

const attachablePoolItems = computed(() => visiblePoolItems.value.filter((item) => !item.boundGroupId))
const selectedAttachablePoolIds = computed(() =>
  selectedPoolIds.value.filter((poolId) => attachablePoolItems.value.some((item) => item.id === poolId)),
)
const poolPageCount = computed(() => Math.max(1, Math.ceil(poolTotal.value / poolPageSize)))
const currentPoolPage = computed(() => Math.min(poolPage.value, poolPageCount.value))
const pagedPoolItems = computed(() => visiblePoolItems.value)
const poolPageStart = computed(() =>
  poolTotal.value ? (currentPoolPage.value - 1) * poolPageSize + 1 : 0,
)
const poolPageEnd = computed(() => Math.min(currentPoolPage.value * poolPageSize, poolTotal.value))

const canCreateGroup = computed(
  () => newGroupName.value.trim().length > 0 && selectedAttachablePoolIds.value.length > 0 && !saving.value,
)
const canAddToGroup = computed(
  () => Boolean(targetGroupId.value) && selectedAttachablePoolIds.value.length > 0 && !saving.value,
)
const targetGroupName = computed(
  () => groups.value.find((group) => group.id === targetGroupId.value)?.name ?? '请选择一级目录',
)

function normalizeError(err: unknown) {
  if (err instanceof ApiError) {
    return typeof err.detail === 'object' ? JSON.stringify(err.detail, null, 2) : String(err.detail)
  }
  return err instanceof Error ? err.message : String(err)
}

function itemState(item: RequirementPoolItem) {
  return item.boundGroupId ? `已纳入：${item.boundGroupName}` : '未进入目录'
}

function selectGroup(groupId: number) {
  selectedGroupId.value = groupId
  targetGroupId.value = groupId
}

function selectTargetGroup(groupId: number) {
  targetGroupId.value = groupId
  groupPickerOpen.value = false
}

function selectPersonFilter(value: number | string | null) {
  personFilter.value = typeof value === 'number' ? value : 'all'
}

function syncDependentPoolFilters() {
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

const filteredGroups = computed(() => {
  const keyword = groupFilter.value.trim().toLowerCase()
  if (!keyword) {
    return groups.value
  }
  return groups.value
    .map((group) => {
      if (group.name.toLowerCase().includes(keyword)) {
        return group
      }
      const items = group.items.filter(
        (item) =>
          item.title.toLowerCase().includes(keyword) ||
          (item.version ?? '').toLowerCase().includes(keyword),
      )
      return items.length ? { ...group, items } : null
    })
    .filter((group): group is RequirementGroup => group !== null)
})

function toggleGroupExpand(groupId: number) {
  const next = new Set(expandedGroups.value)
  if (next.has(groupId)) {
    next.delete(groupId)
  } else {
    next.add(groupId)
  }
  expandedGroups.value = next
}

function isGroupOpen(groupId: number) {
  return groupFilter.value.trim() !== '' || expandedGroups.value.has(groupId)
}

function goToCaseAssets(itemId: number) {
  void router.push({ name: 'case-assets', query: { item: String(itemId) } })
}

async function goPoolToCase(item: RequirementPoolItem) {
  if (item.boundItemId) {
    goToCaseAssets(item.boundItemId)
    return
  }
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await request<{ message: string; items: RequirementItem[] }>(
      '/api/v1/requirement-items/create-from-pool',
      { method: 'POST', body: { poolIds: [item.id] } as unknown as BodyInit },
    )
    const target = result.items[0]
    if (!target) {
      throw new Error('飞书项目未能生成二级需求')
    }
    await loadRequirementManagement()
    goToCaseAssets(target.id)
  } catch (err) {
    error.value = normalizeError(err)
  } finally {
    saving.value = false
  }
}

async function goPoolToFunctionMap(item: RequirementPoolItem) {
  if (item.boundItemId) {
    void router.push({ name: 'function-maps', query: { itemId: String(item.boundItemId) } })
    return
  }
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await request<{ message: string; items: RequirementItem[] }>(
      '/api/v1/requirement-items/create-from-pool',
      { method: 'POST', body: { poolIds: [item.id] } as unknown as BodyInit },
    )
    const target = result.items[0]
    if (!target) {
      throw new Error('飞书项目未能生成二级需求')
    }
    await loadRequirementManagement()
    void router.push({ name: 'function-maps', query: { itemId: String(target.id) } })
  } catch (err) {
    error.value = normalizeError(err)
  } finally {
    saving.value = false
  }
}

function togglePoolItem(poolId: number, checked: boolean) {
  if (checked) {
    if (!selectedPoolIds.value.includes(poolId)) {
      selectedPoolIds.value = [...selectedPoolIds.value, poolId]
    }
    return
  }
  selectedPoolIds.value = selectedPoolIds.value.filter((item) => item !== poolId)
}

function clearSelection() {
  selectedPoolIds.value = []
}

function changePoolPage(delta: number) {
  poolPage.value = Math.min(poolPageCount.value, Math.max(1, poolPage.value + delta))
  void loadPoolItems()
}

function syncSelectionsAfterLoad() {
  syncDependentPoolFilters()
  const attachableIds = new Set(attachablePoolItems.value.map((item) => item.id))
  selectedPoolIds.value = selectedPoolIds.value.filter((poolId) => attachableIds.has(poolId))
  if (!selectedGroupId.value || !groups.value.some((group) => group.id === selectedGroupId.value)) {
    selectedGroupId.value = groups.value[0]?.id ?? null
  }
  if (!targetGroupId.value || !groups.value.some((group) => group.id === targetGroupId.value)) {
    targetGroupId.value = selectedGroupId.value
  }
}

async function loadRequirementManagement() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([loadGroups(), loadPoolItems()])
    syncSelectionsAfterLoad()
  } catch (err) {
    error.value = normalizeError(err)
  } finally {
    loading.value = false
  }
}

async function loadGroups() {
  groups.value = await request<RequirementGroup[]>('/api/v1/requirements')
}

function buildPoolQuery() {
  const query = new URLSearchParams({
    page: String(poolPage.value),
    page_size: String(poolPageSize),
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
  return query.toString()
}

async function loadPoolItems() {
  const page = await request<RequirementPoolPage>(`/api/v1/requirement-pool?${buildPoolQuery()}`)
  poolItems.value = page.items
  poolTotal.value = page.total
  poolAttachableTotal.value = page.attachableTotal
  poolPage.value = page.page
  poolFilterUserIds.value = page.filterUserIds
  poolSprints.value = page.sprints
  syncSelectionsAfterLoad()
}

async function loadSpaces() {
  try {
    spaces.value = await store.listFeishuSpaces()
  } catch (err) {
    error.value = normalizeError(err)
  }
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

async function pullFeishu() {
  if (pulling.value) {
    return
  }
  pulling.value = true
  error.value = ''
  notice.value = ''
  try {
    const projectKeys = spaceFilter.value === 'all' ? undefined : [spaceFilter.value]
    const scope = spaceFilter.value === 'all' ? '全部空间' : spaceNameOf(spaceFilter.value)
    let job = await store.pullFeishuProject(projectKeys)
    notice.value = `已开始从「${scope}」后台拉取。`
    while (job.status === 'pending' || job.status === 'running') {
      await sleep(1500)
      job = await store.getFeishuPullJob(job.jobId)
      notice.value = job.message || `正在从「${scope}」拉取...`
    }
    if (job.status === 'failed') {
      throw new Error(job.error || job.message || '飞书项目拉取失败')
    }
    notice.value = `已从「${scope}」拉取：新增 ${job.created}、更新 ${job.updated}（共获取 ${job.fetched} 条）。`
    await loadRequirementManagement()
  } catch (err) {
    error.value = normalizeError(err)
  } finally {
    pulling.value = false
  }
}

// ---- 纳入弹窗：逐条填写版本（组内唯一）----
const naOpen = ref(false)
const naMode = ref<'create' | 'add'>('create')
const naRows = ref<{ poolId: number; title: string; version: string }[]>([])
const naError = ref('')

const naTakenVersions = computed<string[]>(() => {
  if (naMode.value !== 'add' || !targetGroupId.value) {
    return []
  }
  const group = groups.value.find((g) => g.id === targetGroupId.value)
  return (group?.items ?? []).map((item) => item.version).filter((v): v is string => Boolean(v))
})

function openIncludeDialog(mode: 'create' | 'add') {
  if (mode === 'create' && !canCreateGroup.value) {
    return
  }
  if (mode === 'add' && (!canAddToGroup.value || !targetGroupId.value)) {
    return
  }
  naMode.value = mode
  naError.value = ''
  const selectedSet = new Set(selectedAttachablePoolIds.value)
  naRows.value = attachablePoolItems.value
    .filter((item) => selectedSet.has(item.id))
    .map((item) => ({ poolId: item.id, title: item.title, version: '' }))
  naOpen.value = true
}

function closeIncludeDialog() {
  naOpen.value = false
  naRows.value = []
  naError.value = ''
}

async function confirmInclude() {
  const rows = naRows.value
  const versions = rows.map((r) => r.version.trim())
  if (versions.some((v) => !v)) {
    naError.value = '每个需求都必须填写版本。'
    return
  }
  if (new Set(versions).size !== versions.length) {
    naError.value = '本次纳入存在重复版本。'
    return
  }
  const clash = versions.filter((v) => naTakenVersions.value.includes(v))
  if (clash.length) {
    naError.value = `该一级目录下版本已存在：${clash.join('、')}`
    return
  }
  saving.value = true
  naError.value = ''
  error.value = ''
  notice.value = ''
  try {
    const items = rows.map((r) => ({ poolId: r.poolId, version: r.version.trim() }))
    const url =
      naMode.value === 'create'
        ? '/api/v1/requirement-groups/create-with-pool'
        : `/api/v1/requirement-groups/${targetGroupId.value}/add-pool`
    const body =
      naMode.value === 'create' ? { name: newGroupName.value.trim(), items } : { items }
    const result = await request<{ message: string; group: RequirementGroup }>(url, {
      method: 'POST',
      body: body as unknown as BodyInit,
    })
    notice.value = result.message
    selectedGroupId.value = result.group.id
    targetGroupId.value = result.group.id
    if (naMode.value === 'create') {
      newGroupName.value = ''
    }
    clearSelection()
    closeIncludeDialog()
    await loadRequirementManagement()
  } catch (err) {
    naError.value = normalizeError(err)
  } finally {
    saving.value = false
  }
}

async function unbindRequirementItem(item: RequirementItem) {
  if (!window.confirm(`将「${item.title}」移出一级目录？Case、报告和执行记录会保留。`)) {
    return
  }
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await request<{ message: string; item: RequirementItem }>(
      `/api/v1/requirement-items/${item.id}/unbind-group`,
      { method: 'POST' },
    )
    notice.value = result.message
    await loadRequirementManagement()
  } catch (err) {
    error.value = normalizeError(err)
  } finally {
    saving.value = false
  }
}

async function editItemVersion(item: { id: number; title: string; version?: string | null }) {
  const next = window.prompt(
    `修改「${item.title}」的版本。\n注意：不影响已导入用例的关联，但展示与对外引用会随之改变。`,
    item.version ?? '',
  )
  if (next === null) {
    return
  }
  const version = next.trim()
  if (!version || version === (item.version ?? '')) {
    return
  }
  error.value = ''
  notice.value = ''
  try {
    const result = await request<{ message: string; group: RequirementGroup }>(
      `/api/v1/requirement-items/${item.id}/version`,
      { method: 'PATCH', body: { version } as unknown as BodyInit },
    )
    notice.value = result.message
    await loadRequirementManagement()
  } catch (err) {
    error.value = normalizeError(err)
  }
}

// ---- Function Map：跳转到 Function Map「挂载管理」并选中对应节点 ----
function openFunctionMap(group: RequirementGroup) {
  void router.push({ name: 'function-maps', query: { groupId: String(group.id) } })
}

function openItemFunctionMap(item: RequirementItem) {
  void router.push({ name: 'function-maps', query: { itemId: String(item.id) } })
}

onMounted(() => {
  void store.loadUsers()
  void loadSpaces()
  void loadRequirementManagement()
})

watch([spaceFilter, personFilter, sprintFilter, testingOnly], () => {
  syncDependentPoolFilters()
  poolPage.value = 1
  clearSelection()
  void loadPoolItems()
})

watch(poolPageCount, (count) => {
  if (poolPage.value > count) {
    poolPage.value = count
  }
})
</script>

<template>
  <section class="project-management-page">
    <section class="project-column project-pool">
      <header class="section-head">
        <h1>飞书项目</h1>
        <span class="count-pill">{{ poolAttachableTotal }} 个未进入目录</span>
      </header>

      <div class="pool-toolbar">
        <select v-model="spaceFilter">
          <option value="all">全部空间</option>
          <option v-for="space in spaces" :key="space.projectKey" :value="space.projectKey">
            {{ space.name }}
          </option>
        </select>
        <UserSearchSelect
          class="pool-user-filter"
          :model-value="personFilter"
          :users="selectableUsers"
          all-label="全部测试人员"
          all-value="all"
          aria-label="筛选测试人员"
          @update:model-value="selectPersonFilter"
        />
        <select v-if="sprintOptions.length" v-model="sprintFilter">
          <option value="all">全部迭代排期</option>
          <option v-for="sp in sprintOptions" :key="sp.id" :value="sp.id">{{ sp.name }}</option>
        </select>
        <label class="pool-toggle">
          <input v-model="testingOnly" type="checkbox" />
          <span>只看测试中</span>
        </label>
        <button type="button" :disabled="pulling" @click="pullFeishu">
          {{ pulling ? '拉取中...' : spaceFilter === 'all' ? '全拉' : '拉取本空间' }}
        </button>
      </div>

      <div v-if="loading" class="empty-state">加载中...</div>
      <div v-else-if="!visiblePoolItems.length" class="empty-state">暂无飞书项目，点上方「拉取」从飞书同步。</div>

      <div v-else class="pool-list">
        <label
          v-for="item in pagedPoolItems"
          :key="item.id"
          class="pool-card"
          :class="{ mounted: item.boundGroupId, pending: !item.boundGroupId }"
          :title="item.externalKey"
        >
          <input
            type="checkbox"
            :checked="selectedPoolIds.includes(item.id)"
            :disabled="Boolean(item.boundGroupId)"
            @change="togglePoolItem(item.id, ($event.target as HTMLInputElement).checked)"
          />
          <div class="pool-content">
            <div class="pool-head">
              <span v-if="item.card?.number" class="pool-no">#{{ item.card.number }}</span>
              <span v-if="item.card?.status" class="pool-status">{{ item.card.status }}</span>
              <a
                v-if="item.card?.link"
                class="pool-link"
                :href="item.card.link"
                target="_blank"
                rel="noopener"
                title="在飞书项目中打开"
                @click.stop
              >飞书 ↗</a>
              <button
                type="button"
                class="pool-case-link"
                title="去 Case 资产导入/查看"
                :disabled="saving"
                @click.stop.prevent="goPoolToCase(item)"
              >去 Case</button>
              <button
                type="button"
                class="pool-case-link"
                title="到 Function Map 挂载管理"
                :disabled="saving"
                @click.stop.prevent="goPoolToFunctionMap(item)"
              >Function Map</button>
            </div>
            <h2>{{ item.title }}</h2>
            <div class="pool-meta">
              <span class="pool-tag">{{ spaceNameOf(item.sourceSpace) }}</span>
              <span v-if="item.card?.createdDate" class="pool-tag time">创建 {{ item.card.createdDate }}</span>
              <span v-for="sp in item.card?.sprints ?? []" :key="sp.id" class="pool-tag sprint">迭代 {{ sp.name }}</span>
            </div>
            <div v-if="item.card?.roles?.length" class="pool-roles">
              <span v-for="role in item.card.roles" :key="role.label" class="pool-role">
                <em>{{ role.label }}</em>{{ role.names.join('、') }}
              </span>
            </div>
            <strong>{{ itemState(item) }}</strong>
          </div>
        </label>
      </div>
      <div v-if="!loading && visiblePoolItems.length" class="pool-pagination">
        <span>{{ poolPageStart }}-{{ poolPageEnd }} / {{ poolTotal }}</span>
        <div>
          <button type="button" :disabled="currentPoolPage <= 1" @click="changePoolPage(-1)">上一页</button>
          <strong>{{ currentPoolPage }} / {{ poolPageCount }}</strong>
          <button type="button" :disabled="currentPoolPage >= poolPageCount" @click="changePoolPage(1)">下一页</button>
        </div>
      </div>
    </section>

    <section class="project-column directory-actions">
      <header>
        <h1>目录管理</h1>
      </header>

      <div class="directory-action-list">
        <label>
          新一级目录名称
          <input v-model="newGroupName" type="text" placeholder="例如：学习方法模块" />
        </label>
        <button type="button" :disabled="!canCreateGroup" @click="openIncludeDialog('create')">
          创建目录并纳入
        </button>
        <div class="directory-field">
          纳入已有一级目录
          <div class="group-picker" :class="{ open: groupPickerOpen }">
            <button
              type="button"
              class="group-picker-trigger"
              :disabled="!groups.length"
              @click="groupPickerOpen = !groupPickerOpen"
            >
              <span>{{ targetGroupName }}</span>
              <em>{{ groupPickerOpen ? '▴' : '▾' }}</em>
            </button>
            <div v-if="groupPickerOpen" class="group-picker-menu">
              <button
                v-for="group in groups"
                :key="group.id"
                type="button"
                class="group-picker-option"
                :class="{ selected: group.id === targetGroupId }"
                @click="selectTargetGroup(group.id)"
              >
                <strong>{{ group.name }}</strong>
                <span>{{ group.items.length }} 个二级需求</span>
              </button>
            </div>
          </div>
        </div>
        <button type="button" :disabled="!canAddToGroup" @click="openIncludeDialog('add')">
          纳入已有目录
        </button>
      </div>

      <pre v-if="notice || error" class="result-state">{{ notice || error }}</pre>
    </section>

    <section class="project-column directory-result">
      <header>
        <h1>一级目录</h1>
      </header>
      <input v-model="groupFilter" class="group-filter" type="text" placeholder="筛选目录 / 需求 / 版本" />

      <div v-if="loading" class="empty-state">加载中...</div>
      <div v-else-if="!filteredGroups.length" class="empty-state">暂无一级目录。</div>

      <div v-else class="group-list">
        <article
          v-for="group in filteredGroups"
          :key="group.id"
          class="group-card"
          :class="{ selected: group.id === selectedGroupId }"
          @click="selectGroup(group.id)"
        >
          <div class="group-card-head">
            <button type="button" class="group-caret" @click.stop="toggleGroupExpand(group.id)">
              {{ isGroupOpen(group.id) ? '▾' : '▸' }}
            </button>
            <div class="group-card-title">
              <h2>{{ group.name }}</h2>
              <p>{{ group.items.length }} 个二级需求</p>
            </div>
            <button type="button" class="fm-button" @click.stop="openFunctionMap(group)">Function Map</button>
          </div>
          <div v-if="isGroupOpen(group.id)" class="group-item-list">
            <div
              v-for="item in group.items"
              :key="item.id"
              class="group-item-row"
              :class="{ selected: group.id === selectedGroupId }"
              @click.stop="selectGroup(group.id)"
            >
              <span v-if="item.version" class="item-version">{{ item.version }}</span>
              <span class="item-title">{{ item.title }}</span>
              <button type="button" class="item-go" title="去 Case 资产导入/查看" @click.stop="goToCaseAssets(item.id)">去 Case</button>
              <button type="button" class="item-go" title="到 Function Map 挂载管理" @click.stop="openItemFunctionMap(item)">Function Map</button>
              <button type="button" class="item-edit" title="修改版本" @click.stop="editItemVersion(item)">改版本</button>
              <button type="button" class="item-edit" title="移出一级目录" @click.stop="unbindRequirementItem(item)">移出</button>
            </div>
          </div>
        </article>
      </div>
    </section>

    <div v-if="naOpen" class="modal-mask">
      <section class="na-modal">
        <div class="modal-head">
          <div>
            <h2>{{ naMode === 'create' ? `新建目录「${newGroupName.trim()}」并纳入` : '纳入已有目录' }}</h2>
            <p>每个需求必须填写版本，且在该一级目录下不可重复。</p>
          </div>
          <button type="button" @click="closeIncludeDialog">关闭</button>
        </div>
        <div class="na-body">
          <p v-if="naTakenVersions.length" class="na-taken">该目录已有版本：{{ naTakenVersions.join('、') }}</p>
          <ul class="na-list">
            <li v-for="row in naRows" :key="row.poolId" class="na-row">
              <span class="na-title">{{ row.title }}</span>
              <input v-model="row.version" type="text" placeholder="版本，如 1.1" class="na-input" />
            </li>
          </ul>
          <pre v-if="naError" class="result-state">{{ naError }}</pre>
        </div>
        <div class="na-actions">
          <button type="button" class="na-cancel" @click="closeIncludeDialog">取消</button>
          <button type="button" class="na-confirm" :disabled="saving" @click="confirmInclude">
            {{ saving ? '处理中...' : '确认纳入' }}
          </button>
        </div>
      </section>
    </div>

  </section>
</template>

<style scoped>
.project-management-page {
  width: 100%;
  min-width: 0;
  height: calc(100vh - 92px);
  box-sizing: border-box;
  padding: 12px 16px;
  display: grid;
  grid-template-columns: minmax(420px, 1.2fr) minmax(300px, 0.7fr) minmax(420px, 1.1fr);
  gap: 16px;
  align-items: stretch;
  overflow: hidden;
}

.project-column {
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid #d7dfec;
  border-radius: 8px;
  background: #fff;
  padding: 16px;
}

.project-column > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.project-column h1,
.project-column h2,
.project-column p {
  margin: 0;
}

.project-column h1 {
  font-size: 16px;
}

.project-column h2 {
  font-size: 14px;
  line-height: 1.4;
}

.project-column p,
.project-column span {
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
}

.count-pill {
  flex: 0 0 auto;
  border-radius: 999px;
  background: #ecfdf5;
  color: #047857;
  padding: 2px 8px;
}

.pool-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}

.pool-toolbar select {
  flex: 1 1 0;
  min-width: 0;
  height: 34px;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  padding: 0 9px;
  color: #172033;
  font-size: 13px;
}

.pool-toolbar .pool-user-filter {
  flex: 1 1 0;
  min-width: 0;
}

.pool-toggle {
  flex: 0 0 auto;
  height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #334155;
  padding: 0 9px;
  font-size: 12px;
  font-weight: 800;
  white-space: nowrap;
}

.pool-toggle input {
  margin: 0;
}

.pool-toolbar button {
  flex: 0 0 auto;
  height: 34px;
  border: 0;
  border-radius: 6px;
  background: #0f766e;
  color: #fff;
  font-weight: 700;
  padding: 0 16px;
  cursor: pointer;
}

.pool-toolbar button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.pool-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pool-no {
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 800;
}

.pool-status {
  border-radius: 4px;
  background: #fef3c7;
  color: #92400e;
  font-size: 11px;
  font-weight: 700;
  padding: 1px 7px;
}

.pool-link {
  margin-left: auto;
  color: #2563eb;
  font-size: 11px;
  font-weight: 700;
  text-decoration: none;
}

.pool-link:hover {
  text-decoration: underline;
}

.pool-case-link {
  flex: 0 0 auto;
  border: 1px solid #bfdbfe;
  border-radius: 5px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 11px;
  font-weight: 800;
  padding: 2px 7px;
  cursor: pointer;
}

.pool-case-link:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.pool-roles {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
}

.pool-role {
  color: #334155;
  font-size: 12px;
  font-weight: 600;
}

.pool-role em {
  color: #94a3b8;
  font-style: normal;
  font-weight: 700;
  margin-right: 3px;
}

.pool-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.pool-tag.time {
  background: #f1f5f9;
  color: #475569;
}

.pool-tag.sprint {
  background: #f0fdfa;
  color: #0f766e;
}

.pool-tag {
  border-radius: 999px;
  background: #eef2ff;
  color: #4338ca;
  font-size: 11px;
  font-weight: 700;
  padding: 1px 8px;
}

.pool-tag.owner {
  background: #ecfdf5;
  color: #047857;
}

.pool-list,
.group-list {
  display: grid;
  gap: 8px;
  flex: 1 1 auto;
  min-height: 0;
  align-content: start;
  grid-auto-rows: max-content;
  overflow-y: auto;
  padding-right: 4px;
}

.pool-pagination {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border-top: 1px solid #e2e8f0;
  padding-top: 10px;
  margin-top: 10px;
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.pool-pagination > div {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pool-pagination button {
  height: 28px;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #26364d;
  font-size: 12px;
  font-weight: 800;
  padding: 0 10px;
  cursor: pointer;
}

.pool-pagination button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.pool-card {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 10px;
  border: 1px solid #d7dfec;
  border-radius: 6px;
  background: #fff;
  padding: 10px;
  color: #172033;
  cursor: pointer;
  margin: 0;
}

.pool-card.mounted {
  background: #f8fafc;
  color: #64748b;
}

.pool-card > input {
  width: 16px;
  height: 16px;
  margin: 3px 0 0;
}

.pool-content {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.pool-content h2 {
  font-size: 12px;
  line-height: 1.4;
}

.pool-content p {
  color: #64748b;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pool-content strong {
  color: #2563eb;
  font-size: 12px;
}

.pool-card.mounted .pool-content strong {
  color: #475569;
}

.directory-actions {
  display: grid;
  gap: 0;
  align-self: start;
  overflow: visible;
}

.directory-action-list {
  display: grid;
  gap: 10px;
  margin-top: 2px;
}

.directory-action-list label,
.directory-field {
  display: grid;
  gap: 6px;
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.directory-field {
  position: relative;
}

.directory-action-list input,
.directory-action-list select {
  height: 36px;
  width: 100%;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  padding: 0 9px;
  color: #172033;
  font-size: 13px;
  line-height: normal;
}

.group-picker {
  position: relative;
}

.group-picker-trigger {
  height: 36px;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #172033;
  padding: 0 9px;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  text-align: left;
}

.group-picker.open .group-picker-trigger {
  border-color: #2563eb;
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.12);
}

.group-picker-trigger span {
  min-width: 0;
  overflow: hidden;
  color: #172033;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.group-picker-trigger em {
  flex: 0 0 auto;
  color: #64748b;
  font-style: normal;
}

.group-picker-menu {
  position: absolute;
  z-index: 20;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  max-height: 240px;
  overflow-y: auto;
  border: 1px solid #cfd8e6;
  border-radius: 7px;
  background: #fff;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.16);
  padding: 6px;
}

.group-picker-option {
  min-height: 40px;
  width: 100%;
  display: grid;
  gap: 3px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #172033;
  padding: 7px 8px;
  text-align: left;
  cursor: pointer;
}

.group-picker-option:hover,
.group-picker-option.selected {
  background: #eff6ff;
}

.group-picker-option strong {
  min-width: 0;
  overflow: hidden;
  color: #172033;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.group-picker-option span {
  color: #64748b;
  font-size: 11px;
}

.directory-action-list > button {
  height: 38px;
  border: 0;
  border-radius: 6px;
  background: #2563eb;
  color: #fff;
  font-weight: 700;
  cursor: pointer;
}

.directory-action-list > button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.result-state {
  margin: 12px 0 0;
  max-height: 280px;
  overflow: auto;
  white-space: pre-wrap;
  border: 0;
  border-radius: 6px;
  background: #0f172a;
  color: #e2e8f0;
  padding: 10px;
  font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.group-card {
  border: 1px solid #d7dfec;
  border-left: 5px solid #0f766e;
  border-radius: 6px;
  background: #fff;
  padding: 10px;
  cursor: pointer;
}

.group-card.selected {
  border-color: #2563eb;
  border-left-color: #2563eb;
  background: #edf5ff;
}

.group-filter {
  height: 32px;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  padding: 0 9px;
  font-size: 13px;
  margin-bottom: 10px;
}

.group-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.group-caret {
  flex: 0 0 auto;
  border: 0;
  background: transparent;
  color: #64748b;
  font-size: 12px;
  cursor: pointer;
  padding: 2px 4px;
}

.group-card-title {
  flex: 1 1 auto;
  min-width: 0;
}

.item-go {
  flex: 0 0 auto;
  border: 1px solid #bfdbfe;
  border-radius: 5px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  cursor: pointer;
}

.group-card-head p {
  margin-top: 6px;
}

.group-item-list {
  margin-top: 10px;
  display: grid;
  gap: 8px;
}

.group-item-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  border-radius: 6px;
  background: #f8fafc;
  color: #334155;
  padding: 6px 8px;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.group-item-row.selected {
  background: #e5efff;
}

.item-version {
  flex: 0 0 auto;
  border-radius: 4px;
  background: #eef2f7;
  color: #475569;
  border: 1px solid #dbe3ee;
  font-size: 11px;
  font-weight: 700;
  padding: 1px 6px;
}

.item-title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-edit {
  flex: 0 0 auto;
  border: 1px solid #cfd8e6;
  border-radius: 5px;
  background: #fff;
  color: #475569;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  cursor: pointer;
}

.item-edit:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.na-modal {
  width: min(560px, 94vw);
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 24px 80px rgba(15, 23, 42, 0.32);
}

.na-body {
  padding: 14px 18px;
  overflow: auto;
  display: grid;
  gap: 10px;
}

.na-taken {
  margin: 0;
  color: #92400e;
  font-size: 12px;
  font-weight: 700;
}

.na-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
}

.na-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.na-title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: #172033;
}

.na-input {
  flex: 0 0 130px;
  height: 32px;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  padding: 0 9px;
  font-size: 13px;
}

.na-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  border-top: 1px solid #e2e8f0;
  padding: 12px 18px;
}

.na-cancel {
  height: 34px;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #26364d;
  font-weight: 800;
  padding: 0 14px;
  cursor: pointer;
}

.na-confirm {
  height: 34px;
  border: 0;
  border-radius: 6px;
  background: #2563eb;
  color: #fff;
  font-weight: 800;
  padding: 0 16px;
  cursor: pointer;
}

.na-confirm:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

@media (max-width: 1180px) {
  .project-management-page {
    grid-template-columns: 1fr;
  }
}

.fm-button {
  flex: 0 0 auto;
  height: 28px;
  border: 1px solid #bfdbfe;
  border-radius: 6px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 800;
  padding: 0 10px;
  cursor: pointer;
}

.fm-modal {
  width: min(720px, 94vw);
  max-height: 86vh;
  overflow: auto;
  position: relative;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 24px 80px rgba(15, 23, 42, 0.32);
}

.fm-body {
  padding: 16px 20px;
  display: grid;
  gap: 12px;
}

.fm-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.fm-toolbar > button {
  height: 32px;
  border: 1px solid #2563eb;
  border-radius: 6px;
  background: #2563eb;
  color: #fff;
  font-weight: 800;
  padding: 0 14px;
  cursor: pointer;
}

.fm-toolbar > button:disabled {
  border-color: #94a3b8;
  background: #94a3b8;
  cursor: not-allowed;
}

.fm-toolbar > span {
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
}

.fm-file-input {
  display: none;
}

.fm-file-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
}

.fm-file-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid #d7dfec;
  border-radius: 8px;
  background: #f8fafc;
  padding: 10px 12px;
}

.fm-file-meta {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.fm-file-meta strong {
  overflow: hidden;
  font-size: 13px;
  color: #0f172a;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fm-file-meta span {
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
}

.fm-file-actions {
  flex: 0 0 auto;
  display: flex;
  gap: 8px;
}

.fm-file-actions button {
  height: 30px;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #26364d;
  font-size: 12px;
  font-weight: 800;
  padding: 0 12px;
  cursor: pointer;
}

.fm-file-actions .fm-danger {
  border-color: #fecaca;
  background: #fff1f2;
  color: #b91c1c;
}

.fm-actions {
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid #e2e8f0;
  padding: 12px 20px;
}

.fm-actions button {
  height: 34px;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #26364d;
  font-weight: 800;
  padding: 0 14px;
  cursor: pointer;
}

.fm-viewer-mask {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: 32px;
  background: rgba(15, 23, 42, 0.55);
}

.fm-viewer {
  width: min(760px, 92vw);
  max-height: 82vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 24px 80px rgba(15, 23, 42, 0.34);
}

.fm-viewer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid #e2e8f0;
}

.fm-viewer-head strong {
  font-size: 14px;
}

.fm-viewer-head button {
  height: 30px;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #26364d;
  font-weight: 800;
  padding: 0 12px;
  cursor: pointer;
}

.fm-viewer-body {
  margin: 0;
  overflow: auto;
  padding: 14px 16px;
  white-space: pre-wrap;
  word-break: break-word;
  font: 13px/1.6 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  color: #1e293b;
}
</style>
