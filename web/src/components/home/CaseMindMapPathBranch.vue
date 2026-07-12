<script setup lang="ts">
import type { CaseListItem, ExecutionStatus } from '../../types/case'

interface FieldNode {
  nodeId: string
  label: string
  value: string
}

interface MindCaseNode {
  nodeId: string
  item: CaseListItem
  fields: FieldNode[]
}

interface PathGroup {
  nodeId: string
  parentNodeId: string
  label: string
  name: string
  levelIndex: number
  count: number
  children: PathGroup[]
  cases: MindCaseNode[]
}

defineProps<{
  group: PathGroup
  selectedCaseId: number | null
  collapsed: Set<string>
  statusLabels: Record<ExecutionStatus, string>
}>()

const emit = defineEmits<{
  toggle: [nodeId: string]
  select: [caseId: number]
}>()

function statusClass(status: ExecutionStatus) {
  return `status-${status.replace('_', '-')}`
}

function pathNodeClass(levelIndex: number) {
  return ['xmind-node-module', 'xmind-node-feature', 'xmind-node-test'][levelIndex % 3]
}
</script>

<template>
  <section class="xmind-row xmind-path-row">
    <div
      class="xmind-node xmind-node-path xmind-connect-node xmind-collapsible"
      :class="[pathNodeClass(group.levelIndex), { 'is-collapsed': collapsed.has(group.nodeId) }]"
      :data-node-id="group.nodeId"
      :data-parent-id="group.parentNodeId"
      @click="emit('toggle', group.nodeId)"
    >
      <i class="xmind-caret">{{ collapsed.has(group.nodeId) ? `▸ ${group.count}` : '▾' }}</i>
      <span>{{ group.label }}</span>
      <strong>{{ group.name }}</strong>
      <em>{{ group.count }} 条</em>
    </div>

    <div v-if="!collapsed.has(group.nodeId)" class="xmind-children">
      <CaseMindMapPathBranch
        v-for="child in group.children"
        :key="child.nodeId"
        :group="child"
        :selected-case-id="selectedCaseId"
        :collapsed="collapsed"
        :status-labels="statusLabels"
        @toggle="emit('toggle', $event)"
        @select="emit('select', $event)"
      />
      <section v-for="caseNode in group.cases" :key="caseNode.nodeId" class="xmind-row xmind-case-row">
        <button
          type="button"
          class="mind-case-node xmind-node xmind-node-case xmind-connect-node"
          :class="[statusClass(caseNode.item.executionStatus), { active: selectedCaseId === caseNode.item.id }]"
          :data-node-id="caseNode.nodeId"
          :data-parent-id="group.nodeId"
          :data-case-id="caseNode.item.id"
          :title="caseNode.item.rawTitle"
          @click="emit('select', caseNode.item.id)"
        >
          <span>测试标题 #{{ caseNode.item.displayNo || caseNode.item.ordinal }}</span>
          <strong>{{ caseNode.item.rawTitle }}</strong>
          <em>{{ statusLabels[caseNode.item.executionStatus] }}</em>
        </button>

        <div class="xmind-children xmind-field-children">
          <div v-for="field in caseNode.fields" :key="field.nodeId" class="xmind-row xmind-field-row">
            <div
              class="xmind-node xmind-node-field xmind-connect-node"
              :data-node-id="field.nodeId"
              :data-parent-id="caseNode.nodeId"
            >
              <span>{{ field.label }}</span>
              <strong>{{ field.value }}</strong>
            </div>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>
