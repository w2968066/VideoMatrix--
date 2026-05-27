import * as React from 'react'
import { cn } from '../../lib/utils'

interface SliderProps {
  value: number[]
  min: number
  max: number
  step: number
  onValueChange?: (value: number[]) => void
  className?: string
}

const SEGMENTS = 36

const Slider = React.forwardRef<HTMLDivElement, SliderProps>(
  ({ value, min, max, step, onValueChange, className }, ref) => {
    const trackRef = React.useRef<HTMLDivElement | null>(null)
    React.useImperativeHandle(ref, () => trackRef.current as HTMLDivElement)

    const v = value[0]
    const ratio = Math.max(0, Math.min(1, (v - min) / (max - min)))

    const writeFromClientX = (clientX: number) => {
      const el = trackRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      const r = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
      let nv = min + r * (max - min)
      nv = Math.round(nv / step) * step
      nv = Math.max(min, Math.min(max, parseFloat(nv.toFixed(4))))
      onValueChange?.([nv])
    }

    const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
      e.currentTarget.setPointerCapture(e.pointerId)
      writeFromClientX(e.clientX)
    }
    const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.buttons & 1) writeFromClientX(e.clientX)
    }

    return (
      <div
        ref={trackRef}
        role="slider"
        aria-valuenow={v}
        aria-valuemin={min}
        aria-valuemax={max}
        className={cn(
          'relative h-7 w-full cursor-pointer select-none touch-none',
          className
        )}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
      >
        {/* baseline */}
        <span className="absolute left-0 right-0 top-1/2 h-px bg-white/[0.06] -translate-y-1/2 pointer-events-none" />

        {/* tick ruler */}
        <div className="absolute inset-0 flex items-center justify-between pointer-events-none">
          {Array.from({ length: SEGMENTS }).map((_, i) => {
            const filled = i / (SEGMENTS - 1) <= ratio
            const major = i % 6 === 0
            return (
              <span
                key={i}
                className={cn(
                  'w-px transition-colors duration-100',
                  major ? 'h-4' : 'h-2',
                  filled ? 'bg-accent' : 'bg-white/[0.16]'
                )}
              />
            )
          })}
        </div>

        {/* thumb — vertical bar */}
        <span
          className="absolute top-0 bottom-0 w-[2px] bg-accent -translate-x-1/2 pointer-events-none"
          style={{
            left: `${ratio * 100}%`,
            boxShadow: '0 0 8px rgba(232,166,88,0.85), 0 0 1px rgba(232,166,88,1)',
          }}
        />
      </div>
    )
  }
)
Slider.displayName = 'Slider'

export { Slider }
