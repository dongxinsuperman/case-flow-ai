import { defineStore } from 'pinia'
import { request } from '../api/client'
import type { BugImageUploadResult, CoverageLane, CoverageState, ExecutionStatus, ExecutionTarget, User } from '../types/case'
import type {
  AIPhoneDeviceList,
  AIPhoneSubmitResult,
  BugDraft,
  BugSubmitResult,
  CasePlatformResult,
  QuickAIPhoneSubmitPayload,
  QuickCaseItem,
  QuickCaseUpdate,
  QuickExportResult,
  QuickFeishuTarget,
  QuickFunctionFile,
  QuickImportResult,
  QuickSessionDetail,
  QuickSessionSummary,
  QuickWorkItemUpdate,
  RepairApplyResult,
  RepairPreview,
} from '../types/quick'

const QUICK_SESSION_STORAGE_KEY = 'caseFlow.quickSessionId'

function readStoredSessionId(): string {
  return typeof localStorage !== 'undefined'
    ? localStorage.getItem(QUICK_SESSION_STORAGE_KEY) || ''
    : ''
}

function persistSessionId(sessionId: string): void {
  if (typeof localStorage === 'undefined') return
  if (sessionId) {
    localStorage.setItem(QUICK_SESSION_STORAGE_KEY, sessionId)
  } else {
    localStorage.removeItem(QUICK_SESSION_STORAGE_KEY)
  }
}

