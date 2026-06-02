import * as React from 'react'
import { cn } from '../../lib/utils'

interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: 'default' | 'outline' | 'secondary'
}

function Badge({ className, variant = 'default', ...props }: BadgeProps) {
  return (
    <div
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium',
        {
          'bg-accent/15 text-accent border border-accent/25': variant === 'default',
          'glass-input text-muted-foreground': variant === 'secondary',
          'border border-white/[0.10] text-muted-foreground': variant === 'outline',
        },
        className
      )}
      {...props}
    />
  )
}

export { Badge }
