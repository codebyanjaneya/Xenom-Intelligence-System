// Presentation-layer helpers: translate machine-facing strings (enums, snake_case, status
// codes, raw confidence numbers, internal source references) into plain English for the UI.
// These are display-only — they never touch the underlying data, so provenance, evidence,
// and trace-audit fields stay intact; this module only decides how to *show* them.
// Visual components (badges, evidence rows) live in components/ui and components/evidence.tsx.

/** Turn a snake_case/UPPER_SNAKE token into "Title case words". Fallback for anything not in a lookup below. */
export function humanizeToken(value?: string | null): string {
  if (!value) return 'Unknown'
  return value
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, c => c.toUpperCase())
}

const STATUS_LABELS: Record<string, string> = {
  verified: 'Verified',
  unverified: 'Not yet verified',
  UNVERIFIED: 'Not yet verified',
  VERIFIED: 'Verified',
  needs_review: 'Needs review',
  NEEDS_REVIEW: 'Needs review',
  rejected: 'Rejected',
  accepted: 'Accepted',
  pending: 'Pending',
  draft: 'Draft',
  published: 'Published',
  archived: 'Archived',
  dismissed: 'Dismissed',
  reviewed: 'Reviewed',
  active: 'Active',
  inactive: 'Inactive',
  running: 'Running',
  done: 'Done',
  failed: 'Failed',
  ok: 'OK',
  RESEARCH_RECORDS_FOUND: 'Research records found',
  NOT_STORED: 'Not stored yet',
  NO_EVIDENCE_IN_STORED_CORPUS: 'No supporting evidence found yet',
  EVIDENCE_FOUND: 'Evidence found',
  NOT_CONFIGURED: 'Not configured',
  configured: 'Configured',
  not_configured: 'Not configured',
  literal_term_match: 'Keyword match',
  embedding_similarity: 'Meaning-based match',
  NOT_APPLICABLE: 'Not applicable',
}

/** Human status label for a known status/enum-like string; falls back to title-cased words. */
export function humanStatus(value?: string | null): string {
  if (!value) return 'Unknown'
  return STATUS_LABELS[value] ?? humanizeToken(value)
}

/** "54% confidence" plus a plain-English band, e.g. "54% confidence (moderate)". */
export function humanConfidence(value?: number | null): string | null {
  if (value == null) return null
  const pct = Math.round(value <= 1 ? value * 100 : value)
  const band = pct >= 80 ? 'high' : pct >= 50 ? 'moderate' : 'low'
  return `${pct}% confidence (${band})`
}

/** "Competitor of JCB" from a raw client key. */
export function competitorOf(client?: string | null): string {
  return client ? `Competitor of ${client.toUpperCase()}` : 'Competitor'
}

/** Strip internal profile ids / PDF filenames / path-like basis strings down to a plain sentence.
 * Keeps the human-readable portion; drops tokens that look like "profile:jcb:v1" or "*.pdf".
 */
export function humanBasis(basis: string[]): string | null {
  const clean = basis
    .map(b => b.trim())
    .filter(b => b && !/^profile:/i.test(b) && !/\.pdf(\s|$)/i.test(b) && !/^[a-z0-9_-]+:[a-z0-9_-]+:v?\d+$/i.test(b))
  if (!clean.length) return null
  return clean.join('; ')
}

/** Human date formatting, consistent across the app. */
export const formatDate = (value?: string | null) =>
  value ? new Intl.DateTimeFormat('en', { dateStyle: 'medium' }).format(new Date(value)) : 'Unknown date'

export const formatDateTime = (value?: string | null) =>
  value ? new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : 'Unknown'
