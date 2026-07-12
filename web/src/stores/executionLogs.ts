import { defineStore } from 'pinia'
import { request } from '../api/client'
import type { ExecutionCallLog, ExecutionCallLogPage } from '../types/executionLog'

const PAGE_SIZE = 20

export const useExecutionLogsStore = defineStore('executionLogs', {
  state: () => ({
    items: [] as ExecutionCallLog[],
    total: 0,
    page: 1,
    pageSize: PAGE_SIZE,
    loading: false,
    error: '',
    statusFilter: '' as '' | string,
    executorFilter: '' as '' | string,
    modeFilter: '' as '' | string,
  }),
  getters: {
    pageCount(state): number {
      return Math.max(1, Math.ceil(state.total / state.pageSize))
    },
  },
  actions: {
    async load(page?: number) {
      const targetPage = page ?? this.page
      this.loading = true
      this.error = ''
      try {
        const params = new URLSearchParams()
        if (this.statusFilter) params.set('status', this.statusFilter)
        if (this.executorFilter) params.set('executor', this.executorFilter)
        if (this.modeFilter) params.set('mode', this.modeFilter)
        params.set('page', String(targetPage))
        params.set('page_size', String(this.pageSize))
        const result = await request<ExecutionCallLogPage>(
          `/api/v1/execution-strategy-call-logs?${params.toString()}`,
        )
        this.items = result.items
        this.total = result.total
        this.page = result.page
      } catch (err) {
        this.error = err instanceof Error ? err.message : '加载执行流水失败'
      } finally {
        this.loading = false
      }
    },
    setFilter(kind: 'status' | 'executor' | 'mode', value: string) {
      if (kind === 'status') this.statusFilter = value
      if (kind === 'executor') this.executorFilter = value
      if (kind === 'mode') this.modeFilter = value
      void this.load(1)
    },
    changePage(delta: number) {
      const next = this.page + delta
      if (next < 1 || next > this.pageCount) return
      void this.load(next)
    },
  },
})
