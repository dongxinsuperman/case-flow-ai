<script setup lang="ts">
import type { CaseListItem } from '../../types/case'

interface AssetPathGroup {
  nodeId: string
  label: string
  name: string
  count: number
  children: AssetPathGroup[]
  cases: CaseListItem[]
}

defineProps<{
  group: AssetPathGroup
}>()

defineSlots<{
  case(props: { item: CaseListItem }): unknown
}>()
</script>

<template>
  <section class="asset-path-group">
    <header>
      <div>
        <span>{{ group.label }}</span>
        <strong>{{ group.name }}</strong>
      </div>
      <b>{{ group.count }} 条</b>
    </header>

    <CaseAssetPathBranch
      v-for="child in group.children"
      :key="child.nodeId"
      :group="child"
    >
      <template #case="{ item }">
        <slot name="case" :item="item" />
      </template>
    </CaseAssetPathBranch>

    <template v-for="item in group.cases" :key="item.id">
      <slot name="case" :item="item" />
    </template>
  </section>
</template>
