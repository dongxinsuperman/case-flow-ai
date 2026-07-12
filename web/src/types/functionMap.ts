export type FunctionMapTarget = 'app' | 'web' | 'api'

export interface FunctionMapAssetListItem {
  id: number
  title: string
  description: string
  targets: FunctionMapTarget[]
  updatedAt: string
  referenceCount: number
}

export interface FunctionMapMountRef {
  scope: 'group' | 'item'
  id: number
  name: string
}

export interface FunctionMapAsset {
  id: number
  title: string
  description: string
  content: string
  targets: FunctionMapTarget[]
  sourceType: string
  sourceFilename: string | null
  createdAt: string
  updatedAt: string
  referenceCount: number
  mounts: FunctionMapMountRef[]
}

export interface FunctionMapAssetPage {
  items: FunctionMapAssetListItem[]
  total: number
  page: number
  pageSize: number
}

export interface FunctionMapAssetInput {
  title: string
  description: string
  content: string
  targets: FunctionMapTarget[]
  sourceFilename?: string | null
}

export interface FunctionMapAssetMetaInput {
  title: string
  description: string
  targets: FunctionMapTarget[]
}

export interface FunctionMapAssetContentInput {
  content: string
  sourceFilename?: string | null
}

export interface FunctionMapAssetExport {
  title: string
  description: string
  content: string
  targets: FunctionMapTarget[]
}

export interface MountTargetItem {
  id: number
  title: string
  version?: string | null
}

export interface MountTargetGroup {
  id: number
  name: string
  items: MountTargetItem[]
}

export type MountScope = 'group' | 'item'

export interface RequirementCatalog {
  groups: MountTargetGroup[]
  ungroupedItems: MountTargetItem[]
  total: number
  page: number
  pageSize: number
}
