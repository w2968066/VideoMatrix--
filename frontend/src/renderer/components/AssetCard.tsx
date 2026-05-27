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
  onClear?: () => void
  pickAction?: string
}

export function AssetCard({ kind, label, value, count, required, onBrowse, onClear, pickAction = '浏览' }: AssetCardProps) {
  const filled = !!value
  const tail = filled ? value.split('/').filter(Boolean).slice(-2).join('/') : '未选择'

  return (
    <div
      className={cn(
        'group relative overflow-hidden rounded-lg transition-all duration-200',
        // base elevation
        filled
          ? 'border border-accent/35 bg-gradient-to-b from-[rgba(232,166,88,0.05)] to-[rgba(232,166,88,0.01)] shadow-[0_2px_10px_-4px_rgba(232,166,88,0.25),inset_0_1px_0_rgba(255,255,255,0.04)]'
          : 'border border-white/[0.08] bg-gradient-to-b from-white/[0.025] to-white/[0.005]',
        'hover:border-accent/55 hover:shadow-[0_6px_22px_-6px_rgba(232,166,88,0.45),inset_0_1px_0_rgba(255,255,255,0.06)]',
        'hover:-translate-y-px'
      )}
    >
      {/* Preview area — clickable to browse */}
      <button
        type="button"
        onClick={onBrowse}
        className="w-full text-left px-3 pt-3 pb-2.5"
      >
        <div className="flex items-start gap-2.5">
          <div
            className={cn(
              'w-9 h-9 rounded-md flex items-center justify-center transition-colors shrink-0',
              filled
                ? 'bg-accent/15 text-accent ring-1 ring-inset ring-accent/30'
                : 'bg-white/[0.04] text-muted-foreground'
            )}
          >
            <span className="w-[18px] h-[18px] block">{AssetIcons[kind]}</span>
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-[12px] font-medium text-foreground/90 leading-none">{label}</span>
              {required && !filled && (
                <span className="text-[9px] uppercase tracking-wider text-hot/80">必填</span>
              )}
            </div>
            <div className="mt-1.5 flex items-center gap-2">
              {filled && count !== undefined && (
                <span className="font-mono text-[10px] tabular-nums text-accent/90">
                  {count} 个文件
                </span>
              )}
              {!filled && (
                <span className="text-[10px] text-muted-foreground/70">{pickAction}导入</span>
              )}
            </div>
          </div>
        </div>
      </button>

      {/* Path footer */}
      <div className="border-t border-white/[0.05] bg-black/15 px-3 py-1.5 flex items-center gap-2">
        <svg width="9" height="9" viewBox="0 0 12 12" fill="none" className="text-muted-foreground/60 shrink-0">
          <path d="M2 3.5l3-2 5 2v6L5 11.5l-3-2v-6z" stroke="currentColor" strokeWidth="1" />
        </svg>
        <span
          className={cn(
            'flex-1 font-mono text-[10px] truncate',
            filled ? 'text-muted-foreground' : 'text-muted/45'
          )}
          title={value}
        >
          {tail}
        </span>
        {filled && onClear && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onClear() }}
            className="w-3.5 h-3.5 flex items-center justify-center text-muted-foreground hover:text-hot transition-colors text-[12px] leading-none opacity-0 group-hover:opacity-100"
            aria-label="清除"
          >
            ×
          </button>
        )}
      </div>
    </div>
  )
}
