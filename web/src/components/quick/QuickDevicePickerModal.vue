<script setup lang="ts">
import { computed, ref } from 'vue'
import type { AIPhoneDevice, ExecutionStatus, ExecutionTarget } from '../../types/case'
import type { QuickCaseItem } from '../../types/quick'
import { filterAiPhoneDevices } from '../../utils/deviceFilter'

const props = defineProps<{
  open: boolean
  mode: 'single' | 'queue'
  loading: boolean
  error: string
  items: QuickCaseItem[]
  devices: AIPhoneDevice[]
  webDevices: AIPhoneDevice[]
  selectedAliases: string[]
  webSelectedAliases: string[]
  deviceFilter: string
  retryMax: number
  cacheMode: 'off' | 'v1' | 'v2' | 'v3'
}>()

const emit = defineEmits<{
  close: []
  refresh: []
  refreshWeb: []
  confirm: []
  updateDeviceFilter: [value: string]
  executeDevice: [device: AIPhoneDevice]
  togglePlatform: [platform: string, checked: boolean]
  toggleWebPlatform: [platform: string, checked: boolean]
  toggleDevice: [alias: string, checked: boolean]
  toggleWebDevice: [alias: string, checked: boolean]
  updateRetryMax: [value: number]
  updateCacheMode: [value: 'off' | 'v1' | 'v2' | 'v3']
  reorderItems: [items: QuickCaseItem[]]
}>()

// 队列模式的 App 设备按关键字筛选（单条模式不过滤）；被过滤掉的不渲染、不计入已选。
const visibleDevices = computed(() => filterAiPhoneDevices(props.devices, props.deviceFilter))
const deviceGroups = computed(() => groupDevicesByPlatform(visibleDevices.value))
const webDeviceGroups = computed(() => groupDevicesByPlatform(props.webDevices))
const selectedDeviceCount = computed(
  () => props.selectedAliases.filter((alias) => visibleDevices.value.some((device) => deviceAlias(device) === alias)).length,
)
const webSelectedDeviceCount = computed(() => props.webSelectedAliases.length)
const summary = computed(() => {
  const parts = [`${props.items.length} 条待执行`]
  if (appItems.value.length) parts.push(`${appItems.value.length} 条 App case`)
  if (webItems.value.length) parts.push(`${webItems.value.length} 条 Web case`)
  if (apiItems.value.length) parts.push(`${apiItems.value.length} 条 API case`)
  if (mixedItems.value.length) parts.push(`${mixedItems.value.length} 条 Hybrid case`)
  if (manualItems.value.length) parts.push(`${manualItems.value.length} 条人工 case`)
  return parts.join(' · ')
})
const singleItem = computed(() => (props.items.length === 1 ? props.items[0] : null))
const singleTarget = computed(() => singleItem.value?.executionTarget || 'app')
const singleDevices = computed(() => (singleTarget.value === 'web' ? props.webDevices : props.devices))
const singleDeviceGroups = computed(() => groupDevicesByPlatform(singleDevices.value))
const singleLoadingText = computed(() => (singleTarget.value === 'web' ? '正在读取浏览器槽...' : '正在读取在线设备...'))
const singleEmptyText = computed(() => (singleTarget.value === 'web' ? 'AI Web 当前没有返回浏览器槽。' : 'AI Phone 当前没有返回在线设备。'))
const appItems = computed(() => props.items.filter((item) => item.executionTarget === 'app'))
const webItems = computed(() => props.items.filter((item) => item.executionTarget === 'web'))
const apiItems = computed(() => props.items.filter((item) => item.executionTarget === 'api'))
const mixedItems = computed(() => props.items.filter((item) => item.executionTarget === 'mixed'))
const manualItems = computed(() => props.items.filter((item) => item.executionTarget === 'manual' || item.executionTarget === 'unknown'))
const draggingCaseId = ref<number | null>(null)

const statusLabels: Record<ExecutionStatus, string> = {
  not_run: '未执行',
  running: '执行中',
  passed: '通过',
  failed: '失败',
}

