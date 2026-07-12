<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type {
  CoverageLane,
  CoverageState,
  ExecutionStatus,
  ExecutionTarget,
} from '../../types/case'
import type { QuickCaseItem } from '../../types/quick'

const props = defineProps<{
  cases: QuickCaseItem[]
  selectedCaseId: number | null
  scrollToCaseId: number | null
  scrollRequestKey: number
  selectedCaseIds: number[]
  statusFilter: ExecutionStatus | 'attention' | 'all'
  totalCaseCount: number
  nowMs: number
  bugContextReady: boolean
  preparingBugDraftIds: number[]
}>()

const emit = defineEmits<{
  selectCase: [caseId: number]
  setFilter: [filter: ExecutionStatus | 'attention' | 'all']
  toggleCase: [caseId: number, checked: boolean]
  toggleAll: [checked: boolean]
  executeSelected: []
  stopSelected: []
  repairSelected: []
  repairCase: [caseId: number]
  submitBug: [caseId: number]
  executeCase: [caseId: number]
  cycleCoverage: [caseId: number, lane: CoverageLane]
  updateStatus: [caseId: number, status: ExecutionStatus]
  updateTarget: [caseId: number, target: ExecutionTarget]
  openReport: [caseId: number]
  editCase: [caseId: number]
}>()

const statusFilters: Array<{ value: ExecutionStatus | 'attention' | 'all'; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'not_run', label: '未执行' },
  { value: 'running', label: '执行中' },
  { value: 'passed', label: '通过' },
  { value: 'failed', label: '失败' },
  { value: 'attention', label: '待确认' },
]

const statusLabels: Record<ExecutionStatus, string> = {
  not_run: '未执行',
  running: '执行中',
  passed: '通过',
  failed: '失败',
}

const targetLabels: Record<ExecutionTarget, string> = {
  app: 'AI Phone',
  web: 'AI Web',
  api: 'AI API',
  mixed: 'AI Hybrid',
  manual: '人工',
  unknown: '人工',
}

const channelLabels: Record<ExecutionTarget, string> = {
  app: 'App',
  web: 'Web',
  api: 'API',
  mixed: 'Hybrid',
  manual: '人工',
  unknown: '人工',
}

const failureTypeMetaMap: Record<string, { label: string; className: string }> = {
  execution_failed: { label: '执行失败', className: 'failure-execution' },
  environment_failure: { label: '执行失败', className: 'failure-execution' },
  assertion_failed: { label: '断言失败', className: 'failure-assertion' },
  business_failure: { label: '业务失败', className: 'failure-business' },
  case_step_failure: { label: '步骤问题', className: 'failure-case-step' },
  执行失败: { label: '执行失败', className: 'failure-execution' },
  断言失败: { label: '断言失败', className: 'failure-assertion' },
  业务失败: { label: '业务失败', className: 'failure-business' },
  步骤问题: { label: '步骤问题', className: 'failure-case-step' },
  flaky_failure: { label: '偶发波动', className: 'failure-flaky' },
  偶发波动: { label: '偶发波动', className: 'failure-flaky' },
}

// 覆盖标记：纯展示提醒，不参与执行流转。三态点按循环。
// 覆盖泳道按执行器区分：app → 安卓/iOS/鸿蒙；web → Chrome/Safari/Firefox；其余执行器不展示。
const coverageLanesByTarget: Partial<Record<ExecutionTarget, Array<{ key: CoverageLane; label: string }>>> = {
  app: [
    { key: 'android', label: 'Android' },
    { key: 'ios', label: 'iOS' },
    { key: 'harmony', label: 'Harmony' },
  ],
  web: [
    { key: 'chrome', label: 'Chrome' },
    { key: 'safari', label: 'Safari' },
    { key: 'firefox', label: 'Firefox' },
  ],
}

const coverageStateLabels: Record<CoverageState, string> = {
  none: '未执行',
  passed: '通过',
  failed: '失败',
}

