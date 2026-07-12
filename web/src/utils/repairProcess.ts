import type { RepairDraft } from '../types/case'

export type RepairProcessStep = NonNullable<RepairDraft['process']>[number]

export function repairProcessSummary(item: RepairDraft): string {
  const steps = item.process || []
  const rootRounds = steps.filter((step) => step.stage === 'root_cause' && step.round).length
  const parts = [`${steps.length} 阶段`]
  if (rootRounds) parts.push(`根因 ${rootRounds} 轮`)
  return `诊断过程（${parts.join('，')}，点击展开）`
}

export function repairProcessKey(step: RepairProcessStep, index: number): string {
  return `${step.stage || 'round'}-${step.round ?? index}-${index}`
}

export function repairProcessHead(step: RepairProcessStep, index: number): string {
  if (step.stage === 'anchor') return `${index + 1}. 锁定失败锚点`
  if (step.stage === 'backend_gate') return `${index + 1}. 后台门禁${step.allowed === false ? ' · 拦截修复' : ''}`
  if (step.stage === 'repair') return `${index + 1}. 生成修复候选`
  if (step.stage === 'root_cause') {
    const parts = [`根因第 ${step.round ?? '-'} 轮`]
    const images = step.shownImages || []
    if (images.length) parts.push(`看图 ${images.length} 张（${formatImageRefs(images)}）`)
    const windows = step.shownWindows || step.selectedWindows || []
    if (windows.length) parts.push(`补充窗口 ${windows.join('、')}`)
    if (step.decision === 'need_more') parts.push('需要更多证据')
    if (step.decision === 'conclude') parts.push('得出结论')
    return parts.join(' · ')
  }
  const images = step.shownImages || []
  const parts = [`第 ${step.round ?? index + 1} 轮`]
  if (images.length) parts.push(`看图 ${images.length} 张（${formatImageRefs(images)}）`)
  if (step.decision === 'need_more') parts.push('需要更多证据')
  if (step.decision === 'conclude') parts.push('得出结论')
  return parts.join(' · ')
}

export function repairProcessDetails(step: RepairProcessStep): string[] {
  const lines: string[] = []
  if (step.stage === 'anchor') {
    pushLine(lines, '失败锚点', step.failureAnchor)
    pushLine(lines, '验证目标', step.verificationTarget)
    pushLine(lines, '预期标准', step.expectedStandard)
    return lines
  }
  if (step.stage === 'root_cause') {
    pushPlain(lines, step.note)
    if (step.decision === 'need_more') {
      const raw = step.rawRequestImages || []
      const provided = step.requestImages || []
      const windows = step.selectedWindows || step.requestWindows || []
      if (raw.length || provided.length || windows.length) {
        const chunks: string[] = []
        if (raw.length) chunks.push(`模型点名 ${formatImageRefs(raw)}`)
        if (provided.length) chunks.push(`后台补图 ${formatImageRefs(provided)}`)
        if (windows.length) chunks.push(`后台补窗口 ${windows.join('、')}`)
        pushLine(lines, '补证', chunks.join('；'))
      }
    }
    pushPlain(lines, step.conclusionReason)
    return lines
  }
  if (step.stage === 'backend_gate') {
    pushLine(lines, step.allowed === false ? '拦截原因' : '通过原因', step.reason)
    return lines
  }
  if (step.stage === 'repair') {
    const raw = step.rawDecision || {}
    const repairable = raw.repairable
    const channel = raw.repair_channel || raw.repairChannel
    if (typeof repairable === 'boolean') pushLine(lines, '修复判断', repairable ? '可修复' : '不可修复')
    if (channel) pushLine(lines, '修复通道', String(channel))
    pushPlain(lines, step.note)
    return lines
  }
  pushPlain(lines, step.note)
  pushPlain(lines, step.conclusionReason)
  return lines
}

function formatImageRefs(indices: number[]): string {
  return indices.map((idx) => `#${idx}`).join('、')
}

function pushLine(lines: string[], label: string, value: unknown): void {
  const text = String(value || '').trim()
  if (text) lines.push(`${label}：${text}`)
}

function pushPlain(lines: string[], value: unknown): void {
  const text = String(value || '').trim()
  if (text) lines.push(text)
}
