import { createRouter, createWebHistory } from 'vue-router'
import CaseAssetsView from './pages/CaseAssetsView.vue'
import ExecutionLogsView from './pages/ExecutionLogsView.vue'
import FunctionMapAssetsView from './pages/FunctionMapAssetsView.vue'
import HomeView from './pages/HomeView.vue'
import QuickView from './pages/QuickView.vue'
import RequirementManagementView from './pages/RequirementManagementView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: HomeView,
    },
    {
      path: '/case-assets',
      name: 'case-assets',
      component: CaseAssetsView,
    },
    {
      path: '/function-maps',
      name: 'function-maps',
      component: FunctionMapAssetsView,
    },
    {
      path: '/execution-logs',
      name: 'execution-logs',
      component: ExecutionLogsView,
    },
    {
      path: '/requirements',
      name: 'requirements',
      component: RequirementManagementView,
    },
    {
      path: '/quick',
      name: 'quick',
      component: QuickView,
      meta: { quickLayout: true },
    },
  ],
})
