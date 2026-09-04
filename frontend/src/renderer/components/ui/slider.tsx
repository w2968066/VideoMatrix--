import * as React from 'react'
import { createPortal } from 'react-dom'
import { cn } from '../../lib/utils'

interface SliderProps {
  value: number[]
  min: number
  max: number
  step: number
  onValueChange?: (value: number[]) => void
  ariaLabel?: string
  dragPreview?: React.ReactNode
  className?: string
}

const SEGMENTS = 36

const Slider = React.forwardRef<HTMLDivElement, SliderProps>(
  ({ value, min, max, step, onValueChange, ariaLabel, dragPreview, className }, ref) => {
    const trackRef = React.useRef<HTMLDivElement | null>(null)
    const draggingRef = React.useRef(false)
    const [previewPoint, setPreviewPoint] = React.useState<{ x: number; y: number } | null>(null)
    React.useImperativeHandle(ref, () => trackRef.current as HTMLDivElement)

    const endDrag = React.useCallback(() => {
      draggingRef.current = false
      setPreviewPoint(null)
    }, [])

    React.useEffect(() => {
      window.addEventListener('pointerup', endDrag)
      window.addEventListener('pointercancel', endDrag)
      window.addEventListener('blur', endDrag)
      return () => {
        window.removeEventListener('pointerup', endDrag)
        window.removeEventListener('pointercancel', endDrag)
        window.removeEventListener('blur', endDrag)
      }
    }, [endDrag])

    const v = value[0]
    const ratio = Math.max(0, Math.min(1, (v - min) / (max - min)))

    const writeFromLocalX = (offsetX: number, track: HTMLDivElement) => {
      // Electron 28 / Chromium 120 reports offsetX in visually zoomed pixels,
      // but CSS width and DOM rectangles before CSS zoom. Include every
      // ancestor's zoom so the pointer and thumb use the same physical width.
      let zoom = 1
      for (let node: HTMLElement | null = track; node; node = node.parentElement) {
        zoom *= parseFloat(getComputedStyle(node).zoom) || 1
      }
      const trackWidth = parseFloat(getComputedStyle(track).width) * zoom
      if (trackWidth <= 0) return
      const r = Math.max(0, Math.min(1, offsetX / trackWidth))
      let nv = min + r * (max - min)
      nv = Math.round(nv / step) * step
      nv = Math.max(min, Math.min(max, parseFloat(nv.toFixed(4))))
      onValueChange?.([nv])
    }

    const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return
      e.preventDefault()
      e.currentTarget.focus()
      e.currentTarget.setPointerCapture(e.pointerId)
      draggingRef.current = true
      setPreviewPoint({ x: e.clientX, y: e.clientY })
      writeFromLocalX(e.nativeEvent.offsetX, e.currentTarget)
    }
    const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
      if (draggingRef.current) {
        writeFromLocalX(e.nativeEvent.offsetX, e.currentTarget)
      }
    }
    const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
      endDrag()
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId)
      }
    }
    const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (!['ArrowLeft', 'ArrowDown', 'ArrowRight', 'ArrowUp'].includes(e.key)) return
      e.preventDefault()
      const direction = e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -1 : 1
      const next = Math.max(min, Math.min(max, parseFloat((v + direction * step).toFixed(4))))
      onValueChange?.([next])
    }

    return (
      <div
        className={cn(
          'relative h-7 w-full select-none touch-none',
          className
        )}
      >
        <div
          ref={trackRef}
          role="slider"
          aria-label={ariaLabel}
          tabIndex={0}
          aria-valuenow={v}
          aria-valuemin={min}
          aria-valuemax={max}
          className="absolute inset-x-1.5 inset-y-0 cursor-ew-resize outline-none focus-visible:ring-1 focus-visible:ring-accent/70"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onLostPointerCapture={endDrag}
          onKeyDown={handleKeyDown}
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

          {/* thumb — centered grip and exact value line */}
          <span
            data-slider-thumb
            className="absolute top-0 bottom-0 w-[2px] bg-accent -translate-x-1/2 pointer-events-none"
            style={{
              left: `${ratio * 100}%`,
              boxShadow: '0 0 8px rgba(232,166,88,0.85), 0 0 1px rgba(232,166,88,1)',
            }}
          />
          <span
            className="absolute top-1/2 h-3 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-[2px] border border-background/70 bg-accent pointer-events-none"
            style={{ left: `${ratio * 100}%` }}
          />
        </div>
        {previewPoint && dragPreview && createPortal(
          <div
            data-subtitle-preview
            aria-hidden="true"
            className="pointer-events-none fixed z-[1000] rounded-lg border border-accent/50 bg-background p-3 text-foreground shadow-xl"
            style={{
              width: 220,
              left: Math.max(8, Math.min(previewPoint.x + 24, window.innerWidth - 228)),
              top: Math.max(8, Math.min(previewPoint.y - 282, window.innerHeight - 274)),
            }}
          >
            {dragPreview}
          </div>,
          document.body,
        )}
      </div>
    )
  }
)
Slider.displayName = 'Slider'

export { Slider }