const executorLabels: Record<ExecutionTarget, string> = {
  app: 'App',
  web: 'Web',
  api: 'API',
  mixed: 'Hybrid',
  manual: '人工',
  unknown: '人工',
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

function statusClass(status: ExecutionStatus) {
  return `status-${status.replace('_', '-')}`
}

function executorLabel(item: QuickCaseItem) {
  return executorLabels[item.executionTarget] || '人工'
}

function dragQueueItem(caseId: number) {
  draggingCaseId.value = caseId
}

function dropQueueItem(targetCaseId: number) {
  const draggedCaseId = draggingCaseId.value
  draggingCaseId.value = null
  if (!draggedCaseId || draggedCaseId === targetCaseId) return
  const nextItems = [...props.items]
  const fromIndex = nextItems.findIndex((item) => item.id === draggedCaseId)
  const toIndex = nextItems.findIndex((item) => item.id === targetCaseId)
  if (fromIndex < 0 || toIndex < 0) return
  const [item] = nextItems.splice(fromIndex, 1)
  nextItems.splice(toIndex, 0, item)
  emit('reorderItems', nextItems)
}

function moveQueueItem(caseId: number, direction: -1 | 1) {
  const nextItems = [...props.items]
  const fromIndex = nextItems.findIndex((item) => item.id === caseId)
  const toIndex = fromIndex + direction
  if (fromIndex < 0 || toIndex < 0 || toIndex >= nextItems.length) return
  const [item] = nextItems.splice(fromIndex, 1)
  nextItems.splice(toIndex, 0, item)
  emit('reorderItems', nextItems)
}
</script>

<template>
  <div v-if="open && mode === 'single' && singleItem" class="modal-mask">
    <section class="import-modal device-modal device-picker-modal">
      <div class="modal-head">
        <div>
          <h2>{{ singleTarget === 'web' ? '选择浏览器槽' : '选择执行设备' }}</h2>
          <p>
            {{ singleItem.displayNo || singleItem.ordinal }}. {{ singleItem.rawTitle }} · {{ singleTarget === 'web' ? '在线浏览器槽' : '在线设备' }}
          </p>
        </div>
        <button type="button" @click="emit('close')">关闭</button>
      </div>
      <div v-if="loading" class="notice-state">{{ singleLoadingText }}</div>
      <div v-else-if="!singleDevices.length" class="notice-state">
        {{ error || singleEmptyText }}
      </div>
      <div v-else class="device-group-list">
        <section v-for="(groupDevices, platform) in singleDeviceGroups" :key="platform" class="device-group device-picker-group">
          <div class="device-group-head">
            <h3>{{ platformLabel(platform) }}</h3>
            <span>{{ deviceGroupSummary(groupDevices) }}</span>
          </div>
          <div class="device-grid">
            <button
              v-for="device in groupDevices"
              :key="deviceAlias(device)"
              type="button"
              class="device-card device-pick-card"
              @click="emit('executeDevice', device)"
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
      <div v-if="error && singleDevices.length" class="error-state">
        {{ error }}
      </div>
      <div class="modal-actions">
        <button type="button" @click="emit('close')">取消</button>
      </div>
    </section>
  </div>

  <div v-else-if="open" class="modal-mask">
    <section class="import-modal execution-modal execution-queue-modal">
      <div class="modal-head">
        <div>
          <h2>本次执行队列</h2>
          <p>{{ summary }}</p>
        </div>
        <button type="button" @click="emit('close')">关闭</button>
      </div>

      <div class="queue-hint">
        拖动左侧手柄调整本次推送顺序；这个顺序只影响本次执行，不改变 Case 列表和脑图。
      </div>

      <div class="execution-strategy-panel">
        <div v-if="appItems.length" class="strategy-card">
          <div class="strategy-head">
            <div>
              <strong>App / AI Phone 执行策略</strong>
              <div class="meta">{{ appItems.length }} 条 App case · 在线设备</div>
            </div>
            <div class="strategy-actions">
              <span class="count-pill">已选 {{ selectedDeviceCount }} 台</span>
              <button type="button" :disabled="loading" @click="emit('refresh')">刷新设备</button>
            </div>
          </div>
          <div class="strategy-note">
            所选设备会作为 AI Phone 的 deviceAliasPools；多条 App case 共享同一设备池，由 AI Phone 调度器自动排队和并行。
          </div>
          <div class="device-filter-row">
            <input
              :value="deviceFilter"
              type="search"
              placeholder="按设备名筛选（如：学习工具），只看/只调度匹配的设备，本地保存"
              @input="emit('updateDeviceFilter', ($event.target as HTMLInputElement).value)"
            />
          </div>
          <div class="strategy-options">
            <label>
              <span>失败重试次数</span>
              <input
                :value="retryMax"
                type="number"
                min="0"
                max="10"
                @input="emit('updateRetryMax', Number(($event.target as HTMLInputElement).value || 0))"
              />
            </label>
            <label>
              <span>轨迹缓存模式</span>
              <select
                :value="cacheMode"
                @change="emit('updateCacheMode', ($event.target as HTMLSelectElement).value as 'off' | 'v1' | 'v2' | 'v3')"
              >
                <option value="off">off（不开启）</option>
                <option value="v1">v1</option>
                <option value="v2">v2</option>
                <option value="v3">v3</option>
              </select>
            </label>
          </div>

          <div v-if="loading" class="notice-state">正在读取在线设备...</div>
          <div v-else-if="!devices.length" class="notice-state">
            {{ error || 'AI Phone 当前没有返回在线设备。' }}
          </div>
          <div v-else-if="!visibleDevices.length" class="notice-state">
            当前筛选「{{ deviceFilter }}」没有匹配的在线设备，请调整或清空筛选。
          </div>
          <div v-else class="device-group-list strategy-device-list">
            <section v-for="(groupDevices, platform) in deviceGroups" :key="platform" class="device-group strategy-device-group">
              <div class="device-group-head">
                <h3>{{ platformLabel(platform) }}</h3>
                <span>{{ deviceGroupSummary(groupDevices) }}</span>
                <button type="button" @click="emit('togglePlatform', platform, true)">全选</button>
                <button type="button" @click="emit('togglePlatform', platform, false)">清空</button>
              </div>
              <div class="device-grid strategy-device-grid">
                <label v-for="device in groupDevices" :key="deviceAlias(device)" class="device-check device-chip">
                  <input
                    type="checkbox"
                    :checked="selectedAliases.includes(deviceAlias(device))"
                    @change="emit('toggleDevice', deviceAlias(device), ($event.target as HTMLInputElement).checked)"
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

        <div v-if="webItems.length" class="strategy-card">
          <div class="strategy-head">
            <div>
              <strong>Web / AI Web 执行策略</strong>
              <div class="meta">{{ webItems.length }} 条 case · 在线浏览器槽</div>
            </div>
            <div class="strategy-actions">
              <span class="count-pill">已选 {{ webSelectedDeviceCount }} 个</span>
              <button type="button" :disabled="loading" @click="emit('refreshWeb')">刷新浏览器槽</button>
            </div>
          </div>
          <div class="strategy-note">所选浏览器槽会作为 AI Web 的 deviceAliasPools；多条 Web case 共享同一浏览器槽池。</div>
          <div v-if="loading" class="notice-state">正在读取浏览器槽...</div>
          <div v-else-if="!webDevices.length" class="notice-state">
            {{ error || 'AI Web 当前没有返回浏览器槽。' }}
          </div>
          <div v-else class="device-group-list strategy-device-list">
            <section v-for="(groupDevices, platform) in webDeviceGroups" :key="platform" class="device-group strategy-device-group">
              <div class="device-group-head">
                <h3>{{ platformLabel(platform) }}</h3>
                <span>{{ deviceGroupSummary(groupDevices) }}</span>
                <button type="button" @click="emit('toggleWebPlatform', platform, true)">全选</button>
                <button type="button" @click="emit('toggleWebPlatform', platform, false)">清空</button>
              </div>
              <div class="device-grid strategy-device-grid">
                <label v-for="device in groupDevices" :key="deviceAlias(device)" class="device-check device-chip">
                  <input
                    type="checkbox"
                    :checked="webSelectedAliases.includes(deviceAlias(device))"
                    @change="emit('toggleWebDevice', deviceAlias(device), ($event.target as HTMLInputElement).checked)"
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

        <div v-if="apiItems.length" class="strategy-card">
          <div class="strategy-head">
            <div>
              <strong>API / AI API 执行策略</strong>
              <div class="meta">{{ apiItems.length }} 条 case</div>
            </div>
          </div>
          <div class="strategy-note">AI API 是本地执行器，不占用设备池，确认后直接生成接口请求计划并执行。</div>
        </div>

        <div v-if="mixedItems.length" class="strategy-card">
          <div class="strategy-head">
            <div>
              <strong>混合 / AI Hybrid 执行策略</strong>
              <div class="meta">{{ mixedItems.length }} 条 case</div>
            </div>
          </div>
          <div class="strategy-note">AI Hybrid 是内置编排器，不需要设备池；确认后会按 case 内容自动拆分到 API、Web、Phone 等子工具。</div>
        </div>

        <div v-if="manualItems.length" class="strategy-card is-placeholder">
          <div class="strategy-head">
            <div>
              <strong>人工执行策略</strong>
              <div class="meta">{{ manualItems.length }} 条 case</div>
            </div>
          </div>
          <div class="strategy-note">人工 case 不推送执行器，当前只会进入执行中状态，后续再接人工任务流。</div>
        </div>
      </div>

      <div class="queue-list">
        <article
          v-for="(item, index) in items"
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
              :disabled="index === items.length - 1"
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
              <span class="executor-pill">{{ executorLabel(item) }}</span>
            </div>
          </div>
        </article>
      </div>

      <div v-if="error" class="error-state">{{ error }}</div>
      <div class="modal-actions">
        <button type="button" @click="emit('close')">取消</button>
        <button type="button" class="primary" :disabled="loading || (appItems.length > 0 && selectedDeviceCount === 0)" @click="emit('confirm')">
          确认执行
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.quick-device-mask {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.45);
}

