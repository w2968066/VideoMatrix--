import fs from 'fs'
import path from 'path'
import os from 'os'
import type { DirectoryEntry, DirectoryListing } from '../shared/directory'

export function validateEntryName(name: string) {
  if (typeof name !== 'string' || !name.trim() || name === '.' || name === '..' ||
      name !== name.trim() || /[<>:"/\\|?*\x00-\x1f]/.test(name) || /[. ]$/.test(name) ||
      /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)) {
    throw new Error('文件夹名称无效，请勿使用路径、特殊字符、保留名称或末尾空格和句点。')
  }
  if (name.length > 255) throw new Error('文件夹名称太长，请缩短名称。')
}

export async function createDirectory(parent: string, name: string): Promise<string> {
  if (typeof parent !== 'string' || !path.isAbsolute(parent)) throw new Error('当前目录路径无效。')
  validateEntryName(name)
  const realParent = await fs.promises.realpath(parent)
  const target = path.join(realParent, name)
  if (path.dirname(target) !== realParent) throw new Error('只能在当前目录新建文件夹。')
  await fs.promises.mkdir(target) // Non-recursive: never overwrite or reuse an existing item.
  // Return the browsing path, so symlinked locations stay consistent with the listing.
  return path.join(path.resolve(parent), name)
}

let rootsPromise: Promise<string[]> | undefined
function availableRoots(): Promise<string[]> {
  if (os.platform() !== 'win32') return Promise.resolve(['/'])
  if (!rootsPromise) rootsPromise = Promise.all(Array.from({ length: 26 }, async (_, i) => {
    const root = `${String.fromCharCode(65 + i)}:\\`
    let timer: ReturnType<typeof setTimeout> | undefined
    try {
      return await Promise.race([
        fs.promises.stat(root).then(s => s.isDirectory() ? root : null).catch(() => null),
        new Promise<null>(resolve => { timer = setTimeout(() => resolve(null), 800) }),
      ])
    } finally { if (timer) clearTimeout(timer) }
  })).then(roots => roots.filter((root): root is string => root !== null))
  return rootsPromise
}

export async function listDirectory(rawPath?: string): Promise<DirectoryListing> {
  const directory = path.resolve(rawPath?.trim() || os.homedir())
  const entries = await fs.promises.readdir(directory, { withFileTypes: true })
  const rows = new Array<DirectoryEntry>(entries.length)
  let next = 0
  // Limit metadata reads on large folders / slow disks. Never scan folder contents for size.
  await Promise.all(Array.from({ length: Math.min(8, entries.length) }, async () => {
    while (next < entries.length) {
      const index = next++
      const entry = entries[index]
      const entryPath = path.join(directory, entry.name)
      let isDirectory = entry.isDirectory()
      let modifiedAt: number | null = null, size: number | null = null
      try {
        const stat = await fs.promises.stat(entryPath)
        isDirectory = stat.isDirectory()
        modifiedAt = stat.mtimeMs
        size = isDirectory ? null : stat.size
      } catch { /* Unreadable files / broken links remain visible with unknown metadata. */ }
      rows[index] = { name: entry.name, path: entryPath, directory: isDirectory, modifiedAt, size }
    }
  }))
  rows.sort((a, b) => Number(b.directory) - Number(a.directory) || a.name.localeCompare(b.name, 'zh-CN', { numeric: true }))
  const parent = path.dirname(directory)
  return { path: directory, parent: parent === directory ? null : parent,
    home: os.homedir(), roots: await availableRoots(), entries: rows }
}
