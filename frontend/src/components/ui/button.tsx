import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-[13px] font-medium transition-colors ' +
    'disabled:pointer-events-none disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/40',
  {
    variants: {
      variant: {
        default: 'bg-ink text-surface hover:bg-ink/90',
        primary: 'bg-accent text-white hover:bg-accent/90',
        outline: 'border border-border bg-surface text-ink hover:bg-surface-2',
        ghost: 'text-ink-muted hover:bg-surface-2 hover:text-ink',
        subtle: 'bg-surface-2 text-ink hover:bg-border/60',
        danger: 'bg-danger text-white hover:bg-danger/90',
      },
      size: {
        default: 'h-8 px-3',
        sm: 'h-7 px-2.5 text-xs',
        icon: 'h-8 w-8',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
  },
)
Button.displayName = 'Button'
