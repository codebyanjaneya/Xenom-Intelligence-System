import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

export function KpiStat({ label, value, sub, icon: Icon, tone = 'neutral' }: {
  label: string; value: React.ReactNode; sub?: React.ReactNode; icon?: LucideIcon; tone?: 'neutral' | 'accent' | 'warn'
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3">
      {Icon && (
        <div className={cn('flex h-9 w-9 shrink-0 items-center justify-center rounded-md',
          tone === 'accent' ? 'bg-accent-soft text-accent' : tone === 'warn' ? 'bg-warn-soft text-warn' : 'bg-surface-2 text-ink-muted')}>
          <Icon size={16} />
        </div>
      )}
      <div className="min-w-0">
        <p className="text-[11px] font-medium uppercase tracking-wide text-ink-faint">{label}</p>
        <p className="truncate text-lg font-semibold leading-tight text-ink">{value}</p>
        {sub && <p className="truncate text-xs text-ink-muted">{sub}</p>}
      </div>
    </div>
  )
}

export function KpiRow({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">{children}</div>
}
