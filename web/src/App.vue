<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterView } from 'vue-router'
import { useRoute } from 'vue-router'
import { request } from './api/client'
import AgentAssistant from './components/agent/AgentAssistant.vue'
import UserSearchSelect from './components/UserSearchSelect.vue'
import { useCaseWorkbenchStore } from './stores/caseWorkbench'
import { useQuickSessionStore } from './stores/quickSession'
import type { AgentContextRef } from './types/agent'
import { filterVisibleUsers } from './utils/visibleUsers'

const store = useCaseWorkbenchStore()
const quickStore = useQuickSessionStore()
const route = useRoute()
const osAgentEnabled = ref(false)
const selectableUsers = computed(() => filterVisibleUsers(store.users))
const assistantUserId = computed(() => (
  route.meta.quickLayout
    ? quickStore.currentUserId || quickStore.session?.currentUserId || 0
    : store.currentUserId
))
const assistantContextRef = computed<AgentContextRef | null>(() => {
  if (route.meta.quickLayout) {
    const quickSessionId = quickStore.session?.sessionId || quickStore.sessionId
    return quickSessionId ? { mode: 'quick', quickSessionId } : null
  }
  if (route.name === 'home' && store.selectedRequirementId) {
    return { mode: 'standard', requirementItemId: store.selectedRequirementId }
  }
  return null
})

const reloadPage = () => {
  window.location.reload()
}

const handleUserChange = (value: number | string | null) => {
  if (typeof value === 'number') {
    void store.setCurrentUser(value)
  }
}

const handleDemoModeChange = (event: Event) => {
  const enabled = (event.target as HTMLInputElement).checked
  void store.setDemoMode(enabled)
}

const initializeStandardShell = () => {
  if (!route.meta.quickLayout) {
    store.initDemoMode()
    void store.loadUsers()
  }
}

const loadAppConfig = async () => {
  try {
    const config = await request<{ osAgentEnabled: boolean }>('/api/v1/config')
    osAgentEnabled.value = config.osAgentEnabled
  } catch {
    osAgentEnabled.value = false
  }
}

onMounted(() => {
  void loadAppConfig()
  initializeStandardShell()
})

watch(() => route.meta.quickLayout, () => {
  initializeStandardShell()
})
</script>

<template>
  <RouterView v-if="route.meta.quickLayout" />
  <div v-else class="app-shell">
    <header class="topbar">
      <div class="topbar-title">
        <strong>Case Flow AI 测试工作台</strong>
        <span>需求目录管理、测试资产导入、智能变更分析与执行反馈闭环</span>
      </div>
      <div class="topbar-actions">
        <RouterLink class="quick-mode-top-link" to="/quick" title="进入快速模式">
          快速模式
        </RouterLink>
        <label class="demo-mode-toggle" title="演示模式只渲染首页假数据，不访问后端或外部系统。">
          <input type="checkbox" :checked="store.demoMode" @change="handleDemoModeChange" />
          <span>演示模式</span>
        </label>
        <label class="topbar-user-control">
          <span>当前用户</span>
          <UserSearchSelect
            class="topbar-user-select"
            :model-value="store.currentUserId"
            :users="selectableUsers"
            placeholder="请选择"
            aria-label="选择当前用户"
            @update:model-value="handleUserChange"
          />
        </label>
        <button type="button" @click="reloadPage">刷新</button>
      </div>
    </header>
    <nav class="main-tabs">
      <RouterLink to="/">首页</RouterLink>
      <RouterLink to="/requirements">飞书项目 / 目录管理</RouterLink>
      <RouterLink to="/case-assets">Case 资产</RouterLink>
      <RouterLink to="/function-maps">Function Map</RouterLink>
      <RouterLink to="/execution-logs">执行流水</RouterLink>
    </nav>
    <div v-if="store.demoMode" class="demo-mode-banner">
      演示模式：当前首页数据仅用于产品预览，不会访问飞书、数据库或 AI Phone。关闭后恢复真实后端数据。
    </div>
    <main>
      <RouterView />
    </main>
  </div>
  <AgentAssistant
    v-if="osAgentEnabled && assistantUserId > 0"
    :initial-user-id="assistantUserId"
    :context-ref="assistantContextRef"
  />
</template>
