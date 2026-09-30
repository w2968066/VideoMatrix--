import { useEffect, useRef, useState } from 'react'
import { AssetIcons } from './AssetCard'

type Job = { run: () => Promise<void>; cancelled: () => boolean }
const queue: Job[] = []
let active = 0
function drain() {
  while (active < 2 && queue.length) {
    const job = queue.shift()!
    if (job.cancelled()) continue
    active += 1
    void job.run().finally(() => { active -= 1; drain() })
  }
}

export function DirectoryThumbnail({ file, name, directory, compact = false }: { file: string; name: string; directory: boolean; compact?: boolean }) {
  const box = useRef<HTMLSpanElement>(null)
  const [image, setImage] = useState<string | null>(null)
  const [status, setStatus] = useState('')
  const fallback = directory ? AssetIcons.output : /\.(wav|mp3|aac|flac|ogg|m4a)$/i.test(name) ? AssetIcons.bgm
    : /\.(srt|ass|ssa|txt)$/i.test(name) ? AssetIcons.srt : AssetIcons.body
  useEffect(() => {
    setImage(null); setStatus('')
    if (compact || directory || !/\.(mp4|mov|mkv|avi|webm|m4v|wmv|mpeg|mpg|ts|mts|m2ts|jpg|jpeg|png|webp|gif|bmp|tif|tiff|heic|heif|avif)$/i.test(name)) return
    let cancelled = false
    let scheduled = false
    const observer = new IntersectionObserver(entries => {
      if (scheduled || !entries.some(entry => entry.isIntersecting)) return
      scheduled = true; observer.disconnect(); setStatus('正在加载缩略图')
      queue.push({ cancelled: () => cancelled, run: async () => {
        let result: string | null = null
        try { result = await window.electronAPI?.readThumbnail?.(file) || null } catch { /* Keep the file visible on provider failure. */ }
        if (!cancelled) { setImage(result); setStatus(result ? '' : '系统未提供缩略图') }
      } })
      drain()
    }, { root: box.current?.closest('.directory-picker-list') || null })
    if (box.current) observer.observe(box.current)
    return () => { cancelled = true; observer.disconnect() }
  }, [file, name, directory, compact])
  return <span ref={box} className={`directory-thumbnail ${compact ? 'directory-thumbnail-compact' : ''} ${directory ? 'text-accent' : 'text-muted-foreground'}`} title={compact ? undefined : status || undefined}>
    {image && !compact ? <img src={image} alt={`${name} 缩略图`} width={192} height={128} draggable={false}
      onError={() => { setImage(null); setStatus('系统未提供缩略图') }} />
      : <><span className={compact ? 'h-4 w-4' : 'h-10 w-10'}>{fallback}</span>
        {!compact && status && <span className="mt-1 text-[10px]">{status}</span>}</>}
  </span>
}