// 单 path 官方 glyph（24x24 viewBox，fill=currentColor，颜色由状态 class 决定）。
// harmony（无单符号官方 logo）与 safari（官方 path 过大）改用模板内的几何图形渲染。
type PathLane = 'android' | 'ios' | 'chrome' | 'firefox'
const laneIconPaths: Record<PathLane, string> = {
  android:
    'M17.523 15.341a.999.999 0 110-1.999.999.999 0 010 1.999m-11.046 0a.999.999 0 110-1.999.999.999 0 010 1.999m11.405-6.02l1.997-3.459a.416.416 0 00-.72-.415l-2.022 3.503A12.31 12.31 0 0012 7.851c-1.85 0-3.59.393-5.137 1.073L4.841 5.421a.416.416 0 10-.72.415L6.118 9.32C2.689 11.187.343 14.659 0 18.761h24c-.343-4.102-2.689-7.574-6.118-9.44',
  ios: 'M12.152 6.896c-.948 0-2.415-1.078-3.96-1.04-2.04.027-3.91 1.183-4.961 3.014-2.117 3.675-.546 9.103 1.519 12.09 1.013 1.454 2.208 3.09 3.792 3.039 1.52-.065 2.09-.987 3.935-.987 1.831 0 2.35.987 3.96.948 1.637-.026 2.676-1.48 3.676-2.948 1.156-1.688 1.636-3.325 1.662-3.415-.039-.013-3.182-1.221-3.22-4.857-.026-3.04 2.48-4.494 2.597-4.559-1.429-2.09-3.623-2.324-4.39-2.376-2-.156-3.675 1.09-4.61 1.09zm3.379-3.066c.843-1.012 1.4-2.427 1.245-3.83-1.207.052-2.662.805-3.532 1.818-.78.896-1.454 2.338-1.273 3.714 1.338.104 2.715-.688 3.559-1.701',
  chrome:
    'M12 0C8.21 0 4.831 1.757 2.632 4.501l3.953 6.848A5.454 5.454 0 0 1 12 6.545h10.691A12 12 0 0 0 12 0zM1.931 5.47A11.943 11.943 0 0 0 0 12c0 6.012 4.42 10.991 10.189 11.864l3.953-6.847a5.45 5.45 0 0 1-6.865-2.29zm13.342 2.166a5.446 5.446 0 0 1 1.45 7.09l.002.001h-.002l-5.344 9.257c.206.01.413.016.621.016 6.627 0 12-5.373 12-12 0-1.54-.29-3.011-.818-4.364zM12 16.364a4.364 4.364 0 1 1 0-8.728 4.364 4.364 0 0 1 0 8.728Z',
  firefox:
    'M8.824 7.287c.008 0 .004 0 0 0zm-2.8-1.4c.006 0 .003 0 0 0zm16.754 2.161c-.505-1.215-1.53-2.528-2.333-2.943.654 1.283 1.033 2.57 1.177 3.53l.002.02c-1.314-3.278-3.544-4.6-5.366-7.477-.091-.147-.184-.292-.273-.446a3.545 3.545 0 01-.13-.24 2.118 2.118 0 01-.172-.46.03.03 0 00-.027-.03.038.038 0 00-.021 0l-.006.001a.037.037 0 00-.01.005L15.624 0c-2.585 1.515-3.657 4.168-3.932 5.856a6.197 6.197 0 00-2.305.587.297.297 0 00-.147.37c.057.162.24.24.396.17a5.622 5.622 0 012.008-.523l.067-.005a5.847 5.847 0 011.957.222l.095.03a5.816 5.816 0 01.616.228c.08.036.16.073.238.112l.107.055a5.835 5.835 0 01.368.211 5.953 5.953 0 012.034 2.104c-.62-.437-1.733-.868-2.803-.681 4.183 2.09 3.06 9.292-2.737 9.02a5.164 5.164 0 01-1.513-.292 4.42 4.42 0 01-.538-.232c-1.42-.735-2.593-2.121-2.74-3.806 0 0 .537-2 3.845-2 .357 0 1.38-.998 1.398-1.287-.005-.095-2.029-.9-2.817-1.677-.422-.416-.622-.616-.8-.767a3.47 3.47 0 00-.301-.227 5.388 5.388 0 01-.032-2.842c-1.195.544-2.124 1.403-2.8 2.163h-.006c-.46-.584-.428-2.51-.402-2.913-.006-.025-.343.176-.389.206-.406.29-.787.616-1.136.974-.397.403-.76.839-1.085 1.303a9.816 9.816 0 00-1.562 3.52c-.003.013-.11.487-.19 1.073-.013.09-.026.181-.037.272a7.8 7.8 0 00-.069.667l-.002.034-.023.387-.001.06C.386 18.795 5.593 24 12.016 24c5.752 0 10.527-4.176 11.463-9.661.02-.149.035-.298.052-.448.232-1.994-.025-4.09-.753-5.844z',
}

