import * as React from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

export function Sheet({ open, onOpenChange, children }: { open: boolean; onOpenChange: (v: boolean) => void; children: React.ReactNode }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30 data-[state=open]:animate-in data-[state=open]:fade-in data-[state=closed]:animate-out data-[state=closed]:fade-out" />
        <Dialog.Content
          className={cn(
            'fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-border bg-surface shadow-xl',
            'data-[state=open]:animate-in data-[state=open]:slide-in-from-right data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right',
          )}
        >
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

export function SheetHeader({ title, subtitle, onClose }: { title: React.ReactNode; subtitle?: React.ReactNode; onClose: () => void }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
      <div className="min-w-0">
        {subtitle && <p className="mb-0.5 text-[11px] font-medium uppercase tracking-wide text-ink-faint">{subtitle}</p>}
        <Dialog.Title className="truncate text-[15px] font-semibold text-ink">{title}</Dialog.Title>
      </div>
      <button onClick={onClose} className="rounded-md p-1 text-ink-muted hover:bg-surface-2 hover:text-ink" aria-label="Close">
        <X size={16} />
      </button>
    </div>
  )
}

export function SheetBody({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex-1 overflow-y-auto px-5 py-4', className)} {...props} />
}
