import * as React from 'react'
import { cn } from '../lib/utils'

// ─── Asset type icons ─────────────────────────────────────────────────────────
export const AssetIcons = {
  hook: (
    // lightning bolt — "hook" grabs the viewer
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2L4.5 14h6L11 22l8.5-12h-6L13 2z" />
    </svg>
  ),
  body: (
    // film strip — "body" is the bulk material
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="14" rx="1.5" />
      <path d="M3 9h2M3 13h2M3 17h2M19 9h2M19 13h2M19 17h2M9 5v14M15 5v14" />
    </svg>
  ),
  bgm: (
    // wave + note
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 18V6l9-2v12" />
      <circle cx="7" cy="18" r="2.5" />
      <circle cx="16" cy="16" r="2.5" />
    </svg>
  ),
  voice: (
    // microphone
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0014 0M12 18v3M9 21h6" />
    </svg>
  ),
  srt: (
    // text lines (subtitle)
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="14" rx="1.5" />
      <path d="M6 11h6M6 15h12M14 11h4" />
    </svg>
  ),
  watermark: (
    // image with sparkle
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M3 16l5-5 5 5 3-3 5 5" />
      <circle cx="9" cy="9" r="1.4" />
    </svg>
  ),
  output: (
    // folder with arrow
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
      <path d="M12 12v5m0 0l-2-2m2 2l2-2" />
    </svg>
  ),
}

// ─── Card ─────────────────────────────────────────────────────────────────────
interface AssetCardProps {
  kind: keyof typeof AssetIcons
  label: string
  value: string
  count?: number
  required?: boolean
  onBrowse: () => void
  onChange: (value: string) => void
  onOpen?: () => void
  onClear?: () => void
  pickAction?: string
}

export function AssetCard({ kind, label, value, count, required, onBrowse, onChange, onOpen, onClear, pickAction = '浏览' }: AssetCardProps) {
  const filled = !!value

  return (
    <div
      className={cn(
        'group relative overflow-hidden rounded-md transition-colors',
        filled
          ? 'border border-accent/35 bg-[rgba(232,166,88,0.035)]'
          : 'border border-white/[0.08] bg-white/[0.015]',
        'hover:border-accent/50'
      )}
    >
      <div className="flex items-center gap-2 px-2 py-1.5">
        <button
          type="button"
          onClick={onOpen}
          disabled={!filled || !onOpen}
          title={filled ? '打开路径' : undefined}
          className={cn(
            'w-7 h-7 rounded-[4px] flex items-center justify-center shrink-0 transition-colors',
            filled
              ? 'bg-accent/14 text-accent ring-1 ring-inset ring-accent/25 hover:bg-accent/25'
              : 'bg-white/[0.035] text-muted-foreground',
            (!filled || !onOpen) && 'cursor-default'
          )}
        >
          <span className="w-[15px] h-[15px] block">{AssetIcons[kind]}</span>
        </button>
        <label className="w-[76px] shrink-0 text-[11px] text-foreground/85">
          {label}
          {required && !filled && <span className="ml-1 text-hot/85">必填</span>}
        </label>
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          title={value}
          placeholder={`${pickAction}或粘贴路径`}
          className="h-8 min-w-0 flex-1 rounded-[4px] border border-white/[0.08] bg-black/20 px-2.5 font-mono text-[11px] text-foreground outline-none placeholder:text-muted-foreground/45 focus:border-accent/60"
        />
        {filled && count !== undefined && (
          <span className="w-16 shrink-0 text-right font-mono text-[10px] tabular-nums text-accent/90">
            {count} 文件
          </span>
        )}
        <button
          type="button"
          onClick={onBrowse}
          className="h-8 shrink-0 rounded-[4px] border border-white/[0.08] px-3 text-[11px] text-foreground/85 hover:border-accent/50 hover:text-accent"
        >
          {pickAction}
        </button>
        {filled && onClear && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onClear() }}
            className="h-8 shrink-0 rounded-[4px] border border-white/[0.08] px-2.5 text-[11px] text-muted-foreground hover:border-hot/40 hover:text-hot"
            aria-label="清除"
          >
            清除
          </button>
        )}
      </div>
    </div>
  )
}
