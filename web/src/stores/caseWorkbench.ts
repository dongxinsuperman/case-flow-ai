import { defineStore } from 'pinia'
import { request } from '../api/client'
import {
  applyDemoRepairDraft,
  cycleDemoCaseCoverage,
  getDemoBugDraft,
  getDemoCasePlatformResults,
  getDemoHomeDashboard,
  getDemoUsers,
  listDemoAIPhoneDevices,
  listDemoCases,
  previewDemoRepairs,
  resetDemoWorkbench,
  submitDemoAIPhoneCases,
  submitDemoBug,
  updateDemoCaseAsset,
  updateDemoCasesStatus,
  updateDemoCaseWorkItem,
} from '../demo/caseWorkbenchDemo'
import type {
  AIPhoneDeviceList,
  AIPhoneSubmitResult,
  BugDraft,
  BugImageUploadResult,
  BugSubmitResult,
  CaseAssetUpdate,
  CaseListItem,
  CasePlatformResult,
  CaseWorkItemUpdate,
  CoverageLane,
  CoverageState,
  ExecutionStatus,
  ExecutionTarget,
  FeishuPullJob,
  HomeDashboard,
  RepairApplyResult,
  RepairPreview,
  User,
} from '../types/case'

const CURRENT_USER_STORAGE_KEY = 'caseFlow.currentUserId'
const DEMO_MODE_STORAGE_KEY = 'caseFlow.demoMode'

function readUrlDemoMode(): boolean | null {
  if (typeof window === 'undefined') {
    return null
  }
  const value = new URLSearchParams(window.location.search).get('demo')
  if (value === '1' || value === 'true') {
    return true
  }
  if (value === '0' || value === 'false') {
    return false
  }
  return null
}

function readStoredDemoMode(): boolean {
  const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(DEMO_MODE_STORAGE_KEY) : null
  return raw === 'true'
}

function readInitialDemoMode(): boolean {
  return readUrlDemoMode() ?? readStoredDemoMode()
}

function persistDemoMode(enabled: boolean): void {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(DEMO_MODE_STORAGE_KEY, String(enabled))
  }
}

function readStoredUserId(): number {
  const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(CURRENT_USER_STORAGE_KEY) : null
  const parsed = raw ? Number(raw) : NaN
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
}

function persistUserId(userId: number): void {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(CURRENT_USER_STORAGE_KEY, String(userId))
  }
}

