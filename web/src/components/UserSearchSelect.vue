<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import type { User } from '../types/case'

type SelectValue = number | string | null

interface SelectOption {
  value: SelectValue
  label: string
  searchText: string
}

const props = withDefaults(
  defineProps<{
    modelValue: SelectValue
    users: User[]
    placeholder?: string
    searchPlaceholder?: string
    allLabel?: string
    allValue?: SelectValue
    disabled?: boolean
    ariaLabel?: string
  }>(),
  {
    placeholder: '请选择人员',
    searchPlaceholder: '搜索姓名',
    allValue: 'all',
    disabled: false,
    ariaLabel: '选择人员',
  },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: SelectValue): void
  (event: 'change', value: SelectValue): void
}>()

const rootRef = ref<HTMLElement | null>(null)
const searchRef = ref<HTMLInputElement | null>(null)
const open = ref(false)
const keyword = ref('')
const highlightedIndex = ref(0)
const listboxId = `user-search-select-${Math.random().toString(36).slice(2)}`

const hasAllOption = computed(() => typeof props.allLabel === 'string' && props.allLabel.length > 0)

const options = computed<SelectOption[]>(() => {
  const baseOptions = props.users.map((user) => ({
    value: user.id,
    label: user.displayName,
    searchText: `${user.displayName} ${user.name}`.toLowerCase(),
  }))

  if (!hasAllOption.value) {
    return baseOptions
  }

  return [
    {
      value: props.allValue,
      label: props.allLabel ?? '',
      searchText: (props.allLabel ?? '').toLowerCase(),
    },
    ...baseOptions,
  ]
})

const selectedOption = computed(() => options.value.find((option) => option.value === props.modelValue) ?? null)
const selectedLabel = computed(() => selectedOption.value?.label ?? props.placeholder)

const filteredOptions = computed(() => {
  const normalized = keyword.value.trim().toLowerCase()
  if (!normalized) {
    return options.value
  }
  return options.value.filter((option) => option.searchText.includes(normalized))
})

function syncHighlightedIndex() {
  if (!filteredOptions.value.length) {
    highlightedIndex.value = -1
    return
  }
  const selectedIndex = filteredOptions.value.findIndex((option) => option.value === props.modelValue)
  highlightedIndex.value = selectedIndex >= 0 ? selectedIndex : 0
}

function closeMenu() {
  open.value = false
  keyword.value = ''
}

async function openMenu() {
  if (props.disabled) {
    return
  }
  open.value = true
  syncHighlightedIndex()
  await nextTick()
  searchRef.value?.focus()
}

function toggleMenu() {
  if (open.value) {
    closeMenu()
    return
  }
  void openMenu()
}

function selectOption(option: SelectOption) {
  emit('update:modelValue', option.value)
  emit('change', option.value)
  closeMenu()
}

function moveHighlight(delta: number) {
  if (!open.value) {
    void openMenu()
    return
  }
  const count = filteredOptions.value.length
  if (!count) {
    highlightedIndex.value = -1
    return
  }
  highlightedIndex.value = (highlightedIndex.value + delta + count) % count
}

function selectHighlighted() {
  const option = filteredOptions.value[highlightedIndex.value]
  if (option) {
    selectOption(option)
  }
}

function handleDocumentPointerDown(event: PointerEvent) {
  const target = event.target
  if (target instanceof Node && !rootRef.value?.contains(target)) {
    closeMenu()
  }
}

watch(keyword, () => {
  highlightedIndex.value = filteredOptions.value.length ? 0 : -1
})

watch(open, (nextOpen) => {
  if (nextOpen) {
    document.addEventListener('pointerdown', handleDocumentPointerDown)
    return
  }
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
})

watch(() => props.modelValue, syncHighlightedIndex)

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
})
</script>

