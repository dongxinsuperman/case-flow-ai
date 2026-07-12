<script setup lang="ts">
import type { CaseListItem } from '../../types/case'

const props = defineProps<{
  item: CaseListItem
  expanded: boolean
  groupName: string
  requirementTitle: string
}>()

const emit = defineEmits<{
  toggle: [caseId: number]
  edit: [item: CaseListItem]
  delete: [item: CaseListItem]
}>()

function splitSteps(item: CaseListItem) {
  return String(item.stepsText || '')
    .split('、')
    .map((step) => step.trim())
    .filter(Boolean)
}

function stepCount(item: CaseListItem) {
  return splitSteps(item).length || (item.stepsText ? 1 : 0)
}

function assetStatusLabel(item: CaseListItem) {
  const labels: Record<string, string> = {
    imported: '已导入',
    changed: '变更待确认',
    archived: '已归档',
  }
  return labels[item.assetStatus] ?? item.assetStatus ?? '无'
}

function tagList(item: CaseListItem) {
  const tags = [...(item.scenarioTags || [])]
  if (item.manual && !tags.includes('人工')) {
    tags.unshift('人工')
  }
  return tags.length ? tags : ['标签无']
}

function assetSummaryTags(item: CaseListItem) {
  const tags = tagList(item)
  return tags.length === 1 && tags[0] === '标签无' ? [] : tags
}
</script>

<template>
  <article
    class="asset-case"
    :class="{ expanded }"
  >
    <div class="asset-case-head">
      <strong>{{ item.ordinal }}. {{ item.rawTitle }}</strong>
      <button type="button" @click="emit('toggle', item.id)">
        {{ expanded ? '收起' : '展开' }}
      </button>
    </div>
    <p>{{ item.path }}</p>
    <div class="asset-case-meta">
      {{ stepCount(item) }} 步 · 状态 {{ assetStatusLabel(item) }}
    </div>
    <div v-if="assetSummaryTags(item).length" class="asset-case-tags">
      <span
        v-for="tag in assetSummaryTags(item)"
        :key="tag"
        :class="{ manual: tag === '人工', empty: tag === '标签无' }"
      >
        {{ tag }}
      </span>
    </div>
    <template v-if="expanded">
      <div class="asset-case-detail">
        <div class="asset-detail-head">
          <div>
            <h3>Case 详情</h3>
            <p>删除会同步影响首页、脑图和当前测试资产集合。</p>
          </div>
          <div class="asset-detail-actions">
            <span class="asset-state-pill">{{ assetStatusLabel(item) }}</span>
            <button type="button" @click="emit('edit', item)">编辑 Case</button>
            <button type="button" class="danger" @click="emit('delete', item)">删除 Case</button>
          </div>
        </div>
        <section class="asset-detail-block">
          <h4>测试标题</h4>
          <div>{{ item.rawTitle }}</div>
        </section>
        <section class="asset-detail-block">
          <h4>前置条件</h4>
          <div>{{ item.preconditions || '无' }}</div>
        </section>
        <section class="asset-detail-block">
          <h4>操作步骤</h4>
          <div class="asset-steps-original">{{ item.stepsText || '无' }}</div>
          <div class="asset-split-note">拆分明细：按中文顿号“、”切分</div>
          <ol class="asset-steps">
            <li v-for="step in splitSteps(item)" :key="step">{{ step }}</li>
          </ol>
        </section>
        <section class="asset-detail-block">
          <h4>预期结果</h4>
          <div>{{ item.expectedResult || '无' }}</div>
        </section>
        <section class="asset-detail-block asset-secondary-block">
          <h4>辅助信息</h4>
          <div class="asset-meta-line">需求：{{ groupName || '无' }} / {{ requirementTitle || '无' }}</div>
          <div class="asset-meta-line">来源测试集：{{ item.suiteTitle || '无' }}</div>
          <div class="asset-meta-line">层级：{{ item.path || '无' }}</div>
          <div v-if="tagList(item).length" class="asset-detail-tags">
            <span
              v-for="tag in tagList(item)"
              :key="tag"
              :class="{ manual: tag === '人工', empty: tag === '标签无' }"
            >
              {{ tag }}
            </span>
          </div>
        </section>
      </div>
    </template>
  </article>
</template>
