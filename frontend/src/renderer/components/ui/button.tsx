import * as React from 'react'
import { cn } from '../../lib/utils'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'glass' | 'ghost'
  size?: 'default' | 'sm' | 'lg' | 'xl'
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'default', size = 'default', type = 'button', ...props }, ref) => {
    return (
      <button
        type={type}
        className={cn(
          'inline-flex items-center justify-center gap-2 font-medium transition-all duration-150',
          'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 focus-visible:ring-offset-2 focus-visible:ring-offset-background',
          'disabled:pointer-events-none disabled:opacity-40 active:translate-y-px',
          'rounded-none',
          {
            'bg-accent text-background hover:bg-accent-hover': variant === 'default',
            'bg-transparent border border-white/[0.18] text-foreground/85 hover:border-accent hover:text-accent': variant === 'glass',
            'bg-transparent text-muted-foreground hover:text-foreground': variant === 'ghost',
          },
          {
            'h-9 px-4 text-sm': size === 'default',
            'h-7 px-3 text-xs': size === 'sm',
            'h-11 px-6 text-sm': size === 'lg',
            'h-14 px-10 text-base font-semibold tracking-wide': size === 'xl',
          },
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = 'Button'

export { Button }
