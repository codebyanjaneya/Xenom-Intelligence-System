import { ExternalLink } from 'lucide-react'
import { Badge } from './ui/badge'
import { humanConfidence, humanStatus } from '../format'

const TONE_RX_GOOD = /verified|active|accepted|published|found|ok|done/i
const TONE_RX_WARN = /review|pending|unverified|not|missing|failed|rejected/i

export function StatusPill({ value, tone }: { value?: string | null; tone?: 'accent' | 'warn' | 'danger' | 'neutral' }) {
  const label = humanStatus(value)
  const v = value ?? ''
  const inferred = tone ?? (TONE_RX_GOOD.test(v) && !/not|un/i.test(v) ? 'accent' : TONE_RX_WARN.test(v) ? 'warn' : 'neutral')
  return <Badge variant={inferred}>{label}</Badge>
}

export function ConfidencePill({ value }: { value?: number | null }) {
  const text = humanConfidence(value)
  if (!text) return null
  return <Badge variant="neutral">{text}</Badge>
}

const formatDate = (value?: string | null) => value ? new Intl.DateTimeFormat('en', { dateStyle: 'medium' }).format(new Date(value)) : 'Unknown date'

/** source · date · quote · View source — the standard evidence row. */
export function EvidenceRow({ source, date, quote, url }: { source?: string | null; date?: string | null; quote?: string | null; url?: string | null }) {
  return (
    <div className="border-t border-border py-2.5 first:border-t-0">
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-ink-faint">
        <span className="font-medium text-ink-muted">{source || 'Unknown source'}</span>
        <span>·</span>
        <span>{formatDate(date)}</span>
      </div>
      {quote && <p className="mt-1 break-words text-[13px] leading-relaxed text-ink-muted">&ldquo;{quote}&rdquo;</p>}
      {url && (
        <a href={url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline">
          View source <ExternalLink size={11} />
        </a>
      )}
    </div>
  )
}

export function SectionEyebrow({ children }: { children: React.ReactNode }) {
  return <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-faint">{children}</p>
}
