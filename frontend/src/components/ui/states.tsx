import { AlertCircle, Inbox, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-md bg-surface-2', className)} />
}

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 px-1 py-8 text-[13px] text-ink-muted">
      <Loader2 size={16} className="animate-spin" /> {label}
    </div>
  )
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-danger/30 bg-danger-soft px-3 py-2.5 text-[13px] text-danger">
      <AlertCircle size={16} className="mt-0.5 shrink-0" />
      <div><strong className="font-semibold">Couldn't load this</strong><p className="mt-0.5 text-danger/90">{message}</p></div>
    </div>
  )
}

export function EmptyState({ text, hint }: { text: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-md border border-dashed border-border px-6 py-10 text-center">
      <Inbox size={20} className="text-ink-faint" />
      <p className="text-[13px] text-ink-muted">{text}</p>
      {hint && <p className="text-xs text-ink-faint">{hint}</p>}
    </div>
  )
}

/** A view's error/loading/ready gate — the standard shape used everywhere data is fetched. */
export function DataState({ error, ready, children }: { error?: string; ready: boolean; children: React.ReactNode }) {
  if (error) return <ErrorState message={error} />
  if (!ready) return <LoadingState />
  return <>{children}</>
}
