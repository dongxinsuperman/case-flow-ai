import { defineStore } from 'pinia'
import { request } from '../api/client'
import type {
  FunctionMapAsset,
  FunctionMapAssetContentInput,
  FunctionMapAssetExport,
  FunctionMapAssetInput,
  FunctionMapAssetListItem,
  FunctionMapAssetMetaInput,
  FunctionMapAssetPage,
  FunctionMapTarget,
  RequirementCatalog,
} from '../types/functionMap'

const ASSET_PAGE_SIZE = 20

export const useFunctionMapAssetsStore = defineStore('functionMapAssets', {
  state: () => ({
    assets: [] as FunctionMapAssetListItem[],
    assetsTotal: 0,
    assetsPage: 1,
    assetsPageSize: ASSET_PAGE_SIZE,
    loading: false,
    error: '',
    targetFilter: '' as '' | FunctionMapTarget,
    keyword: '',
  }),
  actions: {
    async loadAssets() {
      this.loading = true
      this.error = ''
      try {
        const params = new URLSearchParams()
        if (this.targetFilter) {
          params.set('target', this.targetFilter)
        }
        const keyword = this.keyword.trim()
        if (keyword) {
          params.set('keyword', keyword)
        }
        params.set('page', String(this.assetsPage))
        params.set('page_size', String(this.assetsPageSize))
        const result = await request<FunctionMapAssetPage>(
          `/api/v1/function-map-assets?${params.toString()}`,
        )
        this.assets = result.items
        this.assetsTotal = result.total
        this.assetsPage = result.page
      } catch (err) {
        this.error = err instanceof Error ? err.message : '加载 Function Map 资产失败'
      } finally {
        this.loading = false
      }
    },
    async getAsset(id: number): Promise<FunctionMapAsset> {
      return request<FunctionMapAsset>(`/api/v1/function-map-assets/${id}`)
    },
    async createAsset(input: FunctionMapAssetInput): Promise<FunctionMapAsset> {
      const created = await request<FunctionMapAsset>('/api/v1/function-map-assets', {
        method: 'POST',
        body: input as unknown as BodyInit,
      })
      await this.loadAssets()
      return created
    },
    async updateMeta(id: number, meta: FunctionMapAssetMetaInput): Promise<FunctionMapAsset> {
      const updated = await request<FunctionMapAsset>(`/api/v1/function-map-assets/${id}`, {
        method: 'PATCH',
        body: meta as unknown as BodyInit,
      })
      await this.loadAssets()
      return updated
    },
    async overwriteContent(id: number, body: FunctionMapAssetContentInput): Promise<FunctionMapAsset> {
      const updated = await request<FunctionMapAsset>(`/api/v1/function-map-assets/${id}/content`, {
        method: 'PUT',
        body: body as unknown as BodyInit,
      })
      await this.loadAssets()
      return updated
    },
    async deleteAsset(id: number): Promise<void> {
      await request(`/api/v1/function-map-assets/${id}`, { method: 'DELETE' })
      await this.loadAssets()
    },
    async exportAsset(id: number): Promise<FunctionMapAssetExport> {
      return request<FunctionMapAssetExport>(`/api/v1/function-map-assets/${id}/export`)
    },
    async searchAssets(keyword: string, page: number, pageSize: number): Promise<FunctionMapAssetPage> {
      const params = new URLSearchParams()
      if (keyword.trim()) {
        params.set('keyword', keyword.trim())
      }
      params.set('page', String(page))
      params.set('page_size', String(pageSize))
      return request<FunctionMapAssetPage>(`/api/v1/function-map-assets?${params.toString()}`)
    },
    async loadMountTargets(
      keyword: string,
      page: number,
      pageSize: number,
      focus?: { groupId?: number; itemId?: number },
    ): Promise<RequirementCatalog> {
      const params = new URLSearchParams()
      if (keyword.trim()) {
        params.set('keyword', keyword.trim())
      }
      params.set('page', String(page))
      params.set('page_size', String(pageSize))
      if (focus?.groupId) {
        params.set('focus_group_id', String(focus.groupId))
      }
      if (focus?.itemId) {
        params.set('focus_item_id', String(focus.itemId))
      }
      return request<RequirementCatalog>(`/api/v1/function-map-mount-targets?${params.toString()}`)
    },
    async listGroupMounts(groupId: number): Promise<FunctionMapAssetListItem[]> {
      return request<FunctionMapAssetListItem[]>(
        `/api/v1/requirement-groups/${groupId}/function-map-mounts`,
      )
    },
    async mountToGroup(groupId: number, assetId: number): Promise<FunctionMapAssetListItem[]> {
      return request<FunctionMapAssetListItem[]>(
        `/api/v1/requirement-groups/${groupId}/function-map-mounts`,
        { method: 'POST', body: { assetId } as unknown as BodyInit },
      )
    },
    async unmountFromGroup(groupId: number, assetId: number): Promise<FunctionMapAssetListItem[]> {
      return request<FunctionMapAssetListItem[]>(
        `/api/v1/requirement-groups/${groupId}/function-map-mounts/${assetId}`,
        { method: 'DELETE' },
      )
    },
    async listItemMounts(itemId: number): Promise<FunctionMapAssetListItem[]> {
      return request<FunctionMapAssetListItem[]>(
        `/api/v1/requirement-items/${itemId}/function-map-mounts`,
      )
    },
    async mountToItem(itemId: number, assetId: number): Promise<FunctionMapAssetListItem[]> {
      return request<FunctionMapAssetListItem[]>(
        `/api/v1/requirement-items/${itemId}/function-map-mounts`,
        { method: 'POST', body: { assetId } as unknown as BodyInit },
      )
    },
    async unmountFromItem(itemId: number, assetId: number): Promise<FunctionMapAssetListItem[]> {
      return request<FunctionMapAssetListItem[]>(
        `/api/v1/requirement-items/${itemId}/function-map-mounts/${assetId}`,
        { method: 'DELETE' },
      )
    },
    async listQuickMounts(quickSessionId: string): Promise<FunctionMapAssetListItem[]> {
      return request<FunctionMapAssetListItem[]>(
        `/api/v1/quick-sessions/${quickSessionId}/function-map-mounts`,
      )
    },
    async mountToQuick(quickSessionId: string, assetId: number): Promise<FunctionMapAssetListItem[]> {
      return request<FunctionMapAssetListItem[]>(
        `/api/v1/quick-sessions/${quickSessionId}/function-map-mounts`,
        { method: 'POST', body: { assetId } as unknown as BodyInit },
      )
    },
    async unmountFromQuick(quickSessionId: string, assetId: number): Promise<FunctionMapAssetListItem[]> {
      return request<FunctionMapAssetListItem[]>(
        `/api/v1/quick-sessions/${quickSessionId}/function-map-mounts/${assetId}`,
        { method: 'DELETE' },
      )
    },
  },
})