<template>
  <div ref="rootRef" class="user-search-select" :class="{ open, disabled }">
    <button
      type="button"
      class="user-search-select__trigger"
      :disabled="disabled"
      :aria-label="ariaLabel"
      :aria-expanded="open"
      :aria-controls="listboxId"
      aria-haspopup="listbox"
      @click="toggleMenu"
      @keydown.down.prevent="moveHighlight(1)"
      @keydown.up.prevent="moveHighlight(-1)"
      @keydown.enter.prevent="toggleMenu"
      @keydown.esc.prevent="closeMenu"
    >
      <span class="user-search-select__value" :class="{ placeholder: !selectedOption }">{{ selectedLabel }}</span>
      <span class="user-search-select__chevron" aria-hidden="true"></span>
    </button>

    <div v-if="open" class="user-search-select__menu">
      <input
        ref="searchRef"
        v-model="keyword"
        class="user-search-select__search"
        type="search"
        :placeholder="searchPlaceholder"
        @keydown.down.prevent="moveHighlight(1)"
        @keydown.up.prevent="moveHighlight(-1)"
        @keydown.enter.prevent="selectHighlighted"
        @keydown.esc.prevent="closeMenu"
      />
      <ul :id="listboxId" class="user-search-select__options" role="listbox">
        <li v-if="!filteredOptions.length" class="user-search-select__empty">无匹配人员</li>
        <li
          v-for="(option, index) in filteredOptions"
          :key="String(option.value)"
          class="user-search-select__option"
          :class="{ selected: option.value === modelValue, highlighted: index === highlightedIndex }"
          role="option"
          :aria-selected="option.value === modelValue"
          @mousedown.prevent="selectOption(option)"
        >
          {{ option.label }}
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.user-search-select {
  position: relative;
  min-width: 0;
  font-size: 13px;
}

.user-search-select__trigger {
  width: 100%;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border: 1px solid #cfd8e6;
  border-radius: 6px;
  background: #fff;
  color: #172033;
  padding: 0 9px 0 10px;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

.user-search-select__trigger:focus-visible {
  outline: 2px solid rgba(37, 99, 235, 0.32);
  outline-offset: 1px;
  border-color: #2563eb;
}

.user-search-select__value {
  min-width: 0;
  overflow: hidden;
  color: #172033;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-search-select__value.placeholder {
  color: #64748b;
}

.user-search-select__chevron {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-right: 2px solid #64748b;
  border-bottom: 2px solid #64748b;
  transform: translateY(-2px) rotate(45deg);
}

.user-search-select.open .user-search-select__chevron {
  transform: translateY(2px) rotate(225deg);
}

.user-search-select__menu {
  position: absolute;
  z-index: 80;
  top: calc(100% + 4px);
  right: 0;
  width: max(100%, 210px);
  overflow: hidden;
  border: 1px solid #cfd8e6;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 14px 34px rgba(15, 23, 42, 0.18);
}

.user-search-select__search {
  width: calc(100% - 16px);
  height: 32px;
  margin: 8px;
  border: 1px solid #d7dfec;
  border-radius: 6px;
  color: #172033;
  padding: 0 9px;
  font: inherit;
  outline: none;
}

.user-search-select__search:focus {
  border-color: #2563eb;
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.14);
}

.user-search-select__options {
  max-height: 224px;
  overflow: auto;
  list-style: none;
  margin: 0;
  padding: 4px;
}

.user-search-select__option,
.user-search-select__empty {
  min-height: 32px;
  display: flex;
  align-items: center;
  border-radius: 6px;
  color: #172033;
  padding: 6px 8px;
  font-weight: 700;
}

.user-search-select__option {
  cursor: pointer;
}

.user-search-select__option.highlighted {
  background: #eff6ff;
  color: #1d4ed8;
}

.user-search-select__option.selected {
  background: #dbeafe;
  color: #1e40af;
}

.user-search-select__empty {
  color: #64748b;
}

.user-search-select.disabled .user-search-select__trigger {
  cursor: not-allowed;
  opacity: 0.58;
}
</style>
