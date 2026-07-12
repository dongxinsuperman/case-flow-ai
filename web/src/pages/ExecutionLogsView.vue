<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useExecutionLogsStore } from '../stores/executionLogs'
import type { ExecutionCallLog } from '../types/executionLog'

const store = useExecutionLogsStore()
const expandedId = ref<number | null>(null)

const MODE_LABEL: Record<string, string> = { standard: '标准', quick: '快速' }
const EXECUTOR_LABEL: Record<string, string> = {
  ai_phone: 'AI Phone',
  ai_web: 'AI Web',
  ai_hybrid: 'AI Hybrid',
  ai_api: 'AI API',
}
const SCOPE_LABEL: Record<string, string> = { single: '单条', batch: '批量' }
const STATUS_META: Record<string, { label: string; cls: string }> = {
  compiling: { label: '编译中', cls: 'st-compiling' },
  submitted: { label: '已提交', cls: 'st-submitted' },
  compile_failed: { label: '编译失败', cls: 'st-failed' },
  submit_failed: { label: '提交失败', cls: 'st-failed' },
}

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'compiling', label: '编译中' },
  { value: 'submitted', label: '已提交' },
  { value: 'compile_failed', label: '编译失败' },
  { value: 'submit_failed', label: '提交失败' },
]
const EXECUTOR_OPTIONS = [
  { value: '', label: '全部执行器' },
  { value: 'ai_phone', label: 'AI Phone' },
  { value: 'ai_web', label: 'AI Web' },
  { value: 'ai_hybrid', label: 'AI Hybrid' },
  { value: 'ai_api', label: 'AI API' },
]
const MODE_OPTIONS = [
  { value: '', label: '全部模式' },
  { value: 'standard', label: '标准' },
  { value: 'quick', label: '快速' },
]

function statusMeta(status: string) {
  return STATUS_META[status] || { label: status, cls: 'st-compiling' }
}

function formatTime(value: string): string {
  if (!value) return '-'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString('zh-CN', { hour12: false })
}

function relation(log: ExecutionCallLog): string {
  if (log.requirementItemId) {
    return log.requirementItemTitle
      ? `二级需求·${log.requirementItemTitle}`
      : `二级需求 #${log.requirementItemId}`
  }
  if (log.quickSessionId) {
    return log.quickSessionTitle
      ? `快速会话·${log.quickSessionTitle}`
      : `快速会话 ${log.quickSessionId}`
  }
  return '-'
}

function toggleRow(log: ExecutionCallLog) {
  expandedId.value = expandedId.value === log.id ? null : log.id
}