export const useQuickSessionStore = defineStore('quickSession', {
  state: () => ({
    users: [] as User[],
    sessionId: readStoredSessionId(),
    session: null as QuickSessionSummary | null,
    cases: [] as QuickCaseItem[],
    selectedCaseId: null as number | null,
    selectedCaseIds: [] as number[],
    statusFilter: 'all' as ExecutionStatus | 'attention' | 'all',
    currentUserId: 0,
    loading: false,
    error: '',
  }),
  getters: {
    active(state) {
      return Boolean(state.session)
    },
    summary(state) {
      const cases = state.cases
      return {
        caseCount: cases.length,
        notRun: cases.filter((item) => item.executionStatus === 'not_run').length,
        running: cases.filter((item) => item.executionStatus === 'running').length,
        passed: cases.filter((item) => item.executionStatus === 'passed').length,
        failed: cases.filter((item) => item.executionStatus === 'failed').length,
      }
    },
    filteredCases(state) {
      if (state.statusFilter === 'all') return state.cases
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
    async loadUsers() {
      if (this.users.length > 0) return
      this.users = await request<User[]>('/api/v1/users')
    },
    applyDetail(detail: QuickSessionDetail) {
      this.session = detail.session
      this.sessionId = detail.session.sessionId
      this.currentUserId = detail.session.currentUserId ?? this.currentUserId ?? 0
      this.cases = detail.cases
      persistSessionId(this.sessionId)
      if (!this.cases.some((item) => item.id === this.selectedCaseId)) {
        this.selectedCaseId = this.cases[0]?.id ?? null
      }
    },
    async restore() {
      if (!this.sessionId) return
      this.loading = true
      this.error = ''
      try {
        await this.loadSession(this.sessionId)
      } catch {
        this.sessionId = ''
        this.session = null
        this.cases = []
        persistSessionId('')
      } finally {
        this.loading = false
      }
    },
    async loadSession(sessionId?: string) {
      const targetSessionId = sessionId || this.sessionId
      const detail = await request<QuickSessionDetail>(`/api/v1/quick/sessions/${encodeURIComponent(targetSessionId)}`)
      this.applyDetail(detail)
    },
    async importMarkdown(payload: { filename: string; content: string; functionFiles: QuickFunctionFile[] }) {
      const detail = await request<QuickImportResult>('/api/v1/quick/sessions/import', {
        method: 'POST',
        body: payload as unknown as BodyInit,
      })
      this.applyDetail(detail)
      return detail
    },
    async patchSession(payload: Partial<Pick<QuickSessionSummary, 'feishuRequirementUrl' | 'feishuBugUrl' | 'currentUserId'>> & {
      functionFiles?: QuickFunctionFile[]
    }) {
      if (!this.sessionId) return null
      const detail = await request<QuickSessionDetail>(`/api/v1/quick/sessions/${encodeURIComponent(this.sessionId)}`, {
        method: 'PATCH',
        body: payload as unknown as BodyInit,
      })
      this.applyDetail(detail)
      return detail
    },
    async bindFeishuTarget(url: string) {
      if (!this.sessionId) throw new Error('缺少 quick session')
      const result = await request<QuickFeishuTarget>(
        `/api/v1/quick/sessions/${encodeURIComponent(this.sessionId)}/feishu-target`,
        { method: 'POST', body: { url } as unknown as BodyInit },
      )
      await this.loadSession()
      return result
    },
    async checkFeishuLink(url: string, kind: 'requirement' | 'bug'): Promise<QuickFeishuTarget> {
      if (!this.sessionId) throw new Error('缺少 quick session')
      return request<QuickFeishuTarget>(
        `/api/v1/quick/sessions/${encodeURIComponent(this.sessionId)}/feishu-link-check`,
        { method: 'POST', body: { url, kind } as unknown as BodyInit },
      )
    },
    async exportMarkdown(options?: { clear?: boolean }) {
      if (!this.sessionId) throw new Error('缺少 quick session')
      const clear = options?.clear ?? true
      const result = await request<QuickExportResult>(
        `/api/v1/quick/sessions/${encodeURIComponent(this.sessionId)}/export?clear=${clear ? 'true' : 'false'}`,
        { method: 'POST' },
      )
      if (result.cleared) this.clearLocal()
      return result
    },
    async clearRemote() {
      if (this.sessionId) {
        await request(`/api/v1/quick/sessions/${encodeURIComponent(this.sessionId)}`, { method: 'DELETE' })
      }
      this.clearLocal()
    },
    clearLocal() {
      this.sessionId = ''
      this.session = null
      this.cases = []
      this.selectedCaseId = null
      this.selectedCaseIds = []
      this.statusFilter = 'all'
      persistSessionId('')
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
      if (checked) selected.add(caseId)
      else selected.delete(caseId)
      this.selectedCaseIds = [...selected]
    },
    toggleAllFilteredCases(checked: boolean) {
      this.selectedCaseIds = checked ? this.filteredCases.map((item) => item.id) : []
    },
    clearCaseSelection() {
      this.selectedCaseIds = []
    },
    async updateCase(caseId: number, payload: QuickCaseUpdate) {
      await request(`/api/v1/quick/cases/${caseId}`, {
        method: 'PATCH',
        body: payload as unknown as BodyInit,
      })
      await this.loadSession()
    },
    async updateCaseWorkItem(payload: QuickWorkItemUpdate) {
      await request('/api/v1/quick/case-work-items/update', {
        method: 'POST',
        body: payload as unknown as BodyInit,
      })
      await this.loadSession()
    },
    async updateCaseStatus(caseId: number, executionStatus: ExecutionStatus) {
      await this.updateCaseWorkItem({ caseId, executionStatus, runEnabled: executionStatus === 'running' })
    },
    async updateCaseTarget(caseId: number, executionTarget: ExecutionTarget) {
      await this.updateCaseWorkItem({ caseId, executionTarget })
    },
    async updateCasesStatus(caseIds: number[], executionStatus: ExecutionStatus) {
      await Promise.all(caseIds.map((caseId) =>
        request('/api/v1/quick/case-work-items/update', {
          method: 'POST',
          body: { caseId, executionStatus, runEnabled: executionStatus === 'running' } as unknown as BodyInit,
        }),
      ))
      this.clearCaseSelection()
      await this.loadSession()
    },
    async cycleCoverage(caseId: number, lane: CoverageLane) {
      const item = this.cases.find((candidate) => candidate.id === caseId)
      if (!item) return
      const next: Record<CoverageState, CoverageState> = {
        none: 'passed',
        passed: 'failed',
        failed: 'none',
      }
      const current = item.coverage ?? {}
      const nextState = next[current[lane] ?? 'none']
      item.coverage = { ...current, [lane]: nextState }
      await request('/api/v1/quick/case-work-items/coverage', {
        method: 'POST',
        body: { caseId, lane, state: nextState } as unknown as BodyInit,
      })
    },
    async listAIPhoneDevices(): Promise<AIPhoneDeviceList> {
      return request<AIPhoneDeviceList>('/api/v1/quick/aiphone/devices')
    },
    async listAIWebDevices(): Promise<AIPhoneDeviceList> {
      return request<AIPhoneDeviceList>('/api/v1/quick/aiweb/devices')
    },
    async submitAIPhoneCases(payload: QuickAIPhoneSubmitPayload): Promise<AIPhoneSubmitResult> {
      const result = await request<AIPhoneSubmitResult>('/api/v1/quick/executions/aiphone/submit', {
        method: 'POST',
        body: payload as unknown as BodyInit,
      })
      this.clearCaseSelection()
      await this.loadSession()
      return result
    },
    async submitAIWebCases(payload: QuickAIPhoneSubmitPayload): Promise<AIPhoneSubmitResult> {
      const result = await request<AIPhoneSubmitResult>('/api/v1/quick/executions/aiweb/submit', {
        method: 'POST',
        body: {
          ...payload,
          deviceAliasPools: payload.deviceAliasPools ?? null,
          cacheMode: payload.cacheMode ?? 'off',
          retryMax: payload.retryMax ?? 0,
        } as unknown as BodyInit,
      })
      this.clearCaseSelection()
      await this.loadSession()
      return result
    },
    async submitAIAPICases(payload: QuickAIPhoneSubmitPayload): Promise<AIPhoneSubmitResult> {
      const result = await request<AIPhoneSubmitResult>('/api/v1/quick/executions/aiapi/submit', {
        method: 'POST',
        body: {
          ...payload,
          deviceAliasPools: null,
          cacheMode: 'off',
          retryMax: 0,
        } as unknown as BodyInit,
      })
      this.clearCaseSelection()
      await this.loadSession()
      return result
    },
    async submitHybridCases(payload: QuickAIPhoneSubmitPayload): Promise<AIPhoneSubmitResult> {
      const result = await request<AIPhoneSubmitResult>('/api/v1/quick/executions/aihybrid/submit', {
        method: 'POST',
        body: {
          ...payload,
          deviceAliasPools: null,
          cacheMode: 'off',
          retryMax: 0,
        } as unknown as BodyInit,
      })
      this.clearCaseSelection()
      await this.loadSession()
      return result
    },
    async getCasePlatformResults(caseId: number): Promise<CasePlatformResult[]> {
      return request<CasePlatformResult[]>(`/api/v1/quick/cases/${caseId}/platform-results`)
    },
    async previewRepairs(caseIds: number[]): Promise<RepairPreview> {
      const result = await request<RepairPreview>('/api/v1/quick/cases/repair-preview', {
        method: 'POST',
        body: { caseIds } as unknown as BodyInit,
      })
      await this.loadSession()
      return result
    },
    async applyRepairDraft(
      draftId: number,
      edited?: { stepsText?: string; preconditions?: string; expectedResult?: string },
    ): Promise<RepairApplyResult> {
      const result = await request<RepairApplyResult>(`/api/v1/quick/cases/repair-drafts/${draftId}/apply`, {
        method: 'POST',
        body: {
          stepsText: edited?.stepsText,
          preconditions: edited?.preconditions,
          expectedResult: edited?.expectedResult,
        } as unknown as BodyInit,
      })
      await this.loadSession()
      return result
    },
    async setCurrentUser(userId: number) {
      this.currentUserId = userId
      await this.patchSession({ currentUserId: userId })
    },
    async getBugDraft(caseId: number): Promise<BugDraft> {
      const userId = this.currentUserId || this.session?.currentUserId || 0
      const params = new URLSearchParams()
      if (userId) params.set('user_id', String(userId))
      return request<BugDraft>(`/api/v1/quick/cases/${caseId}/bug-draft?${params.toString()}`)
    },
    async uploadBugImages(files: File[]) {
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
    ): Promise<BugSubmitResult> {
      const userId = this.currentUserId || this.session?.currentUserId || 0
      const query = userId ? `?user_id=${encodeURIComponent(String(userId))}` : ''
      const result = await request<BugSubmitResult>(`/api/v1/quick/cases/${caseId}/bug${query}`, {
        method: 'POST',
        body: payload as unknown as BodyInit,
      })
      await this.loadSession()
      return result
    },
  },
})