function lanesForCase(item: QuickCaseItem): Array<{ key: CoverageLane; label: string }> {
  return coverageLanesByTarget[item.executionTarget] ?? []
}

function coverageState(item: QuickCaseItem, lane: CoverageLane): CoverageState {
  return item.coverage?.[lane] ?? 'none'
}

function bugCount(item: QuickCaseItem): number {
  return item.bugs?.length ?? 0
}

function coverageClass(item: QuickCaseItem, lane: CoverageLane) {
  return `coverage-${coverageState(item, lane)}`
}

function isPathLane(lane: CoverageLane): lane is PathLane {
  return lane === 'android' || lane === 'ios' || lane === 'chrome' || lane === 'firefox'
}

const selectedSet = computed(() => new Set(props.selectedCaseIds))
const caseListRef = ref<HTMLElement | null>(null)
// 置顶卡片时，列表顶部留出的空白；与卡片栅格间距（gap: 8px）一致，保证上一张卡片正好被顶出、不露尾巴。
const CASE_TOP_GAP = 8
const selectedVisibleCases = computed(() => props.cases.filter((item) => selectedSet.value.has(item.id)))
const allSelected = computed(
  () => props.cases.length > 0 && props.cases.every((item) => selectedSet.value.has(item.id)),
)
const selectedCount = computed(() => selectedVisibleCases.value.length)
const stoppableCount = computed(
  () => selectedVisibleCases.value.filter((item) => item.executionStatus === 'running').length,
)
const repairableCount = computed(
  () => selectedVisibleCases.value.filter((item) => item.executionStatus === 'failed').length,
)
const preparingBugDraftIdSet = computed(() => new Set(props.preparingBugDraftIds))

function bugDraftPreparing(item: QuickCaseItem): boolean {
  return preparingBugDraftIdSet.value.has(item.id)
}

function bugButtonTitle(item: QuickCaseItem): string {
  if (bugCount(item) > 0) {
    return '点击查看已提交 bug / 再提一条'
  }
  if (!props.bugContextReady) {
    return '提交 bug 前需要先选择右上角当前用户，并绑定有效的飞书需求链接'
  }
  return item.bugDraftReady ? '' : '后台预填准备中…'
}

function elapsedText(item: QuickCaseItem) {
  if (item.executionStatus !== 'running' || !item.executionStartedAt) {
    return ''
  }
  const started = new Date(item.executionStartedAt).getTime()
  if (!Number.isFinite(started)) {
    return ''
  }
  const totalSeconds = Math.max(0, Math.floor((props.nowMs - started) / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60)
    const restMinutes = minutes % 60
    return `${hours}时${restMinutes}分`
  }
  return minutes > 0 ? `${minutes}分${seconds}秒` : `${seconds}秒`
}

function statusClass(status: ExecutionStatus) {
  return `status-${status.replace('_', '-')}`
}

function executeButtonText(item: QuickCaseItem) {
  if (item.executionStatus === 'running') {
    return '已推送'
  }
  return '执行'
}

function failureTypeMeta(item: QuickCaseItem) {
  if (item.executionStatus !== 'failed') {
    return null
  }
  // 失败必出一个标签：默认“执行失败”，报告分析后才会是断言/业务等。
  return failureTypeMetaMap[item.failureType || ''] || failureTypeMetaMap['执行失败']
}

