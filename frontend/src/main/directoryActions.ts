import fs from 'fs'
import path from 'path'
import { validateEntryName } from './directoryBrowser'
import type { TrashResult } from '../shared/directory'
import { renameWithoutReplace } from './renameWithoutReplace'

type Options = {
  assertIdle: () => Promise<void>
  confirmTrash: (paths: string[], folders: number) => Promise<boolean>
  trashItem: (file: string) => Promise<void>
  protectedPaths: string[]
}

export function createDirectoryActions(options: Options) {
  let mutating = false
  const key = (file: string) => process.platform === 'win32' ? path.resolve(file).toLowerCase() : path.resolve(file)
  async function targets(parent: string, items: string[], forMutation = false) {
    if (typeof parent !== 'string' || !path.isAbsolute(parent) || !Array.isArray(items) || !items.length || items.length > 500) throw new Error('请选择当前目录中的项目（最多 500 项）。')
    const resolvedParent = path.resolve(parent)
    const realParent = await fs.promises.realpath(resolvedParent)
    const unique = [...new Map(items.map(file => {
      if (typeof file !== 'string' || !path.isAbsolute(file) || key(path.dirname(file)) !== key(resolvedParent) || key(file) === key(resolvedParent)) throw new Error('只能操作当前目录中明确选中的项目。')
      return [key(file), path.resolve(file)]
    })).values()]
    for (const file of unique) {
      if (key(await fs.promises.realpath(path.dirname(file))) !== key(realParent)) throw new Error('目录已变化，请刷新后重试。')
      if (forMutation && options.protectedPaths.some(protectedPath => key(protectedPath) === key(file) || key(protectedPath).startsWith(key(file) + path.sep)))
        throw new Error('不能删除或重命名用户主目录、软件目录、配置目录或包含它们的上级目录。')
      await fs.promises.lstat(file) // Operate on the link itself, never resolve a symlink target for removal.
    }
    return unique
  }
  async function locked<T>(run: () => Promise<T>): Promise<T> {
    if (mutating) throw new Error('有文件操作尚未完成，请稍后重试。')
    mutating = true
    try { return await run() } finally { mutating = false }
  }
  return {
    targets,
    rename: (parent: string, item: string, baseName: string) => locked(async () => {
      await options.assertIdle()
      const [source] = await targets(parent, [item], true)
      const info = await fs.promises.lstat(source)
      validateEntryName(baseName)
      // The input edits only the basename for files; their original extension stays intact.
      const directory = info.isDirectory() || (info.isSymbolicLink() && (await fs.promises.stat(source).catch(() => null))?.isDirectory())
      const extension = directory ? '' : path.extname(source)
      const name = baseName + extension
      validateEntryName(name)
      const destination = path.join(path.dirname(source), name)
      if (source === destination) return source
      if (key(source) === key(destination)) throw new Error('仅大小写不同的改名请在系统文件管理器中操作。')
      try { await fs.promises.lstat(destination); throw new Error('同名文件或文件夹已存在，不会覆盖。') }
      catch (error: any) { if (error.code !== 'ENOENT') throw error }
      await options.assertIdle()
      await renameWithoutReplace(source, destination)
      return destination
    }),
    trash: (parent: string, items: string[]) => locked<TrashResult>(async () => {
      await options.assertIdle()
      const paths = await targets(parent, items, true)
      let folders = 0
      for (const file of paths) if ((await fs.promises.lstat(file)).isDirectory()) folders += 1
      if (!await options.confirmTrash(paths, folders)) return { cancelled: true, removed: [], failed: [] }
      await options.assertIdle()
      const result: TrashResult = { cancelled: false, removed: [], failed: [] }
      for (const file of paths) {
        try {
          await options.assertIdle()
          await targets(parent, [file], true)
          await options.trashItem(file) // OS trash only. Never fall back to unlink/rm/rmdir.
          result.removed.push(file)
        } catch (error: any) { result.failed.push({ path: file, message: error.message || '无法移入回收站。' }) }
      }
      return result
    }),
  }
}
