import * as React from 'react'
import { cn } from '@/lib/utils'

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        'h-8 w-full rounded-md border border-border bg-surface px-2.5 text-[13px] text-ink placeholder:text-ink-faint',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/40',
        className,
      )}
      {...props}
    />
  ),
)
Input.displayName = 'Input'

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        'h-8 rounded-md border border-border bg-surface px-2 text-[13px] text-ink',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/40',
        className,
      )}
      {...props}
    >
      {children}
    </select>
  ),
)
Select.displayName = 'Select'