function pretty(value: unknown): string {
  if (value === null || value === undefined) return '（空）'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

onMounted(() => {
  void store.load(1)
})
</script>

<template>
  <section class="log-page">
    <div class="log-panel">
      <header class="log-header">
        <div>
          <h1>执行流水</h1>
          <p>每次点击执行拆出的每一路提交都记一条：触发人、模式、执行器、关联对象、状态与失败原因，用于排查“这次执行到底发生了什么”。只读。</p>
        </div>
        <button type="button" class="log-refresh" :disabled="store.loading" @click="store.load(store.page)">
          {{ store.loading ? '刷新中…' : '刷新' }}
        </button>
      </header>

      <div class="log-toolbar">
        <select :value="store.modeFilter" @change="store.setFilter('mode', ($event.target as HTMLSelectElement).value)">
          <option v-for="opt in MODE_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
        <select :value="store.executorFilter" @change="store.setFilter('executor', ($event.target as HTMLSelectElement).value)">
          <option v-for="opt in EXECUTOR_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
        <select :value="store.statusFilter" @change="store.setFilter('status', ($event.target as HTMLSelectElement).value)">
          <option v-for="opt in STATUS_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
      </div>

      <p v-if="store.error" class="log-error">{{ store.error }}</p>

      <div class="log-list">
        <div class="log-list-head">
          <span class="c-time">时间</span>
          <span class="c-mode">模式</span>
          <span class="c-exec">执行器</span>
          <span class="c-scope">范围</span>
          <span class="c-rel">关联</span>
          <span class="c-case">case</span>
          <span class="c-user">触发人</span>
          <span class="c-status">状态</span>
        </div>

        <p v-if="store.loading" class="log-empty">加载中…</p>
        <p v-else-if="!store.items.length" class="log-empty">暂无执行流水。</p>

        <template v-for="log in store.items" v-else :key="log.id">
          <div class="log-row" :class="{ open: expandedId === log.id }" @click="toggleRow(log)">
            <span class="c-time">{{ formatTime(log.createdAt) }}</span>
            <span class="c-mode">{{ MODE_LABEL[log.mode] || log.mode }}</span>
            <span class="c-exec">{{ EXECUTOR_LABEL[log.executor] || log.executor }}</span>
            <span class="c-scope">{{ SCOPE_LABEL[log.scope] || log.scope }}</span>
            <span class="c-rel" :title="relation(log)">{{ relation(log) }}</span>
            <span class="c-case">{{ log.caseIds.length }}</span>
            <span class="c-user">{{ log.triggerUserName || (log.triggerUserId ? `#${log.triggerUserId}` : '-') }}</span>
            <span class="c-status">
              <span class="log-badge" :class="statusMeta(log.status).cls">{{ statusMeta(log.status).label }}</span>
            </span>
          </div>
          <div v-if="expandedId === log.id" class="log-detail">
            <div class="log-detail-grid">
              <div><label>入口</label><code>{{ log.entry }}</code></div>
              <div><label>callId</label><code>{{ log.callId }}</code></div>
              <div><label>请求组</label><code>{{ log.requestGroupId || '-' }}</code></div>
              <div><label>submissionId</label><code>{{ log.submissionId || '-' }}</code></div>
              <div><label>batchId</label><code>{{ log.executionBatchId ?? '-' }}</code></div>
              <div><label>caseIds</label><code>{{ log.caseIds.join(', ') || '-' }}</code></div>
            </div>
            <div v-if="log.failureReason" class="log-detail-fail">失败原因：{{ log.failureReason }}</div>
            <div class="log-detail-block">
              <label>本次实际提交的 Function Map（端过滤后）</label>
              <pre v-if="log.submittedFunctionMapContext">{{ log.submittedFunctionMapContext }}</pre>
              <p v-else class="log-detail-empty">本次未带入任何 Function Map（无匹配挂载 / 端不匹配 / 提交前失败）。</p>
            </div>
            <div class="log-detail-block">
              <label>输入摘要</label>
              <pre>{{ pretty(log.input) }}</pre>
            </div>
            <div v-if="log.functionMapResult" class="log-detail-block">
              <label>Function Map 编译结果</label>
              <pre>{{ pretty(log.functionMapResult) }}</pre>
            </div>
            <div v-if="log.effectiveContext" class="log-detail-block">
              <label>最终上下文</label>
              <pre>{{ pretty(log.effectiveContext) }}</pre>
            </div>
          </div>
        </template>
      </div>

      <div v-if="store.total > store.pageSize" class="log-pager">
        <button type="button" :disabled="store.page <= 1" @click="store.changePage(-1)">上一页</button>
        <span>{{ store.page }} / {{ store.pageCount }}（共 {{ store.total }}）</span>
        <button type="button" :disabled="store.page >= store.pageCount" @click="store.changePage(1)">下一页</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.log-page {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: 12px 16px;
  height: calc(100dvh - 92px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.log-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 16px 18px;
  box-sizing: border-box;
}

.log-header {
  flex: none;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.log-header h1 {
  margin: 0 0 4px;
  font-size: 20px;
}

.log-header p {
  margin: 0;
  color: #5b6478;
  font-size: 13px;
}

.log-refresh {
  flex: none;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #26364d;
  font-weight: 700;
  padding: 7px 14px;
  cursor: pointer;
}

.log-toolbar {
  flex: none;
  display: flex;
  gap: 10px;
  margin: 14px 0 10px;
}

.log-toolbar select {
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #152033;
  font-size: 13px;
  padding: 6px 10px;
  cursor: pointer;
}

.log-error {
  flex: none;
  color: #b42318;
  font-size: 13px;
  margin: 0 0 8px;
}

.log-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.log-list-head,
.log-row {
  display: grid;
  grid-template-columns: 168px 60px 90px 56px minmax(160px, 1fr) 52px 110px 90px;
  gap: 10px;
  align-items: center;
  padding: 10px 14px;
  font-size: 13px;
}

.log-list-head {
  position: sticky;
  top: 0;
  z-index: 2;
  background: #f8fafc;
  color: #64748b;
  font-weight: 700;
  font-size: 12px;
  border-bottom: 1px solid #e2e8f0;
}

.log-row {
  border-bottom: 1px solid #f1f5f9;
  cursor: pointer;
}

.log-row:hover,
.log-row.open {
  background: #f8fafc;
}

.log-row .c-rel {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.log-badge {
  display: inline-block;
  border-radius: 999px;
  padding: 1px 10px;
  font-size: 12px;
  font-weight: 700;
}

.st-compiling {
  background: #eef2ff;
  color: #4338ca;
}

.st-submitted {
  background: #dcfce7;
  color: #15803d;
}

.st-failed {
  background: #fee2e2;
  color: #b42318;
}

.log-detail {
  border-bottom: 1px solid #f1f5f9;
  background: #fbfdff;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.log-detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 20px;
}

.log-detail-grid > div {
  display: flex;
  gap: 8px;
  align-items: baseline;
  min-width: 0;
}

.log-detail-grid label {
  flex: 0 0 84px;
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
}

.log-detail-grid code {
  min-width: 0;
  word-break: break-all;
  font-size: 12px;
  color: #172033;
}

.log-detail-fail {
  color: #b42318;
  font-size: 12px;
  font-weight: 700;
}

.log-detail-block label {
  display: block;
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 4px;
}

.log-detail-block pre {
  margin: 0;
  max-height: 260px;
  overflow: auto;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #fff;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.log-detail-empty {
  margin: 0;
  color: #94a3b8;
  font-size: 12px;
}

.log-empty {
  padding: 28px 16px;
  text-align: center;
  color: #94a3b8;
}

.log-pager {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding-top: 12px;
  color: #64748b;
  font-size: 13px;
}

.log-pager button {
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #26364d;
  font-weight: 700;
  padding: 5px 14px;
  cursor: pointer;
}

.log-pager button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