export const useCaseWorkbenchStore = defineStore('caseWorkbench', {
  state: () => ({
    users: [] as User[],
    currentUserId: readStoredUserId(),
    dashboard: null as HomeDashboard | null,
    cases: [] as CaseListItem[],
    selectedRequirementId: null as number | null,
    selectedCaseId: null as number | null,
    selectedCaseIds: [] as number[],
    statusFilter: 'all' as ExecutionStatus | 'attention' | 'all',
    loading: false,
    error: '',
    demoMode: readInitialDemoMode(),
  }),
  getters: {
    summary(state) {
      return state.dashboard?.summary ?? {
        requirements: 0,
        caseCount: 0,
        notRun: 0,
        running: 0,
        passed: 0,
        failed: 0,
        attentionChanged: 0,
      }
    },
    selectedRequirement(state) {
      return state.dashboard?.requirements.find(
        (item) => item.requirementItemId === state.selectedRequirementId,
      ) ?? null
    },
    filteredCases(state) {
      if (state.statusFilter === 'all') {
        return state.cases
      }
      if (state.statusFilter === 'attention') {
        return state.cases.filter((item) => item.attentionReason === '变更待确认')
      }
      return state.cases.filter((item) => item.executionStatus === state.statusFilter)
    },
    selectedCase(state) {
      return state.cases.find((item) => item.id === state.selectedCaseId) ?? state.cases[0] ?? null
    },
    selectedVisibleCases(state) {
      const selected = new Set(state.selectedCaseIds)
      const visible = state.statusFilter === 'all'
        ? state.cases
        : state.statusFilter === 'attention'
          ? state.cases.filter((item) => item.attentionReason === '变更待确认')
          : state.cases.filter((item) => item.executionStatus === state.statusFilter)
      return visible.filter((item) => selected.has(item.id))
    },
  },
  actions: {
    initDemoMode() {
      const urlMode = readUrlDemoMode()
      if (urlMode !== null) {
        this.demoMode = urlMode
        persistDemoMode(urlMode)
      }
    },
    async setDemoMode(enabled: boolean) {
      if (this.demoMode === enabled) {
        return
      }
      this.demoMode = enabled
      persistDemoMode(enabled)
      this.users = []
      this.dashboard = null
      this.cases = []
      this.selectedRequirementId = null
      this.selectedCaseId = null
      this.selectedCaseIds = []
      this.statusFilter = 'all'
      this.error = ''
      if (enabled) {
        resetDemoWorkbench()
      }
      await this.loadInitial()
    },
    async loadUsers() {
      if (this.demoMode) {
        this.users = getDemoUsers()
        const remembered = this.users.some((user) => user.id === this.currentUserId)
        if (!remembered) {
          this.currentUserId = this.users[0]?.id ?? 0
        }
        if (this.currentUserId) {
          persistUserId(this.currentUserId)
        }
        return
      }
      if (this.users.length > 0) {
        return
      }
      this.users = await request<User[]>('/api/v1/users')
      // 当前用户记忆：localStorage 里的人若仍存在就沿用，否则退回第一个并落盘。
      const remembered = this.users.some((user) => user.id === this.currentUserId)
      if (!remembered) {
        this.currentUserId = this.users[0]?.id ?? 0
      }
      if (this.currentUserId) {
        persistUserId(this.currentUserId)
      }
    },
    async setCurrentUser(userId: number) {
      this.currentUserId = userId
      persistUserId(userId)
      this.selectedRequirementId = null
      this.selectedCaseId = null
      this.selectedCaseIds = []
      await this.loadHome()
    },
    async loadInitial() {
      this.loading = true
      this.error = ''
      try {
        await this.loadUsers()
        await this.loadHome()
      } catch (error) {
        this.error = error instanceof Error ? error.message : String(error)
      } finally {
        this.loading = false
      }
    },
    async loadHome() {
      if (this.demoMode) {
        this.dashboard = getDemoHomeDashboard(this.currentUserId)
        const requirements = this.dashboard.requirements
        if (!requirements.some((item) => item.requirementItemId === this.selectedRequirementId)) {
          this.selectedRequirementId = requirements[0]?.requirementItemId ?? null
        }
        await this.loadCases()
        return
      }
      this.dashboard = await request<HomeDashboard>(`/api/v1/home?user_id=${this.currentUserId}`)
      const requirements = this.dashboard.requirements
      if (!requirements.some((item) => item.requirementItemId === this.selectedRequirementId)) {
        this.selectedRequirementId = requirements[0]?.requirementItemId ?? null
      }
      await this.loadCases()
    },
    async loadCases() {
      if (!this.selectedRequirementId) {
        this.cases = []
        return
      }
      if (this.demoMode) {
        this.cases = listDemoCases(this.selectedRequirementId)
        if (!this.cases.some((item) => item.id === this.selectedCaseId)) {
          this.selectedCaseId = this.cases[0]?.id ?? null
        }
        return
      }
      this.cases = await request<CaseListItem[]>(
        `/api/v1/workbench-cases?requirement_item_id=${this.selectedRequirementId}`,
      )
      if (!this.cases.some((item) => item.id === this.selectedCaseId)) {
        this.selectedCaseId = this.cases[0]?.id ?? null
      }
    },
    async selectRequirement(requirementItemId: number) {
      this.selectedRequirementId = requirementItemId
      this.selectedCaseId = null
      this.selectedCaseIds = []
      await this.loadCases()
    },
    async setAutoDiscovery(requirementItemId: number, enabled: boolean) {
      const task = this.dashboard?.requirements.find(
        (item) => item.requirementItemId === requirementItemId,
      )
      if (!task) {
        return
      }
      const previous = task.autoDiscoveryEnabled
      task.autoDiscoveryEnabled = enabled
      if (this.demoMode) {
        return
      }
      try {
        await request(`/api/v1/requirement-items/${requirementItemId}/auto-discovery`, {
          method: 'PATCH',
          body: { enabled } as unknown as BodyInit,
        })
      } catch (err) {
        task.autoDiscoveryEnabled = previous
        throw err
      }
    },
    selectCase(caseId: number) {
      this.selectedCaseId = caseId
    },
    setStatusFilter(filter: ExecutionStatus | 'attention' | 'all') {
      this.statusFilter = filter
      this.selectedCaseIds = []
    },
    toggleCaseSelection(caseId: number, checked: boolean) {
      const selected = new Set(this.selectedCaseIds)
      if (checked) {
        selected.add(caseId)
      } else {
        selected.delete(caseId)
      }
      this.selectedCaseIds = [...selected]
    },
    toggleAllFilteredCases(checked: boolean) {
      this.selectedCaseIds = checked ? this.filteredCases.map((item) => item.id) : []
    },
    clearCaseSelection() {
      this.selectedCaseIds = []
    },
    async updateCaseWorkItem(payload: CaseWorkItemUpdate) {
      if (this.demoMode) {
        updateDemoCaseWorkItem(payload)
        await Promise.all([this.loadHome(), this.loadCases()])
        return
      }
      await request('/api/v1/case-work-items/update', {
        method: 'POST',
        body: payload as unknown as BodyInit,
      })
      await Promise.all([this.loadHome(), this.loadCases()])
    },
    async updateCaseStatus(caseId: number, executionStatus: ExecutionStatus) {
      await this.updateCaseWorkItem({
        caseId,
        executionStatus,
        runEnabled: executionStatus === 'running',
      })
    },
    async updateCaseTarget(caseId: number, executionTarget: ExecutionTarget) {
      await this.updateCaseWorkItem({ caseId, executionTarget })
    },
    async getCasePlatformResults(caseId: number): Promise<CasePlatformResult[]> {
      if (this.demoMode) {
        return getDemoCasePlatformResults(caseId)
      }
      return request<CasePlatformResult[]>(`/api/v1/cases/${caseId}/platform-results`)
    },
    // 覆盖标记：三态点按循环（未执行→通过→失败→未执行）。泳道按执行器区分（app/web）。
    // 纯展示提醒，落库时只改 coverage，绝不触碰 execution_status / 报告 / bug。
    async cycleCoverage(caseId: number, lane: CoverageLane) {
      const next: Record<CoverageState, CoverageState> = {
        none: 'passed',
        passed: 'failed',
        failed: 'none',
      }
      const item = this.cases.find((candidate) => candidate.id === caseId)
      if (!item) {
        return
      }
      const current = item.coverage ?? {}
      const nextState = next[current[lane] ?? 'none']
      // 乐观更新：先改本地，立即反馈点击。
      item.coverage = { ...current, [lane]: nextState }
      if (this.demoMode) {
        cycleDemoCaseCoverage(caseId, lane)
        return
      }
      await request('/api/v1/case-work-items/coverage', {
        method: 'POST',
        body: { caseId, lane, state: nextState } as unknown as BodyInit,
      })
    },
    async updateCaseAsset(caseId: number, payload: CaseAssetUpdate) {
      if (this.demoMode) {
        updateDemoCaseAsset(caseId, payload)
        await Promise.all([this.loadHome(), this.loadCases()])
        return
      }
      await request(`/api/v1/cases/${caseId}`, {
        method: 'PATCH',
        body: payload as unknown as BodyInit,
      })
      await Promise.all([this.loadHome(), this.loadCases()])
    },
    async updateCasesStatus(caseIds: number[], executionStatus: ExecutionStatus) {
      if (this.demoMode) {
        updateDemoCasesStatus(caseIds, executionStatus)
        this.clearCaseSelection()
        await Promise.all([this.loadHome(), this.loadCases()])
        return
      }
      await Promise.all(
        caseIds.map((caseId) =>
          request('/api/v1/case-work-items/update', {
            method: 'POST',
            body: {
              caseId,
              executionStatus,
              runEnabled: executionStatus === 'running',
            } as unknown as BodyInit,
          }),
        ),
      )
      this.clearCaseSelection()
      await Promise.all([this.loadHome(), this.loadCases()])
    },
    async listFeishuSpaces() {
      const result = await request<{ spaces: { projectKey: string; name: string }[] }>(
        '/api/v1/sources/feishu-project/spaces',
      )
      return result.spaces
    },
    async pullFeishuProject(projectKeys?: string[]) {
      const query = projectKeys && projectKeys.length
        ? '?' + projectKeys.map((key) => `project_keys=${encodeURIComponent(key)}`).join('&')
        : ''
      return request<FeishuPullJob>(`/api/v1/sources/feishu-project/pull${query}`, { method: 'POST' })
    },
    async getFeishuPullJob(jobId: string) {
      return request<FeishuPullJob>(`/api/v1/sources/feishu-project/pull-jobs/${encodeURIComponent(jobId)}`)
    },
    async listAIPhoneDevices() {
      if (this.demoMode) {
        return listDemoAIPhoneDevices()
      }
      return request<AIPhoneDeviceList>('/api/v1/aiphone/devices')
    },
    async listAIWebDevices() {
      if (this.demoMode) {
        return listDemoAIPhoneDevices()
      }
      return request<AIPhoneDeviceList>('/api/v1/aiweb/devices')
    },
    async submitAIPhoneCases(
      caseIds: number[],
      deviceAliasPools: Record<string, string[]> | null,
      submissionName: string,
      options?: { cacheMode?: 'off' | 'v1' | 'v2' | 'v3'; retryMax?: number; executionRequestGroupId?: string },
    ) {
      if (this.demoMode) {
        void deviceAliasPools
        void options
        const result = submitDemoAIPhoneCases(caseIds, submissionName)
        this.clearCaseSelection()
        await Promise.all([this.loadHome(), this.loadCases()])
        return result
      }
      const result = await request<AIPhoneSubmitResult>('/api/v1/executions/aiphone/submit', {
        method: 'POST',
        body: {
          caseIds,
          deviceAliasPools,
          submissionName,
          cacheMode: options?.cacheMode ?? 'off',
          retryMax: options?.retryMax ?? 0,
          executionRequestGroupId: options?.executionRequestGroupId,
          currentUserId: this.currentUserId,
        } as unknown as BodyInit,
      })
      this.clearCaseSelection()
      await Promise.all([this.loadHome(), this.loadCases()])
      return result
    },
    async submitAIWebCases(
      caseIds: number[],
      deviceAliasPools: Record<string, string[]> | null,
      submissionName: string,
      options?: { cacheMode?: 'off' | 'v1' | 'v2' | 'v3'; retryMax?: number; executionRequestGroupId?: string },
    ) {
      if (this.demoMode) {
        void deviceAliasPools
        void options
        const result = submitDemoAIPhoneCases(caseIds, submissionName)
        this.clearCaseSelection()
        await Promise.all([this.loadHome(), this.loadCases()])
        return result
      }
      const result = await request<AIPhoneSubmitResult>('/api/v1/executions/aiweb/submit', {
        method: 'POST',
        body: {
          caseIds,
          deviceAliasPools,
          submissionName,
          cacheMode: options?.cacheMode ?? 'off',
          retryMax: options?.retryMax ?? 0,
          executionRequestGroupId: options?.executionRequestGroupId,
          currentUserId: this.currentUserId,
        } as unknown as BodyInit,
      })
      this.clearCaseSelection()
      await Promise.all([this.loadHome(), this.loadCases()])
      return result
    },
    async submitAIAPICases(
      caseIds: number[],
      submissionName: string,
      options?: { executionRequestGroupId?: string },
    ) {
      if (this.demoMode) {
        const result = submitDemoAIPhoneCases(caseIds, submissionName)
        this.clearCaseSelection()
        await Promise.all([this.loadHome(), this.loadCases()])
        return result
      }
      const result = await request<AIPhoneSubmitResult>('/api/v1/executions/aiapi/submit', {
        method: 'POST',
        body: {
          caseIds,
          submissionName,
          executionRequestGroupId: options?.executionRequestGroupId,
          currentUserId: this.currentUserId,
        } as unknown as BodyInit,
      })
      this.clearCaseSelection()
      await Promise.all([this.loadHome(), this.loadCases()])
      return result
    },
    async submitHybridCases(
      caseIds: number[],
      submissionName: string,
      options?: { executionRequestGroupId?: string },
    ) {
      if (this.demoMode) {
        const result = submitDemoAIPhoneCases(caseIds, submissionName)
        this.clearCaseSelection()
        await Promise.all([this.loadHome(), this.loadCases()])
        return result
      }
      const result = await request<AIPhoneSubmitResult>('/api/v1/executions/aihybrid/submit', {
        method: 'POST',
        body: {
          caseIds,
          submissionName,
          executionRequestGroupId: options?.executionRequestGroupId,
          currentUserId: this.currentUserId,
        } as unknown as BodyInit,
      })
      this.clearCaseSelection()
      await Promise.all([this.loadHome(), this.loadCases()])
      return result
    },
    async previewRepairs(caseIds: number[]) {
      if (this.demoMode) {
        const result = previewDemoRepairs(caseIds)
        await Promise.all([this.loadHome(), this.loadCases()])
        return result
      }
      const result = await request<RepairPreview>('/api/v1/cases/repair-preview', {
        method: 'POST',
        body: { caseIds } as unknown as BodyInit,
      })
      // 预览阶段后端已把失败分类(执行失败→断言/业务)写库；刷新列表+看板，卡片立刻反映新标签。
      await Promise.all([this.loadHome(), this.loadCases()])
      return result
    },
    async getBugDraft(caseId: number) {
      if (this.demoMode) {
        return getDemoBugDraft(caseId)
      }
      return request<BugDraft>(`/api/v1/cases/${caseId}/bug-draft?user_id=${this.currentUserId}`)
    },
    async uploadBugImages(files: File[]) {
      if (this.demoMode) {
        return files.map((file) => ({
          platform: '手动补图',
          image: URL.createObjectURL(file),
        }))
      }
      const form = new FormData()
      files.forEach((file) => form.append('files', file))
      const response = await fetch('/api/v1/bug-images', {
        method: 'POST',
        body: form,
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(typeof data?.detail === 'string' ? data.detail : response.statusText)
      }
      return ((data as BugImageUploadResult).images ?? [])
    },
    async submitBug(
      caseId: number,
      payload: {
        title: string
        description: string
        fields: unknown[]
        keyImages?: { platform: string; image: string }[]
      },
    ) {
      if (this.demoMode) {
        void payload
        const result = submitDemoBug(caseId)
        await Promise.all([this.loadHome(), this.loadCases()])
        return result
      }
      const result = await request<BugSubmitResult>(
        `/api/v1/cases/${caseId}/bug?user_id=${this.currentUserId}`,
        { method: 'POST', body: payload as unknown as BodyInit },
      )
      await Promise.all([this.loadHome(), this.loadCases()])
      return result
    },
    async applyRepairDraft(
      draftId: number,
      edited?: { stepsText?: string; preconditions?: string; expectedResult?: string },
    ) {
      if (this.demoMode) {
        const result = applyDemoRepairDraft(draftId, edited)
        await Promise.all([this.loadHome(), this.loadCases()])
        return result
      }
      const result = await request<RepairApplyResult>(`/api/v1/cases/repair-drafts/${draftId}/apply`, {
        method: 'POST',
        body: {
          stepsText: edited?.stepsText,
          preconditions: edited?.preconditions,
          expectedResult: edited?.expectedResult,
        } as unknown as BodyInit,
      })
      await Promise.all([this.loadHome(), this.loadCases()])
      return result
    },
  },
})
