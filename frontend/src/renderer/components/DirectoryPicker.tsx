import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { Checkbox } from './ui/checkbox'
import { DirectoryThumbnail } from './DirectoryThumbnail'
import { entryType, favoriteName, formatFileSize, formatModifiedAt, readBrowserPreferences, sortDirectoryEntries, writeBrowserPreferences, type SortKey, type ViewMode } from '../directoryView'
import { useStore } from '../store'
import { remapConfiguredPaths, remapPath, type PathChange } from '../directoryPathUpdates'
import type { DirectoryAction } from '../../shared/directory'

type Listing = Awaited<ReturnType<Window['electronAPI']['readDirectory']>>
type Result = string | string[] | null
type PickerRequest = { start?: string; multi: boolean }

export function useDirectoryPicker() {
  const [request, setRequest] = useState<PickerRequest | null>(null)
  const resolve = useRef<((value: Result) => void) | null>(null)
  useEffect(() => () => { resolve.current?.(null) }, [])
  const openDirectory = (start?: string, multi = false): Promise<Result> => {
    resolve.current?.(null)
    return new Promise(done => { resolve.current = done; setRequest({ start, multi }) })
  }
  const finish = (result: Result) => {
    resolve.current?.(result)
    resolve.current = null
    setRequest(null)
  }
  return { openDirectory, directoryPicker: request && <DirectoryPicker key={`${request.start}-${request.multi}`} request={request} finish={finish} /> }
}

