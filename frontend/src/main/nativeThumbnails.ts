import fs from 'fs'
import path from 'path'

const mediaExtensions = /\.(mp4|mov|mkv|avi|webm|m4v|wmv|mpeg|mpg|ts|mts|m2ts|jpg|jpeg|png|webp|gif|bmp|tif|tiff|heic|heif|avif)$/i

// Use the OS thumbnail provider only; never start FFmpeg while browsing.
export function createThumbnailReader(provider: (file: string) => Promise<string | null>) {
  const cache = new Map<string, { stamp: string; image: string | null; expires: number }>()
  const pending = new Map<string, Promise<string | null>>()
  const queue: (() => void)[] = []
  let active = 0
  async function limited(file: string) {
    if (active >= 2) await new Promise<void>(resolve => queue.push(resolve))
    else active += 1
    try { return await provider(file) }
    finally {
      const next = queue.shift()
      if (next) next()
      else active -= 1
    }
  }
  return async (file: string): Promise<string | null> => {
    if (typeof file !== 'string' || !path.isAbsolute(file) || !mediaExtensions.test(file)) return null
    try {
      const resolved = path.resolve(file)
      const stat = await fs.promises.stat(resolved)
      if (!stat.isFile()) return null
      const stamp = `${stat.mtimeMs}:${stat.ctimeMs}:${stat.size}`
      const hit = cache.get(resolved)
      if (hit?.stamp === stamp && hit.expires > Date.now()) {
        cache.delete(resolved); cache.set(resolved, hit)
        return hit.image
      }
      const key = `${resolved}\0${stamp}`
      if (pending.has(key)) return pending.get(key)!
      const promise = limited(resolved).catch(() => null).then(image => {
        cache.delete(resolved)
        cache.set(resolved, { stamp, image, expires: Date.now() + (image ? 300_000 : 10_000) })
        while (cache.size > 160) cache.delete(cache.keys().next().value!)
        return image
      }).finally(() => pending.delete(key))
      pending.set(key, promise)
      return promise
    } catch { return null }
  }
}
