import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium leading-none', {
  variants: {
    variant: {
      neutral: 'bg-surface-2 text-ink-muted',
      accent: 'bg-accent-soft text-accent',
      warn: 'bg-warn-soft text-warn',
      danger: 'bg-danger-soft text-danger',
      outline: 'border border-border text-ink-muted',
    },
  },
  defaultVariants: { variant: 'neutral' },
})

export function Badge({ className, variant, ...props }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant, className }))} {...props} />
}
