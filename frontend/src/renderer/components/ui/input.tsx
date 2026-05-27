import * as React from 'react'
import { cn } from '../../lib/utils'

/**
 * Bracket-frame input. The four corners are rendered as L-shaped tick marks
 * via ::before / ::after on wrapper spans, giving inputs a "viewfinder" look
 * without any solid border or fill.
 */
const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => {
    return (
      <div className="relative w-full group">
        {/* corner brackets */}
        <span className="bracket bracket-tl" />
        <span className="bracket bracket-tr" />
        <span className="bracket bracket-bl" />
        <span className="bracket bracket-br" />
        <input
          type={type}
          className={cn(
            'flex h-8 w-full bg-transparent px-3 py-1 font-mono text-[12px] text-foreground placeholder:text-muted',
            'border-0 outline-none focus:outline-none',
            'transition-colors',
            className
          )}
          ref={ref}
          {...props}
        />
      </div>
    )
  }
)
Input.displayName = 'Input'

export { Input }