function DirectoryPicker({ request, finish }: { request: PickerRequest; finish: (result: Result) => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const contents = useRef<HTMLDivElement>(null)
  const opener = useRef<HTMLElement | null>(document.activeElement as HTMLElement)
  const requestId = useRef(0)
  const [listing, setListing] = useState<Listing | null>(null)
  const [pathInput, setPathInput] = useState(request.start || '')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('')
  const [candidate, setCandidate] = useState<string | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [managed, setManaged] = useState<string[]>([])
  const anchor = useRef<string | null>(null)
  const history = useRef({ paths: [] as string[], index: -1 })
  const [historyIndex, setHistoryIndex] = useState(-1)
  const [operationBusy, setOperationBusy] = useState(false)
  const [operationError, setOperationError] = useState('')
  const [rename, setRename] = useState<{ path: string; name: string; extension: string } | null>(null)
  const renameInput = useRef<HTMLInputElement>(null)
  const tasksRunning = useStore(state => state.tasks.some(task => task.status === 'pending' || task.status === 'running'))
  const notify = useStore(state => state.addToast)
  useEffect(() => { if (rename) { renameInput.current?.focus(); renameInput.current?.select() } }, [rename?.path])
  const [preferences, setPreferences] = useState(readBrowserPreferences)
  const [newFolderName, setNewFolderName] = useState<string | null>(null)
  const [newFolderError, setNewFolderError] = useState('')
  const newFolderInput = useRef<HTMLInputElement>(null)
  useEffect(() => { if (newFolderName !== null) { newFolderInput.current?.focus(); newFolderInput.current?.select() } }, [newFolderName !== null])
  useEffect(() => { writeBrowserPreferences(preferences) }, [preferences])

  const navigate = async (directory?: string, historyTarget?: number | 'refresh') => {
    if (operationBusy) return
    const id = ++requestId.current
    setLoading(true); setError(''); setCandidate(null); setFilter('')
    setNewFolderName(null); setNewFolderError('')
    setRename(null); setManaged([]); anchor.current = null; setOperationError('')
    try {
      if (!window.electronAPI?.readDirectory) throw new Error('目录浏览器需要新版桌面程序，请重新打开开发版。')
      const result = await window.electronAPI.readDirectory(directory)
      if (id !== requestId.current) return
      setListing(result); setPathInput(result.path)
      if (typeof historyTarget === 'number') history.current.index = historyTarget
      else if (history.current.paths[history.current.index] !== result.path) {
        history.current.paths = [...history.current.paths.slice(0, history.current.index + 1), result.path]
        history.current.index = history.current.paths.length - 1
      }
      setHistoryIndex(history.current.index)
    } catch (e: any) {
      if (id === requestId.current) setError(String(e.message || e).replace(/^Error invoking remote method '[^']+': Error: /, ''))
    } finally { if (id === requestId.current) setLoading(false) }
  }
  useEffect(() => {
    dialog.current?.showModal()
    void navigate(request.start)
    return () => { requestId.current += 1; opener.current?.focus() }
  }, [])
  useEffect(() => { if (contents.current) contents.current.scrollTop = 0 }, [listing?.path, preferences.view, preferences.sort, preferences.descending, filter])

  function toggle(directory: string) {
    setSelected(current => current.includes(directory) ? current.filter(p => p !== directory) : [...current, directory])
  }
  function confirm() {
    if (!listing || loading || error) return
    finish(request.multi ? (selected.length ? selected : [listing.path]) : candidate || listing.path)
  }
  const rows = sortDirectoryEntries(listing?.entries.filter(entry => entry.name.toLocaleLowerCase().includes(filter.toLocaleLowerCase())) || [], preferences.sort, preferences.descending)
  const folderCount = listing?.entries.filter(entry => entry.directory).length || 0
  const addressChanged = !!listing && pathInput.trim() !== listing.path
  const busy = loading || operationBusy
  useEffect(() => {
    // Disabled navigation controls can drop focus onto body. Keep shortcuts in the modal.
    if (!busy && document.activeElement === document.body) dialog.current?.focus()
  }, [busy, listing?.path])
  const goHistory = (delta: number) => {
    const next = history.current.index + delta
    if (next >= 0 && next < history.current.paths.length) void navigate(history.current.paths[next], next)
  }
  function syncPaths(changes: PathChange[]) {
    const state = useStore.getState()
    const patch = remapConfiguredPaths(state.config, changes)
    if (Object.keys(patch).length) state.setConfig(patch)
    setSelected(current => current.map(p => remapPath(p, changes)).filter(Boolean))
    setCandidate(current => current ? remapPath(current, changes) || null : null)
    setPreferences(current => ({ ...current, favorites: [...new Set(current.favorites.map(p => remapPath(p, changes)).filter(Boolean))] }))
    history.current.paths = history.current.paths.map(p => remapPath(p, changes)).filter(Boolean)
    history.current.index = listing ? history.current.paths.indexOf(listing.path) : -1
    setHistoryIndex(history.current.index)
  }
  function selectManaged(entry: Listing['entries'][number], event: ReactMouseEvent) {
    if (busy) return
    const add = event.ctrlKey || event.metaKey
    if (event.shiftKey && anchor.current) {
      const a = rows.findIndex(row => row.path === anchor.current), b = rows.findIndex(row => row.path === entry.path)
      const range = a >= 0 && b >= 0 ? rows.slice(Math.min(a, b), Math.max(a, b) + 1).map(row => row.path) : [entry.path]
      setManaged(current => add ? [...new Set([...current, ...range])] : range)
    } else if (add) setManaged(current => current.includes(entry.path) ? current.filter(p => p !== entry.path) : [...current, entry.path])
    else setManaged([entry.path])
    if (!event.shiftKey) anchor.current = entry.path
    if (!request.multi && !add && !event.shiftKey) setCandidate(entry.directory ? entry.path : null)
    setOperationError('')
  }
  async function runAction(action: DirectoryAction, paths = managed) {
    if (!listing || busy || error || addressChanged || !paths.length) return
    const entries = paths.map(p => listing.entries.find(entry => entry.path === p)).filter((entry): entry is Listing['entries'][number] => !!entry)
    if (entries.length !== paths.length) { setOperationError('选中的项目已变化，请刷新后重试。'); return }
    if (action === 'rename') {
      if (tasksRunning) { setOperationError('混剪任务进行中，暂禁删除和改名。'); return }
      if (entries.length !== 1) return
      const entry = entries[0], dot = entry.directory ? -1 : entry.name.lastIndexOf('.')
      setNewFolderName(null); setOperationError('')
      setRename({ path: entry.path, name: dot > 0 ? entry.name.slice(0, dot) : entry.name, extension: dot > 0 ? entry.name.slice(dot) : '' })
      return
    }
    if (action === 'open' && entries.length === 1 && entries[0].directory) { void navigate(entries[0].path); return }
    const id = requestId.current
    setOperationBusy(true); setOperationError('')
    try {
      if (action === 'trash') {
        if (tasksRunning) throw new Error('混剪任务进行中，暂禁删除和改名。')
        const result = await window.electronAPI.trashEntries(listing.path, paths)
        if (id !== requestId.current || result.cancelled) return
        if (result.removed.length) {
          syncPaths(result.removed.map(from => ({ from, to: null })))
          setManaged(current => current.filter(p => !result.removed.includes(p)))
          const refreshed = await window.electronAPI.readDirectory(listing.path)
          if (id !== requestId.current) return
          setListing(refreshed)
          notify(`已将 ${result.removed.length} 项移入系统回收站，可在系统回收站恢复。相关素材引用已移除。`, 'success')
        }
        if (result.failed.length) setOperationError(`${result.failed.length} 项未删除：${result.failed.map(item => `${favoriteName(item.path)}（${item.message}）`).join('；')}`)
      } else if (action === 'copy') { await window.electronAPI.copyEntryPaths(listing.path, paths); if (id === requestId.current) notify(`已复制 ${paths.length} 项完整路径`, 'success') }
      else if (action === 'reveal' && paths.length === 1) await window.electronAPI.revealEntry(listing.path, paths[0])
      else if (action === 'open' && paths.length === 1) await window.electronAPI.openEntry(listing.path, paths[0])
    } catch (e: any) { if (id === requestId.current) setOperationError(String(e.message || e).replace(/^Error invoking remote method '[^']+': Error: /, '')) }
    finally { if (id === requestId.current) setOperationBusy(false) }
  }
  async function openMenu(paths = managed) {
    if (!listing || busy || error || addressChanged || !paths.length) return
    const id = requestId.current
    try {
      const action = await window.electronAPI.directoryActionMenu(listing.path, paths)
      if (id === requestId.current && action) await runAction(action, paths)
    } catch (e: any) { if (id === requestId.current) setOperationError(String(e.message || e)) }
  }
  function contextMenu(event: ReactMouseEvent, entry: Listing['entries'][number]) {
    event.preventDefault()
    if (busy) return
    const paths = managed.includes(entry.path) ? managed : [entry.path]
    setManaged(paths); anchor.current = entry.path
    void openMenu(paths)
  }
  async function saveRename() {
    if (!listing || !rename || !rename.name.trim() || busy || tasksRunning || error || addressChanged) return
    const id = requestId.current
    setOperationBusy(true); setOperationError('')
    try {
      const next = await window.electronAPI.renameEntry(listing.path, rename.path, rename.name)
      if (id !== requestId.current) return
      syncPaths([{ from: rename.path, to: next }]); setManaged([next]); setRename(null)
      const refreshed = await window.electronAPI.readDirectory(listing.path)
      if (id === requestId.current) { setListing(refreshed); notify('已重命名，软件内相关路径已同步更新。', 'success') }
    } catch (e: any) { if (id === requestId.current) setOperationError(String(e.message || e).replace(/^Error invoking remote method '[^']+': Error: /, '')) }
    finally { if (id === requestId.current) setOperationBusy(false) }
  }
  async function createFolder() {
    if (!listing || newFolderName === null || !newFolderName.trim() || busy || error || addressChanged) return
    const id = ++requestId.current
    setLoading(true); setNewFolderError('')
    let created: string | null = null
    try {
      if (!window.electronAPI?.createDirectory) throw new Error('请重新打开新版桌面程序后再新建文件夹。')
      created = await window.electronAPI.createDirectory(listing.path, newFolderName)
      if (id !== requestId.current) return
      setNewFolderName(null); setFilter('')
      const result = await window.electronAPI.readDirectory(listing.path)
      if (id !== requestId.current) return
      setListing(result); setCandidate(created); setManaged([created])
      if (request.multi) setSelected(current => current.includes(created!) ? current : [...current, created!])
      requestAnimationFrame(() => {
        const row = Array.from(contents.current?.querySelectorAll<HTMLElement>('[data-entry-path]') || []).find(el => el.dataset.entryPath === created)
        row?.scrollIntoView({ block: 'nearest' }); row?.querySelector<HTMLButtonElement>('.directory-entry-main')?.focus()
      })
    } catch (e: any) {
      if (id !== requestId.current) return
      const message = String(e.message || e).replace(/^Error invoking remote method '[^']+': Error: /, '')
      if (created) setError(`文件夹已创建，但刷新失败：${message} 请点击刷新。`)
      else setNewFolderError(message)
    } finally { if (id === requestId.current) setLoading(false) }
  }
  const buttonClass = 'rounded border border-border/20 px-2.5 py-1.5 text-[12px] text-foreground hover:border-accent focus-visible:outline focus-visible:outline-accent disabled:opacity-40'
  const sortColumns: { key: SortKey; label: string }[] = [{ key: 'name', label: '名称' }, { key: 'modifiedAt', label: '修改时间' }, { key: 'type', label: '类型' }, { key: 'size', label: '大小' }]
  const chooseSort = (sort: SortKey) => setPreferences(current => ({ ...current, sort, descending: current.sort === sort ? !current.descending : sort === 'modifiedAt' }))
  const isFavorite = !!listing && preferences.favorites.includes(listing.path)
  function toggleFavorite() {
    if (!listing || loading || error || addressChanged) return
    setPreferences(current => ({ ...current, favorites: current.favorites.includes(listing.path)
      ? current.favorites.filter(p => p !== listing.path) : [...current.favorites, listing.path].slice(0, 30) }))
  }
  const entryName = (entry: Listing['entries'][number]) => {
    const inner = <><DirectoryThumbnail file={entry.path} name={entry.name} directory={entry.directory} compact={preferences.view !== 'thumbnails'} />
      <span className="directory-entry-name" title={entry.name}>{entry.name}</span></>
    return <button type="button" className="directory-entry-main rounded focus-visible:outline focus-visible:outline-accent"
      aria-label={entry.name} title={entry.path} aria-pressed={managed.includes(entry.path)} disabled={busy}
      onClick={e => { if (e.detail < 2) selectManaged(entry, e) }} onDoubleClick={() => void runAction('open', [entry.path])}>{inner}</button>
  }
  const entryCheckbox = (entry: Listing['entries'][number]) => request.multi && entry.directory && <Checkbox aria-label={`选择文件夹 ${entry.name}`}
    checked={selected.includes(entry.path)} disabled={busy} onCheckedChange={() => toggle(entry.path)} />
  const enterButton = (entry: Listing['entries'][number]) => entry.directory && <button type="button" aria-label={`查看文件夹 ${entry.name}`}
    className="rounded px-2 py-1 text-[11px] text-accent hover:bg-accent/10 focus-visible:outline focus-visible:outline-accent" disabled={busy} onClick={() => void navigate(entry.path)}>进入</button>

  return createPortal(<dialog ref={dialog} tabIndex={-1} className="directory-picker" aria-labelledby="directory-picker-title"
    onKeyDown={e => {
      const editing = !!(e.target as HTMLElement).closest('input, textarea, select, [contenteditable="true"]')
      if (busy) return
      if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) { e.preventDefault(); goHistory(e.key === 'ArrowLeft' ? -1 : 1); return }
      if (e.key === 'F5') { e.preventDefault(); void navigate(listing?.path, 'refresh'); return }
      if (editing || rename || newFolderName !== null) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); if (rows.length > 500) setOperationError('批量管理最多 500 项，请先筛选或分批选择。'); else setManaged(rows.map(row => row.path)); return }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'c' && managed.length) { e.preventDefault(); void runAction('copy'); return }
      if (e.key === 'F2' && managed.length === 1) { e.preventDefault(); void runAction('rename'); return }
      if (e.key === 'Delete' && managed.length) { e.preventDefault(); void runAction('trash'); return }
      if ((e.shiftKey && e.key === 'F10') || e.key === 'ContextMenu') { e.preventDefault(); void openMenu(); return }
    }}
    onCancel={e => {
      e.preventDefault()
      if (busy) return
      if (rename) { setRename(null); setOperationError('') }
      else if (newFolderName !== null) { setNewFolderName(null); setNewFolderError('') }
      else finish(null)
    }}>
    <div className="flex items-center justify-between gap-3">
      <h2 id="directory-picker-title" className="text-sm font-semibold">选择文件夹</h2>
      <button type="button" disabled={busy} className={buttonClass} onClick={() => finish(null)}>取消</button>
    </div>
    <p className="mt-2 text-[12px] text-muted-foreground">双击打开，右键管理，Ctrl / Shift 多选项目。{request.multi ? '素材目录只认勾选，文件管理选中不会加入素材。' : '素材仅选择文件夹。'} 缩略图由系统提供。</p>
    <form className="mt-3 flex gap-2" onSubmit={e => { e.preventDefault(); void navigate(pathInput) }}>
      <button type="button" className={buttonClass} disabled={busy || historyIndex <= 0} title="Alt + ←" onClick={() => goHistory(-1)}>后退</button>
      <button type="button" className={buttonClass} disabled={busy || historyIndex >= history.current.paths.length - 1} title="Alt + →" onClick={() => goHistory(1)}>前进</button>
      <button type="button" className={buttonClass} disabled={!listing?.parent || busy} onClick={() => void navigate(listing!.parent!)}>上一级</button>
      <input aria-label="文件夹地址" disabled={busy} placeholder="输入路径后按回车" value={pathInput} onChange={e => { setPathInput(e.target.value); setManaged([]) }}
        className="h-8 min-w-0 flex-1 rounded border border-border/20 bg-background-elev px-2 font-mono text-[12px] text-foreground outline-none focus:border-accent" />
      <button type="submit" className={buttonClass} disabled={busy}>前往</button>
      <button type="button" className={buttonClass} disabled={busy} title="F5" onClick={() => void navigate(listing?.path, 'refresh')}>刷新</button>
    </form>
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <button type="button" className={buttonClass} disabled={busy} onClick={() => void navigate(listing?.home)}>主目录</button>
      {listing?.roots.map(root => <button key={root} type="button" className={buttonClass} disabled={busy} onClick={() => void navigate(root)}>{root}</button>)}
      <button type="button" className={`${buttonClass} text-accent`} disabled={!listing || busy || !!error || addressChanged || (!isFavorite && preferences.favorites.length >= 30)} onClick={toggleFavorite}>
        {isFavorite ? '取消收藏' : '收藏当前目录'}</button>
      <input aria-label="筛选文件夹内容" disabled={busy} placeholder="筛选名称" value={filter} onChange={e => { setFilter(e.target.value); setManaged([]); anchor.current = null }}
        className="ml-auto h-8 rounded border border-border/20 bg-background-elev px-2 text-[12px] text-foreground outline-none focus:border-accent" />
    </div>
    {preferences.favorites.length > 0 && <div className="directory-favorites mt-2 flex flex-wrap items-center gap-2" aria-label="常用文件夹">
      <span className="text-[11px] text-muted-foreground">收藏</span>
      {preferences.favorites.map(directory => <span key={directory} className="inline-flex max-w-full items-center rounded border border-accent/25">
        <button type="button" title={directory} aria-label={`打开收藏 ${directory}`} disabled={busy} className="max-w-40 truncate px-2 py-1.5 text-[12px] text-accent focus-visible:outline focus-visible:outline-accent" onClick={() => void navigate(directory)}>{favoriteName(directory)}</button>
        <button type="button" title="取消收藏" aria-label={`取消收藏 ${directory}`} className="px-2 py-1.5 text-[12px] text-muted-foreground hover:text-hot focus-visible:outline focus-visible:outline-accent"
          onClick={() => setPreferences(current => ({ ...current, favorites: current.favorites.filter(p => p !== directory) }))}>×</button>
      </span>)}
    </div>}
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <div className="flex gap-1" role="group" aria-label="显示方式">
        {([{ value: 'thumbnails', label: '缩略图' }, { value: 'list', label: '列表' }, { value: 'details', label: '详细信息' }] as { value: ViewMode; label: string }[]).map(option =>
          <button key={option.value} type="button" disabled={busy} aria-pressed={preferences.view === option.value} className={`${buttonClass} ${preferences.view === option.value ? 'border-accent bg-accent/10 text-accent' : ''}`}
            onClick={() => setPreferences(current => ({ ...current, view: option.value }))}>{option.label}</button>)}
      </div>
      <button type="button" className={buttonClass} disabled={!listing || busy || !!error || addressChanged} onClick={() => { setRename(null); setOperationError(''); setNewFolderName('新建文件夹'); setNewFolderError('') }}>新建文件夹</button>
      <label className="ml-auto flex items-center gap-2 text-[12px] text-muted-foreground">排序
        <select aria-label="排序方式" value={preferences.sort} className="rounded border border-border/20 bg-background-elev px-2 py-1.5 text-foreground focus-visible:outline focus-visible:outline-accent"
          onChange={e => setPreferences(current => ({ ...current, sort: e.target.value as SortKey, descending: e.target.value === 'modifiedAt' }))}>
          {sortColumns.map(column => <option key={column.key} value={column.key}>{column.label}</option>)}
        </select></label>
      <button type="button" className={buttonClass} aria-label="切换排序方向" title={preferences.sort === 'modifiedAt' ? preferences.descending ? '最新在前' : '最早在前' : undefined}
        onClick={() => setPreferences(current => ({ ...current, descending: !current.descending }))}>{preferences.descending ? '降序 ↓' : '升序 ↑'}</button>
    </div>
    <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px]" aria-label="文件管理操作">
      <span className="min-w-24 text-muted-foreground">管理选中 {managed.length} 项</span>
      <button type="button" className={buttonClass} disabled={busy || addressChanged || !managed.length} onClick={() => void openMenu()}>操作</button>
      <button type="button" className={buttonClass} title="F2" disabled={busy || tasksRunning || managed.length !== 1 || addressChanged} onClick={() => void runAction('rename')}>重命名</button>
      <button type="button" className={`${buttonClass} text-hot`} title="Delete；始终移入回收站" disabled={busy || tasksRunning || addressChanged || !managed.length} onClick={() => void runAction('trash')}>移入回收站</button>
      <button type="button" className={buttonClass} disabled={busy || !managed.length} onClick={() => setManaged([])}>清除管理选中</button>
      {tasksRunning && <span className="text-[11px] text-hot">混剪进行中，暂禁删除和改名</span>}
    </div>
    {rename && <form className="mt-2 rounded border border-accent/25 bg-accent/5 p-2" onSubmit={e => { e.preventDefault(); void saveRename() }}>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="directory-rename" className="text-[12px]">新名称</label>
        <input ref={renameInput} id="directory-rename" value={rename.name} maxLength={255} disabled={busy || tasksRunning} onChange={e => { setRename(current => current && { ...current, name: e.target.value }); setOperationError('') }}
          className="h-8 min-w-0 flex-1 rounded border border-border/20 bg-background-elev px-2 text-[12px] text-foreground outline-none focus:border-accent" />
        {rename.extension && <span className="text-[11px] text-muted-foreground">保留扩展名 {rename.extension}</span>}
        <button type="submit" disabled={busy || tasksRunning || !rename.name.trim() || addressChanged} className={buttonClass}>{operationBusy ? '正在改名…' : '保存名称'}</button>
        <button type="button" disabled={busy} className={buttonClass} onClick={() => { setRename(null); setOperationError('') }}>取消改名</button>
      </div>
    </form>}
    {operationError && <p role="alert" className="mt-2 text-[12px] text-hot">{operationError}</p>}
    {newFolderName !== null && <form className="mt-2 rounded border border-accent/25 bg-accent/5 p-2" onSubmit={e => { e.preventDefault(); void createFolder() }}>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="directory-new-folder" className="text-[12px] text-foreground">文件夹名称</label>
        <input ref={newFolderInput} id="directory-new-folder" value={newFolderName} disabled={loading} maxLength={255}
          aria-describedby={newFolderError ? 'directory-new-folder-error' : undefined} aria-invalid={!!newFolderError}
          onChange={e => { setNewFolderName(e.target.value); setNewFolderError('') }}
          className="h-8 min-w-0 flex-1 rounded border border-border/20 bg-background-elev px-2 text-[12px] text-foreground outline-none focus:border-accent" />
        <button type="submit" className={buttonClass} disabled={loading || !newFolderName.trim() || addressChanged}>{loading ? '正在创建…' : '创建'}</button>
        <button type="button" className={buttonClass} disabled={loading} onClick={() => { setNewFolderName(null); setNewFolderError('') }}>取消新建</button>
      </div>
      {newFolderError && <p id="directory-new-folder-error" role="alert" className="mt-2 text-[12px] text-hot">{newFolderError}</p>}
    </form>}
    {error && <p role="alert" className="mt-2 text-[12px] text-hot">{error}</p>}
    <div ref={contents} className={`directory-picker-list directory-view-${preferences.view} mt-3`} aria-label="文件夹内容" aria-busy={loading}>
      {loading ? <p role="status" className="p-4 text-muted-foreground">正在读取文件夹…</p> : !error && !rows.length ? <p className="p-4 text-muted-foreground">{filter ? '没有匹配的文件或文件夹' : '空文件夹'}</p> : !error && preferences.view === 'details' ?
        <table className="directory-details" aria-label="文件夹详细信息">
          <thead><tr>{sortColumns.map(column => <th key={column.key} scope="col" aria-sort={preferences.sort === column.key ? preferences.descending ? 'descending' : 'ascending' : 'none'}>
            <button type="button" aria-label={`按${column.label}排序`} onClick={() => chooseSort(column.key)} className="w-full py-2 text-left focus-visible:outline focus-visible:outline-accent">
              {column.label}{preferences.sort === column.key ? preferences.descending ? ' ↓' : ' ↑' : ''}</button></th>)}<th scope="col"><span className="sr-only">操作</span></th></tr></thead>
          <tbody>{rows.map(entry => <tr key={entry.path} data-entry-path={entry.path} onContextMenu={e => contextMenu(e, entry)}
            onClick={e => { if (!(e.target as HTMLElement).closest('button, [role="checkbox"]')) selectManaged(entry, e) }} className={managed.includes(entry.path) ? 'directory-entry-selected' : ''}>
            <td><div className="flex min-w-0 items-center gap-2">{entryCheckbox(entry)}{entryName(entry)}</div></td>
            <td>{formatModifiedAt(entry.modifiedAt)}</td><td>{entryType(entry)}</td><td className="text-right">{formatFileSize(entry.size)}</td><td>{enterButton(entry)}</td>
          </tr>)}</tbody>
        </table> : !error && rows.map(entry =>
        <div key={entry.path} data-entry-path={entry.path} onContextMenu={e => contextMenu(e, entry)}
          onClick={e => { if (!(e.target as HTMLElement).closest('button, [role="checkbox"]')) selectManaged(entry, e) }} className={`directory-entry ${managed.includes(entry.path) ? 'directory-entry-selected' : ''}`}>
          {request.multi && entry.directory && <span className="directory-entry-check">{entryCheckbox(entry)}</span>}
          {entryName(entry)}
          <div className="directory-entry-meta text-[10px] text-muted-foreground">
            <span>{entryType(entry)}</span>{enterButton(entry)}
          </div>
        </div>)}
    </div>
    <div className="mt-2 flex items-center gap-2 text-[11px] text-muted-foreground">
      <span>{folderCount} 个文件夹 · {(listing?.entries.length || 0) - folderCount} 个文件</span>
      {request.multi && <button type="button" className={`${buttonClass} ml-auto`} disabled={!listing || busy || !!error} onClick={() => toggle(listing!.path)}>
        {selected.includes(listing?.path || '') ? '移除当前目录' : '加入当前目录'}</button>}
    </div>
    {request.multi && selected.length > 0 && <div className="mt-2 max-h-20 overflow-y-auto text-[11px] text-foreground" aria-label="已选文件夹">
      {selected.map(directory => <div key={directory} className="flex items-center gap-2 py-1"><span className="min-w-0 flex-1 truncate" title={directory}>{directory}</span>
        <button type="button" className="text-accent" aria-label={`移除已选目录 ${directory}`} onClick={() => toggle(directory)}>移除</button></div>)}
    </div>}
    <div className="mt-3 flex items-center justify-between gap-3 border-t border-border/15 pt-3">
      <span className="min-w-0 truncate font-mono text-[11px] text-muted-foreground" title={candidate || listing?.path}>{request.multi && selected.length ? `已选 ${selected.length} 个文件夹` : candidate || listing?.path}</span>
      <button type="button" disabled={!listing || busy || !!error || addressChanged || rename !== null || newFolderName !== null} onClick={confirm}
        className="shrink-0 rounded bg-accent px-4 py-2 text-[12px] font-semibold text-background hover:bg-accent-hover focus-visible:outline focus-visible:outline-accent disabled:opacity-40">
        {request.multi && selected.length ? `确认 ${selected.length} 个文件夹` : candidate && !request.multi ? '选择此文件夹' : '选择当前文件夹'}</button>
    </div>
  </dialog>, document.body)
}
