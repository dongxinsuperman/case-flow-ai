<script setup lang="ts">
import { computed, ref } from 'vue'

import type { BugEditableOption, BugField } from '../types/case'

const props = defineProps<{
  field: BugField
}>()

const emit = defineEmits<{
  (event: 'update:selected', value: string | string[] | null): void
}>()

const keyword = ref('')
const manualRelatedId = ref('')

const fieldType = computed(() => props.field.type ?? '')
const isUserField = computed(() => fieldType.value === 'multi_user' || fieldType.value === 'user')
const isRelationField = computed(() =>
  fieldType.value === 'work_item_related_select' ||
  fieldType.value === 'workitem_related_select' ||
  fieldType.value === 'work_item_related_multi_select' ||
  fieldType.value === 'workitem_related_multi_select',
)
const isMultiValueField = computed(() =>
  fieldType.value === 'multi_select' ||
  fieldType.value === 'multi_user' ||
  fieldType.value === 'work_item_related_multi_select' ||
  fieldType.value === 'workitem_related_multi_select',
)

const searchable = computed(() => isUserField.value && props.field.options.length > 8)

const filteredOptions = computed(() => {
  const normalized = keyword.value.trim().toLowerCase()
  if (!normalized) {
    return props.field.options
  }
  return props.field.options.filter((option) => {
    const label = String(option.name || '').toLowerCase()
    const id = String(option.id || '').toLowerCase()
    return label.includes(normalized) || id.includes(normalized)
  })
})

function optionId(option: BugEditableOption): string {
  return String(option.id ?? '')
}

function optionName(option: BugEditableOption): string {
  return String(option.name ?? option.id ?? '')
}

function currentArray(): string[] {
  if (Array.isArray(props.field.selected)) {
    return props.field.selected.map(String)
  }
  if (props.field.selected === null || props.field.selected === undefined || props.field.selected === '') {
    return []
  }
  return [String(props.field.selected)]
}

function isSelected(value: string): boolean {
  return currentArray().includes(value)
}

function setSelected(value: string | string[] | null): void {
  emit('update:selected', value)
}

function toggleByName(name: string): void {
  const current = currentArray()
  const index = current.indexOf(name)
  if (index >= 0) {
    current.splice(index, 1)
  } else {
    current.push(name)
  }
  setSelected(current)
}

function toggleById(id: string): void {
  const current = currentArray()
  const index = current.indexOf(id)
  if (index >= 0) {
    current.splice(index, 1)
  } else if (isMultiValueField.value) {
    current.push(id)
  } else {
    current.splice(0, current.length, id)
  }
  setSelected(current)
}

function updateSingleRelation(value: string): void {
  const clean = value.trim()
  setSelected(clean || null)
}

function addManualRelation(): void {
  const clean = manualRelatedId.value.trim()
  if (!clean) {
    return
  }
  if (isMultiValueField.value) {
    const current = currentArray()
    if (!current.includes(clean)) {
      current.push(clean)
    }
    setSelected(current)
  } else {
    setSelected(clean)
  }
  manualRelatedId.value = ''
}
</script>

<template>
  <div class="bug-field">
    <span class="bug-field-label">
      {{ field.label }}
      <em v-if="field.required">必填</em>
    </span>

    <template v-if="!field.editable">
      <div class="bug-field-readonly">{{ field.display || '—' }}</div>
    </template>

    <select
      v-else-if="field.type === 'select'"
      :value="typeof field.selected === 'string' ? field.selected : ''"
      @change="setSelected(($event.target as HTMLSelectElement).value || null)"
    >
      <option value="">（不填）</option>
      <option v-for="option in field.options" :key="option.id" :value="option.name">
        {{ option.name }}
      </option>
    </select>

    <div v-else-if="field.type === 'multi_select'" class="bug-tags">
      <button
        v-for="option in field.options"
        :key="option.id"
        type="button"
        class="bug-tag"
        :class="{ on: isSelected(optionName(option)) }"
        @click="toggleByName(optionName(option))"
      >
        {{ option.name }}
      </button>
      <span v-if="!field.options.length" class="bug-field-readonly">（暂无选项）</span>
    </div>

    <div v-else-if="isUserField" class="bug-field-composite">
      <input
        v-if="searchable"
        v-model="keyword"
        class="bug-field-search"
        type="search"
        placeholder="搜索人员"
      />
      <div class="bug-tags bug-tags-scroll">
        <button
          v-for="option in filteredOptions"
          :key="option.id"
          type="button"
          class="bug-tag bug-person"
          :class="{ on: isSelected(optionId(option)) }"
          :title="isSelected(optionId(option)) ? '点击移除' : '点击选择'"
          @click="toggleById(optionId(option))"
        >
          {{ option.name }}<span v-if="isSelected(optionId(option))" class="bug-person-x">×</span>
        </button>
      </div>
      <span v-if="!field.options.length" class="bug-field-readonly">（暂无候选人员）</span>
    </div>

    <div v-else-if="isRelationField" class="bug-field-composite">
      <div v-if="field.options.length" class="bug-tags">
        <button
          v-for="option in field.options"
          :key="option.id"
          type="button"
          class="bug-tag"
          :class="{ on: isSelected(optionId(option)) }"
          @click="toggleById(optionId(option))"
        >
          {{ option.name || option.id }}
        </button>
      </div>
      <input
        v-if="!isMultiValueField"
        :value="typeof field.selected === 'string' ? field.selected : currentArray()[0] || ''"
        type="text"
        placeholder="输入工作项 ID"
        @input="updateSingleRelation(($event.target as HTMLInputElement).value)"
      />
      <div v-else class="bug-relation-add">
        <input
          v-model="manualRelatedId"
          type="text"
          placeholder="输入工作项 ID 后回车添加"
          @keydown.enter.prevent="addManualRelation"
        />
        <button type="button" @click="addManualRelation">添加</button>
      </div>
    </div>

    <textarea
      v-else
      :value="typeof field.selected === 'string' ? field.selected : ''"
      rows="2"
      @input="setSelected(($event.target as HTMLTextAreaElement).value)"
    />
  </div>
</template>
