<script setup lang="ts">
import type { RepairDraft } from '../../types/case'
import { repairProcessDetails, repairProcessHead, repairProcessKey, repairProcessSummary } from '../../utils/repairProcess'

const props = defineProps<{
  open: boolean
  loading: boolean
  error: string
  caseIds: number[]
  items: RepairDraft[]
  applyingDraftId: number | null
}>()

const emit = defineEmits<{
  close: []
  apply: [item: RepairDraft]
  skip: [caseId: number]
  submitBug: [caseId: number]
}>()

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
  flaky_failure: '偶发波动',
  偶发波动: '偶发波动',
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

function repairGateCanRepair(item: RepairDraft) {
  const gate = item.gate as { canRepair?: unknown; can_repair?: unknown; allowed?: unknown } | null
  if (typeof gate?.canRepair === 'boolean') return gate.canRepair
  if (typeof gate?.can_repair === 'boolean') return gate.can_repair
  if (typeof gate?.allowed === 'boolean') return gate.allowed
  return item.repairable
}

function repairGateLabel(item: RepairDraft) {
  const gate = item.gate as { label?: unknown } | null
  const label = String(gate?.label || item.failureType || '').trim()
  if (label) return failureTypeLabels[label] ?? (repairGateCanRepair(item) ? '可修复' : '不确定')
  return repairGateCanRepair(item) ? '可修复' : '不确定'
}

function repairGateReason(item: RepairDraft) {
  const gate = item.gate as { reason?: unknown } | null
  return String(gate?.reason || item.reason || '').trim()
}

function repairReportSummary(item: RepairDraft) {
  return item.reportSummary || repairGateReason(item) || '当前 case 没有关联执行报告，不能生成诊断修复候选。'
}

function repairResultText(item: RepairDraft) {
  return repairGateCanRepair(item)
    ? (item.proposedSteps || '模型没有返回可用的候选步骤。')
    : (repairGateReason(item) || '当前无法自动修复。')
}
</script>

<template>
  <div v-if="props.open" class="modal-mask">
    <section class="import-modal repair-modal">
      <div class="modal-head">
        <div>
          <h2>诊断修复</h2>
          <p>基于执行报告判断是否可修复；只有点击采用修复后才会写入 Quick Case。</p>
        </div>
        <button type="button" @click="emit('close')">关闭</button>
      </div>

      <div v-if="props.loading" class="repair-list case-repair-list">
        <div class="repair-loading-card">
          <strong>正在解读报告</strong>
          <span>共 {{ props.caseIds.length }} 条失败 case。系统会逐条读取报告、提取证据、判断是否可以给出修复步骤。</span>
        </div>
      </div>
      <div v-else class="repair-list case-repair-list">
        <article v-for="(item, index) in props.items" :key="item.caseId" class="case-repair-item">
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
              :disabled="!item.draftId || props.applyingDraftId === item.draftId"
              @click="emit('apply', item)"
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
            <button v-else type="button" class="secondary-button" @click="emit('submitBug', item.caseId)">
              提交 bug
            </button>
            <button type="button" class="secondary-button" @click="emit('skip', item.caseId)">
              本次不改
            </button>
          </div>
        </article>
        <div v-if="!props.items.length" class="notice-state">没有可展示的诊断修复项。</div>
      </div>

      <div v-if="props.error" class="error-state">{{ props.error }}</div>
      <div class="case-repair-footer">
        <button type="button" class="secondary-button" @click="emit('close')">关闭</button>
      </div>
    </section>
  </div>
</template>
