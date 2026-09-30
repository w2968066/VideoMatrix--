export interface DirectoryEntry {
  name: string
  path: string
  directory: boolean
  modifiedAt: number | null
  size: number | null
}

export interface DirectoryListing {
  path: string
  parent: string | null
  home: string
  roots: string[]
  entries: DirectoryEntry[]
}

export type DirectoryAction = 'open' | 'reveal' | 'copy' | 'rename' | 'trash'
export type TrashResult = { cancelled: boolean; removed: string[]; failed: { path: string; message: string }[] }
