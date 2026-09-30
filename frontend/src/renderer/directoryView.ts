import type { DirectoryEntry } from '../shared/directory'

export type ViewMode = 'thumbnails' | 'list' | 'details'
export type SortKey = 'name' | 'modifiedAt' | 'type' | 'size'
export type BrowserPreferences = { view: ViewMode; sort: SortKey; descending: boolean; favorites: string[] }
export const browserPreferenceKey = 'vm-directory-browser'
export function readBrowserPreferences(): BrowserPreferences {
  let saved: any = {}
  try { saved = JSON.parse(localStorage.getItem(browserPreferenceKey) || '{}') || {} } catch { /* Keep browsing usable when storage is unavailable. */ }
  return {
    view: ['thumbnails', 'list', 'details'].includes(saved.view) ? saved.view : 'thumbnails',
    sort: ['name', 'modifiedAt', 'type', 'size'].includes(saved.sort) ? saved.sort : 'name',
    descending: saved.descending === true,
    favorites: Array.isArray(saved.favorites) ? [...new Set<string>(saved.favorites.filter((p: unknown) => typeof p === 'string' && p.trim()))].slice(0, 30) : [],
  }
}
export function writeBrowserPreferences(value: BrowserPreferences) {
  try { localStorage.setItem(browserPreferenceKey, JSON.stringify(value)) } catch { /* Session settings still work. */ }
}
export function entryType(entry: DirectoryEntry) {
  if (entry.directory) return '文件夹'
  const extension = entry.name.includes('.') ? entry.name.split('.').pop() : ''
  return extension ? `${extension.toUpperCase()} 文件` : '文件'
}
export function sortDirectoryEntries(entries: DirectoryEntry[], key: SortKey, descending: boolean) {
  const compareName = (a: DirectoryEntry, b: DirectoryEntry) => a.name.localeCompare(b.name, 'zh-CN', { numeric: true, sensitivity: 'base' })
  return [...entries].sort((a, b) => {
    if (a.directory !== b.directory) return a.directory ? -1 : 1
    let compared = 0
    if (key === 'modifiedAt' || key === 'size') {
      const av = a[key], bv = b[key]
      if (av == null || bv == null) {
        if (av == null && bv != null) return 1
        if (av != null && bv == null) return -1
      } else compared = av - bv
    } else if (key === 'type') compared = entryType(a).localeCompare(entryType(b), 'zh-CN')
    else compared = compareName(a, b)
    return (compared || compareName(a, b)) * (descending ? -1 : 1)
  })
}
export function formatFileSize(size: number | null) {
  if (size == null) return '—'
  if (size < 1024) return `${size} B`
  const power = Math.min(Math.floor(Math.log(size) / Math.log(1024)), 4)
  return `${(size / 1024 ** power).toFixed(1)} ${['B', 'KB', 'MB', 'GB', 'TB'][power]}`
}
export function formatModifiedAt(time: number | null) {
  return time == null ? '—' : new Date(time).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}
export function favoriteName(directory: string) {
  return directory.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || directory
}