function scrollCaseIntoView(caseId: number | null) {
  const list = caseListRef.value
  if (!caseId || !list) {
    return
  }

  const target = list.querySelector<HTMLElement>(`[data-case-id="${caseId}"]`)
  if (!target) {
    return
  }

  // 只滚动 case 列表自身这一块滚动区，整页与上方标题行保持不动。
  const top = list.scrollTop + target.getBoundingClientRect().top - list.getBoundingClientRect().top - CASE_TOP_GAP
  list.scrollTo({ top, behavior: 'smooth' })
}

watch(
  () => props.scrollRequestKey,
  async (requestKey) => {
    if (!props.scrollToCaseId || requestKey <= 0) {
      return
    }
    await nextTick()
    window.requestAnimationFrame(() => scrollCaseIntoView(props.scrollToCaseId))
  },
  { flush: 'post' },
)

</script>

<template>
  <aside class="panel case-panel">
    <div class="panel-head">
      <h2>Case 操作</h2>
      <span>{{ cases.length }} / {{ totalCaseCount }} 条</span>
    </div>

    <div class="case-toolbar">
      <div class="case-filter-tabs">
          <button
            v-for="item in statusFilters"
            :key="item.value"
            type="button"
            :data-filter="item.value"
            :class="{ active: statusFilter === item.value }"
            @click="emit('setFilter', item.value)"
        >
          {{ item.label }}
        </button>
      </div>
      <div class="case-bulk-row">
        <label class="bulk-check">
          <input
            type="checkbox"
            data-control="select-visible-cases"
            :checked="allSelected"
            :disabled="!cases.length"
            @change="emit('toggleAll', ($event.target as HTMLInputElement).checked)"
          />
          勾选当前列表
        </label>
        <div class="bulk-actions">
          <button data-control="execute-selected" type="button" :disabled="selectedCount === 0" @click="emit('executeSelected')">
            执行
          </button>
          <button data-control="stop-selected" type="button" :disabled="stoppableCount === 0" @click="emit('stopSelected')">
            停止执行
          </button>
          <button data-control="repair-selected" type="button" :disabled="repairableCount === 0" @click="emit('repairSelected')">
            诊断修复
          </button>
          <span>已选 {{ selectedCount }} / {{ cases.length }}</span>
        </div>
      </div>
    </div>

    <div ref="caseListRef" class="case-list">
      <article
        v-for="item in cases"
        :key="item.id"
        class="case-card"
        :data-case-id="item.id"
        :class="[
          statusClass(item.executionStatus),
          { selected: selectedCaseId === item.id, scanning: item.executionStatus === 'running' },
        ]"
        @click="emit('selectCase', item.id)"
      >
        <label class="case-select-cell" @click.stop>
          <input
            type="checkbox"
            data-control="select-case"
            :checked="selectedSet.has(item.id)"
            @change="emit('toggleCase', item.id, ($event.target as HTMLInputElement).checked)"
          />
        </label>
        <div class="case-card-content">
          <div class="case-card-main">
            <div>
              <div class="case-card-title">
                <strong>{{ item.displayNo || item.ordinal }}. {{ item.rawTitle }}</strong>
              </div>
              <span class="case-path">{{ item.path }}</span>
              <div class="case-tags">
                <b class="status-pill" :class="statusClass(item.executionStatus)">{{ statusLabels[item.executionStatus] }}</b>
                <b v-if="elapsedText(item)" class="elapsed-pill">已执行 {{ elapsedText(item) }}</b>
                <b
                  v-if="failureTypeMeta(item)"
                  class="failure-type-pill"
                  :class="failureTypeMeta(item)?.className"
                  :title="item.failureSummary || ''"
                >
                  {{ failureTypeMeta(item)?.label }}
                </b>
                <b class="executor-pill">{{ channelLabels[item.executionTarget] }}</b>
                <b v-if="item.attentionReason" class="reason-pill reason-changed">{{ item.attentionReason }}</b>
              </div>
            </div>
          </div>
          <div class="case-controls" @click.stop>
            <label class="compact-status-control" title="执行状态">
              <select
                :value="item.executionStatus"
                @change="emit('updateStatus', item.id, ($event.target as HTMLSelectElement).value as ExecutionStatus)"
              >
                <option value="not_run">未执行</option>
                <option value="running">执行中</option>
                <option value="passed">通过</option>
                <option value="failed">失败</option>
              </select>
            </label>
            <div class="executor-control">
              <button
                class="push-run-button"
                data-control="execute-case"
                type="button"
                :disabled="item.executionStatus === 'running'"
                @click="emit('executeCase', item.id)"
              >
                {{ executeButtonText(item) }}
              </button>
              <label title="执行器">
                <select
                  :value="item.executionTarget"
                  @change="emit('updateTarget', item.id, ($event.target as HTMLSelectElement).value as ExecutionTarget)"
                >
                  <option value="app">{{ targetLabels.app }}</option>
                  <option value="web">{{ targetLabels.web }}</option>
                  <option value="api">{{ targetLabels.api }}</option>
                  <option value="mixed">{{ targetLabels.mixed }}</option>
                  <option value="manual">{{ targetLabels.manual }}</option>
                </select>
              </label>
            </div>
            <div v-if="lanesForCase(item).length" class="platform-coverage" title="覆盖标记（点击切换：未执行 → 通过 → 失败）">
              <button
                v-for="lane in lanesForCase(item)"
                :key="lane.key"
                type="button"
                class="coverage-chip"
                :class="coverageClass(item, lane.key)"
                :title="`${lane.label}：${coverageStateLabels[coverageState(item, lane.key)]}`"
                :aria-label="`${lane.label} ${coverageStateLabels[coverageState(item, lane.key)]}`"
                @click="emit('cycleCoverage', item.id, lane.key)"
              >
                <svg class="coverage-icon" viewBox="0 0 24 24" aria-hidden="true">
                  <path v-if="isPathLane(lane.key)" :d="laneIconPaths[lane.key]" />
                  <template v-else-if="lane.key === 'harmony'">
                    <ellipse cx="12" cy="6" rx="2.4" ry="3.6" />
                    <ellipse cx="12" cy="18" rx="2.4" ry="3.6" />
                    <ellipse cx="6" cy="12" rx="3.6" ry="2.4" />
                    <ellipse cx="18" cy="12" rx="3.6" ry="2.4" />
                    <circle cx="12" cy="12" r="2.6" />
                  </template>
                  <template v-else-if="lane.key === 'safari'">
                    <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="2" />
                    <path d="M16.6 7.4 L13.8 13.8 L7.4 16.6 L10.2 10.2 Z" />
                  </template>
                </svg>
                <span
                  v-if="coverageState(item, lane.key) === 'passed'"
                  class="coverage-pip pip-pass"
                  aria-hidden="true"
                  >✓</span
                >
                <span
                  v-else-if="coverageState(item, lane.key) === 'failed'"
                  class="coverage-pip pip-fail"
                  aria-hidden="true"
                  >✕</span
                >
              </button>
            </div>
            <button class="report-button" type="button" :disabled="!item.reportUrl" @click="emit('openReport', item.id)">
              {{ item.reportUrl ? '查看报告' : '暂无报告' }}
            </button>
            <button
              v-if="item.executionStatus === 'failed'"
              class="repair-case-button"
              :class="{ preparing: !item.diagnosisReady && !!item.reportUrl }"
              type="button"
              data-control="repair-case"
              :title="item.diagnosisReady ? '' : '后台诊断准备中…'"
              @click="emit('repairCase', item.id)"
            >
              诊断修复
            </button>
            <button
              v-if="item.executionStatus === 'failed'"
              class="bug-case-button"
              :class="{
                submitted: bugCount(item) > 0,
                preparing: bugDraftPreparing(item),
              }"
              type="button"
              data-control="submit-bug"
              :title="bugButtonTitle(item)"
              @click="emit('submitBug', item.id)"
            >
              {{ bugCount(item) > 0 ? `已提交 ${bugCount(item)}` : '提交 bug' }}
            </button>
            <button class="edit-case-button" type="button" @click="emit('editCase', item.id)">编辑</button>
          </div>
        </div>
      </article>
    </div>
  </aside>
</template>
