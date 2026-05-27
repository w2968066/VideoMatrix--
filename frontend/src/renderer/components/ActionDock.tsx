import * as React from 'react'
import { cn } from '../lib/utils'

// Bottom action bar. Sticks to the bottom of the viewport, sits above the page
// content. Inner children render as a horizontal row of rectangular buttons.
export function BottomBar({ children }: { children: React.ReactNode }) {
  return (
    <div className="shrink-0 border-t border-white/[0.06] px-10 py-3 flex items-center gap-3 bg-background/80 backdrop-blur-xl">
      {children}
    </div>
  )
}

// ─── Primary rectangular action button (启动渲染) ─────────────────────────────
interface BarPrimaryProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  loading?: boolean
}

export const BarPrimary = React.forwardRef<HTMLButtonElement, BarPrimaryProps>(
  ({ label, loading, children, className, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      className={cn(
        'flex-1 h-11 rounded-md bg-accent text-background',
        'inline-flex items-center justify-center gap-2.5',
        'font-medium text-[13px] tracking-wide',
        'shadow-[inset_0_1px_0_rgba(255,255,255,0.28),0_0_0_1px_rgba(232,166,88,0.45),0_8px_24px_-8px_rgba(232,166,88,0.55)]',
        'hover:bg-accent-hover hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.35),0_0_0_1px_rgba(232,166,88,0.7),0_12px_32px_-8px_rgba(232,166,88,0.8)]',
        'transition-colors duration-150 active:translate-y-px',
        'disabled:opacity-40 disabled:pointer-events-none',
        className
      )}
      {...props}
    >
      {loading ? (
        <>
          <span className="w-3.5 h-3.5 border-2 border-background/40 border-t-background rounded-full animate-spin-slow" />
          启动中…
        </>
      ) : (
        <>
          {children}
          {label}
        </>
      )}
    </button>
  )
)
BarPrimary.displayName = 'BarPrimary'

// ─── Secondary rectangular button ─────────────────────────────────────────────
interface BarSecondaryProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
}

export const BarSecondary = React.forwardRef<HTMLButtonElement, BarSecondaryProps>(
  ({ label, children, className, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      className={cn(
        'h-11 px-5 rounded-md',
        'inline-flex items-center justify-center gap-2',
        'bg-transparent border border-white/[0.14] text-foreground/80',
        'font-medium text-[12px] tracking-wide',
        'hover:border-accent/80 hover:text-accent hover:bg-accent/[0.06]',
        'transition-colors duration-150 active:translate-y-px',
        'disabled:opacity-40 disabled:pointer-events-none',
        className
      )}
      {...props}
    >
      {children}
      {label}
    </button>
  )
)
BarSecondary.displayName = 'BarSecondary'