.quick-device-modal {
  width: min(980px, 96vw);
  max-height: 92vh;
  overflow: auto;
  display: grid;
  gap: 14px;
  border-radius: 8px;
  background: #fff;
  padding: 18px;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.28);
}

.quick-device-head,
.quick-device-card-head,
.quick-device-group-head,
.quick-device-modal-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.quick-device-head,
.quick-device-modal-actions {
  justify-content: space-between;
}

.quick-device-head h2,
.quick-device-group-head h3 {
  margin: 0;
}

.quick-device-head p,
.quick-device-card-head .meta,
.quick-device-group-head span,
.quick-device-case-body > span {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.quick-device-head button,
.quick-device-actions button,
.quick-device-group-head button,
.quick-device-modal-actions button {
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #26364d;
  padding: 8px 12px;
  font-weight: 800;
  cursor: pointer;
}

.quick-device-modal-actions .primary {
  border-color: #1f5eff;
  background: #1f5eff;
  color: #fff;
}

.quick-device-actions button:disabled,
.quick-device-modal-actions button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.quick-device-hint,
.quick-device-notice {
  border-radius: 7px;
  padding: 10px 12px;
  color: #047857;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  font-size: 13px;
  font-weight: 700;
}

.quick-device-card {
  border: 1px solid #dbe3ef;
  border-radius: 8px;
  background: #f8fbff;
  padding: 12px;
}

.quick-device-card-head {
  justify-content: space-between;
}

.quick-device-card-head strong {
  color: #0f172a;
}

.quick-device-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.quick-device-actions span {
  border-radius: 999px;
  background: #dcfce7;
  color: #047857;
  padding: 5px 10px;
  font-size: 12px;
  font-weight: 900;
}

.quick-device-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin: 12px 0;
}

.quick-device-options label {
  display: grid;
  gap: 6px;
  color: #52627a;
  font-size: 12px;
  font-weight: 800;
}

.quick-device-options input,
.quick-device-options select {
  width: 100%;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #152033;
  padding: 9px 10px;
  font: inherit;
}

.quick-device-group-list {
  display: grid;
  gap: 10px;
}

.quick-device-group {
  border: 1px solid #dbe3ef;
  border-radius: 8px;
  background: #fff;
  padding: 10px;
}

.quick-device-group-head {
  justify-content: flex-start;
}

.quick-device-group-head span {
  margin-right: auto;
}

.quick-device-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
  margin-top: 8px;
}

.quick-device-chip {
  min-height: auto;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #fff;
  padding: 8px;
}

.quick-device-chip input {
  margin: 0;
}

.quick-device-chip span {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.quick-device-chip strong {
  color: #0f172a;
  font-size: 13px;
}

.quick-device-chip small {
  color: #64748b;
  font-size: 11px;
}

.quick-device-occupancy {
  display: inline-block;
  margin-left: 6px;
  border-radius: 999px;
  padding: 1px 7px;
  font-size: 11px;
  font-weight: 800;
  vertical-align: middle;
}

.quick-device-occupancy.occupancy-idle {
  background: #dcfce7;
  color: #047857;
}

.quick-device-occupancy.occupancy-busy {
  background: #ffedd5;
  color: #c2410c;
}

.quick-device-queue {
  display: grid;
  gap: 8px;
}

.quick-device-queue-item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  border: 1px solid #e2e8f0;
  border-left: 5px solid #94a3b8;
  border-radius: 8px;
  background: #fff;
  padding: 10px 12px;
}

.quick-device-queue-item.status-running {
  border-left-color: #2563eb;
}

.quick-device-queue-item.status-passed {
  border-left-color: #15924f;
}

.quick-device-queue-item.status-failed {
  border-left-color: #d23b3b;
}

.quick-device-order {
  display: inline-grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #eef2ff;
  color: #1f5eff;
}

.quick-device-case-body {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.quick-device-case-body b {
  color: #0f172a;
}

.quick-device-case-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.quick-device-case-tags em {
  border-radius: 999px;
  background: #edf2f7;
  color: #334155;
  padding: 3px 8px;
  font-size: 12px;
  font-style: normal;
  font-weight: 800;
}

.quick-device-case-tags em.status-running {
  background: #dbeafe;
  color: #1d4ed8;
}

.quick-device-case-tags em.status-passed {
  background: #dcfce7;
  color: #047857;
}

.quick-device-case-tags em.status-failed {
  background: #fee2e2;
  color: #b91c1c;
}

.quick-device-case-tags em.executor {
  background: #eef2ff;
  color: #4338ca;
}

.quick-device-error {
  border-top: 1px solid #e2e8f0;
  color: #b91c1c;
  padding-top: 10px;
  white-space: pre-wrap;
}

@media (max-width: 760px) {
  .quick-device-card-head,
  .quick-device-actions,
  .quick-device-modal-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .quick-device-options {
    grid-template-columns: 1fr;
  }
}
</style>
