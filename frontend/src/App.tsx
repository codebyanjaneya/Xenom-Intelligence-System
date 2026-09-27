import { useEffect, useState } from 'react'
import {
  AlertCircle, ArrowUpRight, BarChart3, Bookmark, Check, Clock3, LayoutGrid, LogOut, Moon, Newspaper,
  Search, ShieldCheck, Sun, Target, Users, X,
} from 'lucide-react'
import { ClientsPanel, CompetitorsPanel, NewsDetectorPanel, IndustryPanel, MorningDigestPanel, ResearchStatus, SourcesPanel, TechnologyView } from './views'
import { ArticleDetailPanel, CapabilityStrip, RecommendationsPanel, ResearchersPanel, SavedArticlesPanel } from './admin'
import { type EvidenceSearch, type SearchOptions, searchEvidence } from './architecture'
import { type Alert, type ClientSummaryView, type Article, type Briefing, type Claim, type Entity, type Evidence, type Insight, type IntelligenceResponse, type Opportunity, type PipelineStatus, type RegistryEntity, createClaim, extractIntelligence, getClientBriefing, getClientSummary, getCurrentUser, getFeed, getPipelineStatus, listClaims, listClients, login, logout as apiLogout, updateClaimStatus } from './api'
import { listSavedArticles, saveArticle, unsaveArticle } from './architecture'
import { formatDateTime, humanizeToken, humanStatus } from './format'
import { StatusPill, EvidenceRow, SectionEyebrow } from './components/evidence'
import { Button } from './components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardEyebrow } from './components/ui/card'
import { Badge } from './components/ui/badge'
import { Input, Select } from './components/ui/input'
import { DataState, EmptyState, LoadingState, ErrorState } from './components/ui/states'
import { KpiRow, KpiStat } from './components/ui/kpi'
import { cn } from './lib/utils'
import { CopilotPanel } from './components/CopilotPanel'
import { CheckCircle2, Radio, Users2 } from 'lucide-react'

type Phase = 'idle' | 'loading' | 'success' | 'error'
const formatDate = formatDateTime

const getRelativeTime = (dateStr?: string | null) => {
  if (!dateStr) return 'Recently'
  const date = new Date(dateStr)
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`
  return formatDate(dateStr)
}

// ---- article cards (Today feed) -----------------------------------------------------------------------------------
function HeroArticle({ article }: { article: Article }) {
  const [imageError, setImageError] = useState(false)
  const url = article.canonical_url || article.url
  const hasImage = article.image_url && !imageError
  if (!hasImage) {
    // No real thumbnail: a clean text card, never a blank image box.
    return (
      <a href={url} target="_blank" rel="noreferrer" className="block h-full rounded-lg border border-border bg-surface p-6">
        <div className="mb-3 flex flex-wrap gap-1.5">
          {article.entity_names?.[0] && <Badge variant="accent">{article.entity_names[0]}</Badge>}
          {article.topic && <Badge variant="outline">{article.topic}</Badge>}
        </div>
        <h2 className="mb-2 max-w-[95%] text-xl font-semibold leading-snug text-ink">{article.title}</h2>
        <div className="flex gap-3 text-xs font-medium text-ink-muted">
          <span>{article.source_name}</span><span>{getRelativeTime(article.published_at || article.fetched_at)}</span>
        </div>
      </a>
    )
  }
  return (
    <a href={url} target="_blank" rel="noreferrer" className="group block h-full">
      <div className="relative min-h-[360px] overflow-hidden rounded-lg border border-border bg-surface-2">
        <img src={article.image_url || ''} alt="" className="absolute inset-0 h-full w-full object-cover" onError={() => setImageError(true)} />
        <div className="absolute inset-0 flex flex-col justify-end bg-gradient-to-t from-black/80 via-black/30 to-transparent p-6">
          <div className="mb-3 flex flex-wrap gap-1.5">
            {article.entity_names?.[0] && <Badge variant="accent" className="bg-white/90 text-ink">{article.entity_names[0]}</Badge>}
            {article.topic && <Badge variant="outline" className="border-white/40 text-white/90">{article.topic}</Badge>}
          </div>
          <h2 className="mb-2 max-w-[95%] text-xl font-semibold leading-snug text-white">{article.title}</h2>
          <div className="flex gap-3 text-xs font-medium text-white/80">
            <span>{article.source_name}</span><span>{getRelativeTime(article.published_at || article.fetched_at)}</span>
          </div>
        </div>
      </div>
    </a>
  )
}

function ListArticle({ article }: { article: Article }) {
  const [imageError, setImageError] = useState(false)
  const url = article.canonical_url || article.url
  const hasImage = article.image_url && !imageError
  return (
    <a href={url} target="_blank" rel="noreferrer" className="flex gap-3 border-b border-border py-3 last:border-0 hover:bg-surface-2/60 -mx-2 px-2 rounded-md transition-colors">
      {hasImage && <img src={article.image_url || ''} alt="" className="h-16 w-16 shrink-0 rounded-md object-cover" onError={() => setImageError(true)} />}
      <div className="min-w-0">
        <h3 className="line-clamp-2 text-[13px] font-medium leading-snug text-ink">{article.title}</h3>
        <div className="mt-1 flex gap-2 text-xs text-ink-faint">
          <span className="font-medium text-ink-muted">{article.source_name}</span><span>{getRelativeTime(article.published_at || article.fetched_at)}</span>
        </div>
      </div>
    </a>
  )
}

function GridArticle({ article }: { article: Article }) {
  const [imageError, setImageError] = useState(false)
  const url = article.canonical_url || article.url
  const hasImage = article.image_url && !imageError
  return (
    <a href={url} target="_blank" rel="noreferrer" className="block h-full overflow-hidden rounded-lg border border-border bg-surface transition-colors hover:border-accent/40">
      {hasImage ? <img src={article.image_url || ''} alt="" className="h-32 w-full object-cover" onError={() => setImageError(true)} />
        : <div className="flex h-32 w-full items-center justify-center bg-surface-2 text-xs text-ink-faint">{article.topic || 'News'}</div>}
      <div className="p-3">
        <h3 className="line-clamp-2 text-[13px] font-medium leading-snug text-ink">{article.title}</h3>
        <div className="mt-2 flex gap-2 text-xs text-ink-faint"><span className="font-medium text-ink-muted">{article.source_name}</span><span>{getRelativeTime(article.published_at || article.fetched_at)}</span></div>
      </div>
    </a>
  )
}

/** No-thumbnail articles as a compact text row: source, headline, date, bookmark — never an empty image box. */
function TextNewsRow({ article, token, saved, onToggleSaved }: { article: Article; token?: string; saved?: boolean; onToggleSaved?: (id: string, next: boolean) => void }) {
  const [pending, setPending] = useState(false)
  const url = article.canonical_url || article.url
  async function toggle() {
    if (!token || pending) return
    setPending(true)
    const next = !saved
    try {
      if (next) await saveArticle(token, article.id); else await unsaveArticle(token, article.id)
      onToggleSaved?.(article.id, next)
    } catch { /* leave state unchanged on failure */ } finally { setPending(false) }
  }
  return (
    <div className="flex items-center gap-3 border-b border-border py-2 last:border-0">
      <a href={url} target="_blank" rel="noreferrer" className="min-w-0 flex-1 hover:text-accent">
        <p className="truncate text-[13px] font-medium text-ink">{article.title}</p>
        <p className="mt-0.5 flex gap-2 text-xs text-ink-faint"><span className="font-medium text-ink-muted">{article.source_name}</span><span>{getRelativeTime(article.published_at || article.fetched_at)}</span></p>
      </a>
      <button onClick={toggle} disabled={pending} aria-label={saved ? 'Remove from saved' : 'Save article'} className={cn('shrink-0 text-ink-faint hover:text-accent', saved && 'text-accent')}>
        <Bookmark size={14} fill={saved ? 'currentColor' : 'none'} />
      </button>
    </div>
  )
}

/** Articles with a real thumbnail render as image cards; the rest render as a scrollable text list — never a broken/empty image box. */
function NewsGrid({ articles, token, savedIds, onToggleSaved }: { articles: Article[]; token?: string; savedIds?: Set<string>; onToggleSaved?: (id: string, next: boolean) => void }) {
  const withImage = articles.filter(a => a.image_url)
  const withoutImage = articles.filter(a => !a.image_url)
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
      {withImage.length > 0 && <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">{withImage.map(a => <GridArticle key={a.id} article={a} />)}</div>}
      {withoutImage.length > 0 && <div className="max-h-[420px] overflow-y-auto rounded-lg border border-border bg-surface px-3">{withoutImage.map(a => <TextNewsRow key={a.id} article={a} token={token} saved={savedIds?.has(a.id)} onToggleSaved={onToggleSaved} />)}</div>}
    </div>
  )
}

function SidebarCard({ article }: { article: Article }) {
  const [imageError, setImageError] = useState(false)
  const hasImage = article.image_url && !imageError
  const url = article.canonical_url || article.url
  return (
    <a href={url} target="_blank" rel="noreferrer" className="flex gap-2.5 border-b border-border pb-3 pt-3 first:pt-0 last:border-0">
      {hasImage && <img src={article.image_url || ''} alt="" className="h-12 w-12 shrink-0 rounded-md object-cover" onError={() => setImageError(true)} />}
      <div className="min-w-0">
        <h4 className="line-clamp-2 text-xs font-medium leading-snug text-ink">{article.title}</h4>
        <p className="mt-1 text-[11px] text-ink-faint">{getRelativeTime(article.published_at || article.fetched_at)}</p>
      </div>
    </a>
  )
}

// ---- login -----------------------------------------------------------------------------------------------------
function Login({ onLogin }: { onLogin: (token: string, name: string, role: string) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setLoading(true); setError('')
    try { const result = await login(email, password); onLogin(result.access_token, result.user.name || result.user.email, result.user.role) }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to sign in') } finally { setLoading(false) }
  }
  return (
    <main className="grid min-h-screen grid-cols-1 bg-bg lg:grid-cols-2">
      <div className="flex flex-col justify-center gap-6 px-10 py-16 lg:px-16">
        <div className="text-lg font-semibold tracking-tight text-ink">XENOM <span className="font-normal text-ink-faint">Intelligence</span></div>
        <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">Public-information intelligence</p>
        <h1 className="max-w-md text-3xl font-semibold leading-tight text-ink">Know what changed. <em className="not-italic text-accent">Know what to do next.</em></h1>
        <p className="max-w-sm text-sm text-ink-muted">Evidence-backed research for client organizations, companies, technologies and markets.</p>
        <div className="flex items-center gap-2 text-sm text-ink-muted"><ShieldCheck size={16} className="text-accent" /> Every signal keeps its source and verification state.</div>
      </div>
      <div className="flex items-center justify-center border-t border-border bg-surface px-10 py-16 lg:border-l lg:border-t-0">
        <form onSubmit={submit} className="w-full max-w-sm space-y-4">
          <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">Admin access</p>
          <h2 className="text-xl font-semibold text-ink">Open intelligence desk</h2>
          <label className="block space-y-1"><span className="text-xs font-medium text-ink-muted">Email</span><Input value={email} onChange={e => setEmail(e.target.value)} type="email" required /></label>
          <label className="block space-y-1"><span className="text-xs font-medium text-ink-muted">Password</span><Input value={password} onChange={e => setPassword(e.target.value)} type="password" required /></label>
          {error && <ErrorState message={error} />}
          <Button type="submit" variant="primary" className="w-full" disabled={loading}>{loading ? 'Signing in…' : 'Sign in'}<ArrowUpRight size={14} /></Button>
        </form>
      </div>
    </main>
  )
}

// ---- evidence / entities / timeline / claims (Research tab) --------------------------------------------------------
function EvidenceItem({ item }: { item: Evidence }) {
  return <EvidenceRow source={item.provider} date={item.published_at || item.discovered_at} quote={item.content_span || item.snippet} url={item.source_url} />
}

function EvidenceResults({ data, onDetail }: { data: EvidenceSearch; onDetail: (id: string) => void }) {
  return (
    <Card>
      <CardHeader><div><CardEyebrow>Search · stored corpus · {data.mode}</CardEyebrow><CardTitle>Available signals</CardTitle></div><Badge>{data.count} results</Badge></CardHeader>
      <CardContent className="space-y-3">
        {data.fallback_reason && <div className="flex items-start gap-2 rounded-md bg-warn-soft px-3 py-2 text-xs text-warn"><AlertCircle size={14} className="mt-0.5 shrink-0" /><span>{data.mode_requested} search was requested but lexical search answered: {data.fallback_reason}.</span></div>}
        {data.results.length ? data.results.map(hit => (
          <div key={hit.article_id} className="rounded-md border border-border p-3">
            <div className="mb-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-faint">
              <span className="font-medium text-ink-muted">{[hit.client, hit.source].filter(Boolean).join(' · ') || (hit.source_class && humanizeToken(hit.source_class)) || 'Stored article'}</span>
              <span>· {formatDate(hit.published_at)}</span>
              {hit.tier && <span>· {humanizeToken(hit.tier)}</span>}
              {hit.relevance?.decision && <span>· {humanStatus(hit.relevance.decision)}</span>}
            </div>
            <h3 className="text-[13px] font-medium text-ink">{hit.title ?? hit.url}</h3>
            {hit.spans[0] && <p className="mt-1 text-[13px] text-ink-muted">{hit.spans[0].quote}</p>}
            {[...new Set(hit.spans.flatMap(sp => sp.terms))].length > 0 && <p className="mt-1 text-xs text-ink-faint">matched: {[...new Set(hit.spans.flatMap(sp => sp.terms))].join(', ')}</p>}
            <div className="mt-2 flex items-center gap-3">
              <a href={hit.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline">View source <ArrowUpRight size={11} /></a>
              <button onClick={() => onDetail(hit.article_id)} className="text-xs font-medium text-ink-muted hover:text-ink">Details</button>
            </div>
          </div>
        )) : <EmptyState text="No stored article matches this query and these filters." />}
      </CardContent>
    </Card>
  )
}

function EntitySection({ intelligence }: { intelligence: IntelligenceResponse }) {
  return (
    <Card>
      <CardHeader><div><CardEyebrow>Entities</CardEyebrow><CardTitle>What the evidence identifies</CardTitle></div><Badge>{intelligence.entities.length}</Badge></CardHeader>
      <CardContent>
        {intelligence.entities.length ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {intelligence.entities.map(entity => (
              <div key={entity.canonical_key} className="rounded-md border border-border p-3">
                <div className="mb-1 flex items-center gap-2"><Target size={14} className="text-accent" /><span className="text-xs text-ink-faint">{humanizeToken(entity.entity_type)}</span><StatusPill value={entity.verification_status} /></div>
                <h3 className="text-[13px] font-medium text-ink">{entity.canonical_name}</h3>
                <div className="mt-1.5 flex flex-wrap gap-1">{entity.mentions.map(m => <Badge key={m} variant="neutral">{m}</Badge>)}</div>
                {entity.evidence.map((item, i) => <EvidenceItem item={item} key={`${item.source_url}-${i}`} />)}
              </div>
            ))}
          </div>
        ) : <EmptyState text="No entities were extracted from the available evidence." />}
      </CardContent>
    </Card>
  )
}

function Timeline({ intelligence }: { intelligence: IntelligenceResponse }) {
  const events = new Map(intelligence.events.map(e => [e.event_key, e]))
  return (
    <Card>
      <CardHeader><div><CardEyebrow>Timeline</CardEyebrow><CardTitle>What changed, and when</CardTitle></div></CardHeader>
      <CardContent>
        {intelligence.timeline.length ? (
          <div className="space-y-4 border-l border-border pl-4">
            {intelligence.timeline.map(entry => {
              const event = events.get(entry.event_key); if (!event) return null
              return (
                <div key={entry.event_key} className="relative">
                  <div className="absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full border-2 border-surface bg-accent" />
                  <p className="text-xs text-ink-faint">{humanizeToken(event.event_type)} · {humanizeToken(entry.ordering_time_source)}</p>
                  <h3 className="text-[13px] font-medium text-ink">{event.title}</h3>
                  <p className="mt-0.5 text-xs text-ink-muted">{event.event_time ? `${humanizeToken(event.event_time_precision)} event time: ${formatDate(event.event_time)}` : `Event date unknown; ordered using ${formatDate(entry.ordering_time)}`}</p>
                  {event.evidence.map((item, i) => <EvidenceItem item={item} key={`${item.source_url}-${i}`} />)}
                </div>
              )
            })}
          </div>
        ) : <EmptyState text="No dated events are available." />}
      </CardContent>
    </Card>
  )
}

function Claims({ token, query, intelligence, claims, reload }: { token: string; query: string; intelligence?: IntelligenceResponse; claims: Claim[]; reload: () => void }) {
  const [text, setText] = useState(''); const [entityKey, setEntityKey] = useState(''); const [eventKey, setEventKey] = useState(''); const [error, setError] = useState('')
  const evidence = intelligence?.entities.find(i => i.canonical_key === entityKey)?.evidence || intelligence?.events.find(i => i.event_key === eventKey)?.evidence || []
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setError('')
    try { await createClaim(token, { query, claim_text: text, entity_key: entityKey || undefined, event_key: eventKey || undefined, evidence: evidence.slice(0, 1) }); setText(''); reload() }
    catch (err) { setError(err instanceof Error ? err.message : 'Claim could not be saved') }
  }
  async function review(claim: Claim, status: Claim['status']) { try { await updateClaimStatus(token, claim.claim_id, status); reload() } catch (err) { setError(err instanceof Error ? err.message : 'Claim could not be reviewed') } }
  return (
    <Card>
      <CardHeader><div><CardEyebrow>Claims / evidence</CardEyebrow><CardTitle>Reviewable intelligence</CardTitle></div><Badge>{claims.length}</Badge></CardHeader>
      <CardContent className="space-y-4">
        {intelligence && (
          <form onSubmit={submit} className="space-y-2 rounded-md border border-border p-3">
            <textarea value={text} onChange={e => setText(e.target.value)} placeholder="State only what the evidence supports…" required
              className="h-20 w-full rounded-md border border-border bg-surface p-2 text-[13px] text-ink placeholder:text-ink-faint focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/40" />
            <div className="flex flex-wrap gap-2">
              <Select value={entityKey} onChange={e => setEntityKey(e.target.value)}><option value="">Select entity</option>{intelligence.entities.map(i => <option value={i.canonical_key} key={i.canonical_key}>{i.canonical_name}</option>)}</Select>
              <Select value={eventKey} onChange={e => setEventKey(e.target.value)}><option value="">Select event</option>{intelligence.events.map(i => <option value={i.event_key} key={i.event_key}>{i.title}</option>)}</Select>
              <Button type="submit" variant="primary" size="sm" disabled={!text.trim() || !evidence.length}>Save unreviewed claim</Button>
            </div>
            {error && <p className="text-xs text-danger">{error}</p>}
          </form>
        )}
        {claims.length ? claims.map(claim => (
          <div key={claim.claim_id} className="flex items-start justify-between gap-3 rounded-md border border-border p-3">
            <div className="min-w-0">
              <StatusPill value={claim.status} />
              <p className="mt-1.5 text-[13px] text-ink">{claim.claim_text}</p>
              <p className="mt-1 text-xs text-ink-faint">{claim.entity_key || claim.event_key || 'Reference unavailable'} · {claim.evidence.length} source · {claim.provenance_status === 'UNRESOLVED' ? 'evidence no longer resolves to a stored article (legacy)' : claim.provenance_status === 'VERIFIED_IN_STORED_CORPUS' ? 'evidence verified in stored corpus' : 'not checked yet'}</p>
            </div>
            <div className="flex shrink-0 gap-1">
              {claim.status !== 'verified' && <Button size="icon" variant="ghost" title="Verify" onClick={() => review(claim, 'verified')}><Check size={14} /></Button>}
              {claim.status !== 'rejected' && <Button size="icon" variant="ghost" title="Reject" onClick={() => review(claim, 'rejected')}><X size={14} /></Button>}
            </div>
          </div>
        )) : <p className="text-[13px] text-ink-muted">No claims have been saved for this workspace.</p>}
      </CardContent>
    </Card>
  )
}

// ---- insights / opportunities / alerts: signal → impact → opportunity → action hierarchy ---------------------------
function InsightList({ items }: { items: Insight[] }) {
  return items.length ? (
    <div className="space-y-3">
      {items.map(item => (
        <div key={item.insight_id} className="rounded-md border border-border p-3">
          <div className="flex items-center gap-2 text-xs text-ink-faint"><Badge variant="accent">{Math.round(item.strength * 100)}% strength</Badge>{humanizeToken(item.insight_type)}{item.recency_days !== null && ` · ${item.recency_days}d before as-of`}</div>
          <h3 className="mt-1.5 text-[13px] font-medium text-ink">{item.title}</h3>
          <p className="mt-1 text-[13px] text-ink-muted">{item.explanation}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5"><StatusPill value={item.status || 'draft'} /><span className="text-xs text-ink-faint">{item.article_ids.length} article(s) · {item.event_keys.length} event(s)</span></div>
          {item.evidence.slice(0, 2).map((ev, i) => <EvidenceItem item={ev} key={`${ev.source_url}-${i}`} />)}
        </div>
      ))}
    </div>
  ) : <EmptyState text="No insight rule was triggered by the stored events." />
}

/** Signal → impact → opportunity → recommended action, as one card. */
function OpportunityCard({ item }: { item: Opportunity }) {
  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="mb-1 flex flex-wrap items-center gap-1.5"><StatusPill value={item.status} /><StatusPill value={item.capability_match} /></div>
            <h3 className="text-[14px] font-semibold text-ink">{item.title}</h3>
          </div>
          <Badge variant="accent" className="shrink-0">{Math.round(item.opportunity_score * 100)}%</Badge>
        </div>
        <ol className="space-y-2 border-l border-border pl-3 text-[13px]">
          <li><span className="font-medium text-ink-faint">Signal → </span><span className="text-ink-muted">{item.rationale}</span></li>
          <li><span className="font-medium text-ink-faint">Interpretation → </span><span className="text-ink-muted">{item.interpretation}</span></li>
          <li><span className="font-medium text-ink-faint">Recommended action ({humanStatus(item.review_status)}) → </span><span className="text-ink">{item.recommended_action}</span></li>
        </ol>
        {item.evidence.slice(0, 1).map((ev, i) => <EvidenceItem item={ev} key={`${ev.source_url}-${i}`} />)}
      </CardContent>
    </Card>
  )
}

function OpportunityList({ items }: { items: Opportunity[] }) {
  return items.length ? <div className="space-y-3">{items.map(item => <OpportunityCard item={item} key={item.opportunity_id} />)}</div>
    : <EmptyState text="No opportunity rule was triggered by the stored events." />
}

function AlertList({ items }: { items: Alert[] }) {
  return items.length ? (
    <div className="space-y-3">
      {items.map(item => (
        <div key={item.alert_id} className="rounded-md border border-border p-3">
          <div className="flex items-center gap-2 text-xs text-ink-faint"><Badge variant="warn">{humanizeToken(item.severity)}</Badge>{item.client_name} · as of {item.as_of ? formatDate(item.as_of) : 'unknown'}</div>
          <h3 className="mt-1.5 text-[13px] font-medium text-ink">{item.title}</h3>
          <p className="mt-1 text-[13px] text-ink-muted">{item.body}</p>
          {item.evidence.slice(0, 1).map((ev, i) => <EvidenceItem item={ev} key={`${ev.source_url}-${i}`} />)}
        </div>
      ))}
    </div>
  ) : <EmptyState text="No alert rule fired inside the alert window." />
}

function DerivedSections({ intelligence }: { intelligence: IntelligenceResponse }) {
  return (
    <>
      <Card><CardHeader><div><CardEyebrow>Insights</CardEyebrow><CardTitle>Patterns across stored events</CardTitle></div><Badge>{(intelligence.insights || []).length}</Badge></CardHeader><CardContent><InsightList items={intelligence.insights || []} /></CardContent></Card>
      <Card><CardHeader><div><CardEyebrow>Opportunities</CardEyebrow><CardTitle>Evidence-backed openings</CardTitle></div><Badge>{(intelligence.opportunities || []).length}</Badge></CardHeader><CardContent><OpportunityList items={intelligence.opportunities || []} /></CardContent></Card>
      <Card><CardHeader><div><CardEyebrow>Alerts</CardEyebrow><CardTitle>Rule-based alerts</CardTitle></div><Badge>{(intelligence.alerts || []).length}</Badge></CardHeader><CardContent><AlertList items={intelligence.alerts || []} /></CardContent></Card>
    </>
  )
}

function ClientBriefing({ token }: { token: string }) {
  const [clients, setClients] = useState<string[]>([]); const [client, setClient] = useState(''); const [briefing, setBriefing] = useState<Briefing>()
  const [state, setState] = useState<'idle' | 'loading' | 'error'>('idle'); const [error, setError] = useState('')
  useEffect(() => { listClients(token).then(list => { setClients(list); if (list.length) setClient(list[0]) }).catch(() => setClients([])) }, [token])
  async function load(event: React.FormEvent) { event.preventDefault(); if (!client) return; setState('loading'); setError(''); try { setBriefing(await getClientBriefing(token, client)); setState('idle') } catch (err) { setState('error'); setError(err instanceof Error ? err.message : 'Briefing failed') } }
  return (
    <div className="space-y-4">
      <form onSubmit={load} className="flex flex-wrap items-center gap-2">
        <Select value={client} onChange={e => setClient(e.target.value)}>{clients.map(name => <option key={name} value={name}>{name}</option>)}</Select>
        <Button type="submit" variant="primary" disabled={!client || state === 'loading'}>{state === 'loading' ? 'Building…' : 'Build client briefing'}</Button>
      </form>
      {state === 'error' && <ErrorState message={error} />}
      {briefing && (
        <>
          <Card>
            <CardHeader><div><CardEyebrow>Briefing · {briefing.scope.client || briefing.scope.query}</CardEyebrow><CardTitle>Summary</CardTitle></div><Badge>as of {briefing.as_of ? formatDate(briefing.as_of) : 'unknown'}</Badge></CardHeader>
            <CardContent><p className="text-[13px] text-ink">{briefing.summary}</p><ul className="mt-2 list-inside list-disc text-xs text-ink-faint">{briefing.limitations.map(item => <li key={item}>{item}</li>)}</ul></CardContent>
          </Card>
          <Card>
            <CardHeader><div><CardEyebrow>Key events</CardEyebrow><CardTitle>Latest stated events</CardTitle></div><Badge>{briefing.key_events.length}</Badge></CardHeader>
            <CardContent>{briefing.key_events.length ? (
              <div className="space-y-3 border-l border-border pl-4">
                {briefing.key_events.map((ev, index) => (
                  <div key={ev.event_key}>
                    <p className="text-xs text-ink-faint">{humanizeToken(ev.event_type)} · {ev.when ? formatDate(ev.when) : 'date unknown'} ({humanizeToken(ev.when_source)}) · {ev.publishers.join(', ')}</p>
                    <h3 className="text-[13px] font-medium text-ink">{ev.title}</h3>
                    {ev.evidence.slice(0, 1).map((item, i) => <EvidenceItem item={item} key={`${item.source_url}-${i}`} />)}
                  </div>
                ))}
              </div>
            ) : <EmptyState text="No events were stated in the stored articles for this client." />}</CardContent>
          </Card>
          <Card><CardHeader><div><CardEyebrow>Insights</CardEyebrow><CardTitle>Patterns</CardTitle></div><Badge>{briefing.insights.length}</Badge></CardHeader><CardContent><InsightList items={briefing.insights} /></CardContent></Card>
          <Card><CardHeader><div><CardEyebrow>Opportunities</CardEyebrow><CardTitle>Evidence-backed openings</CardTitle></div><Badge>{briefing.opportunities.length}</Badge></CardHeader><CardContent><OpportunityList items={briefing.opportunities} /></CardContent></Card>
          <Card><CardHeader><div><CardEyebrow>Alerts</CardEyebrow><CardTitle>Rule-based alerts</CardTitle></div><Badge>{briefing.alerts.length}</Badge></CardHeader><CardContent><AlertList items={briefing.alerts} /></CardContent></Card>
          <Card><CardHeader><div><CardEyebrow>Sources</CardEyebrow><CardTitle>Provenance</CardTitle></div></CardHeader><CardContent><div className="flex flex-wrap gap-1.5">{briefing.sources.map((src, i) => <Badge key={i}>{src.publisher || (src.source_class && humanizeToken(src.source_class)) || 'Unknown source'}: {src.articles}</Badge>)}</div></CardContent></Card>
        </>
      )}
    </div>
  )
}

// ---- shell -----------------------------------------------------------------------------------------------------
type Tab = 'today' | 'clients' | 'competitors' | 'technology' | 'industry' | 'research' | 'opportunities' | 'saved' | 'sources' | 'factcheck'
const NAV: { id: Tab; label: string; icon: typeof LayoutGrid }[] = [
  { id: 'today', label: "Today's Intelligence", icon: Newspaper },
  { id: 'clients', label: 'Clients', icon: Users },
  { id: 'competitors', label: 'Competitors', icon: Target },
  { id: 'technology', label: 'AI & Technology', icon: BarChart3 },
  { id: 'industry', label: 'Industry', icon: LayoutGrid },
  { id: 'research', label: 'Research', icon: Search },
  { id: 'opportunities', label: 'Opportunities', icon: ArrowUpRight },
  { id: 'saved', label: 'Saved articles', icon: Bookmark },
  { id: 'sources', label: 'Sources / Evidence', icon: Newspaper },
  { id: 'factcheck', label: 'News Detector', icon: ShieldCheck },
]

export function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem('xenom_token') || '')
  const [user, setUser] = useState(() => sessionStorage.getItem('xenom_user') || '')
  const [feed, setFeed] = useState<Article[]>([])
  const [articlesPhase, setArticlesPhase] = useState<Phase>('loading')
  const [searchData, setSearchData] = useState<EvidenceSearch>()
  const [detailId, setDetailId] = useState('')
  const [role, setRole] = useState(() => sessionStorage.getItem('xenom_role') || '')
  const [intelligence, setIntelligence] = useState<IntelligenceResponse>()
  const [claims, setClaims] = useState<Claim[]>([])
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState('')
  const [tab, setTab] = useState<Tab>('today')
  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState<SearchOptions>({ mode: 'lexical' })
  const [clientOptions, setClientOptions] = useState<string[]>([])
  const [summary, setSummary] = useState<ClientSummaryView>()
  const [pipeline, setPipeline] = useState<PipelineStatus>()
  const [theme, setTheme] = useState<'light' | 'dark'>(() => (localStorage.getItem('xenom_theme') as 'light' | 'dark' | null) || 'light')
  const [savedIds, setSavedIds] = useState<Set<string>>(new Set())
  const [moreIntelligenceCount, setMoreIntelligenceCount] = useState(16)
  function toggleSaved(id: string, next: boolean) {
    setSavedIds(prev => { const copy = new Set(prev); if (next) copy.add(id); else copy.delete(id); return copy })
  }

  const moreIntelligenceArticles = feed.slice(5, 5 + moreIntelligenceCount)
  const hasMoreIntelligence = feed.length > 5 + moreIntelligenceCount

  useEffect(() => { localStorage.setItem('xenom_theme', theme); document.documentElement.setAttribute('data-theme', theme) }, [theme])

  async function loadWorkspace(nextToken: string, nextRole: string) {
    try {
      const [savedClaims, feed_, summary_, pipeline_, savedArticles] = await Promise.all([nextRole === 'ADMIN' ? listClaims(nextToken) : Promise.resolve([]), getFeed(nextToken, 30), getClientSummary(nextToken), getPipelineStatus(nextToken), listSavedArticles(nextToken).catch(() => [])])
      setClaims(savedClaims); setFeed(feed_); setSummary(summary_); setPipeline(pipeline_); setSavedIds(new Set(savedArticles.map(a => a.id))); setArticlesPhase('success')
    } catch (err) { setError(err instanceof Error ? err.message : 'Failed to load workspace'); setArticlesPhase('error') }
  }
  function signedIn(nextToken: string, name: string, nextRole: string) {
    sessionStorage.setItem('xenom_token', nextToken); sessionStorage.setItem('xenom_user', name); sessionStorage.setItem('xenom_role', nextRole)
    setToken(nextToken); setUser(name); setRole(nextRole)
  }
  useEffect(() => {
    if (!token) return
    getCurrentUser(token).then(current => {
      setUser(current.name || current.email); setRole(current.role)
      return Promise.all([loadWorkspace(token, current.role), listClients(token).then(setClientOptions)])
    }).catch(() => logout())
  }, [token])

  async function runResearch(event: React.FormEvent) {
    event.preventDefault(); const clean = query.trim(); if (!clean) return
    setPhase('loading'); setError(''); setSearchData(undefined); setIntelligence(undefined)
    try {
      const [searchResult, intelligenceResult] = await Promise.all([searchEvidence(token, clean, filters), extractIntelligence(token, clean)])
      setSearchData(searchResult); setIntelligence(intelligenceResult); setPhase('success')
    } catch (err) { setPhase('error'); setError(err instanceof Error ? err.message : 'Research request failed') }
  }

  function logout() { if (token) void apiLogout(token).catch(() => undefined); sessionStorage.clear(); setToken(''); setUser(''); setRole('') }
  const isAdmin = role === 'ADMIN'
  const visibleNav = isAdmin ? NAV : NAV.filter(item => item.id !== 'research')
  if (!token) return <Login onLogin={signedIn} />

  return (
    <div className="flex min-h-screen bg-bg text-ink">
      <CopilotPanel token={token} name="Xenom" />
      <aside className="hidden w-56 shrink-0 flex-col border-r border-border bg-surface px-4 py-5 lg:flex">
        <div className="mb-6 text-[15px] font-semibold tracking-tight">XENOM <span className="font-normal text-ink-faint">Intelligence</span></div>
        <nav className="flex-1 space-y-0.5">
          {visibleNav.map(n => (
            <button key={n.id} onClick={() => setTab(n.id)}
              className={cn('flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-[13px] font-medium transition-colors',
                tab === n.id ? 'bg-accent-soft text-accent' : 'text-ink-muted hover:bg-surface-2 hover:text-ink')}>
              <n.icon size={15} /> {n.label}
            </button>
          ))}
        </nav>
        <div className="mt-4 border-t border-border pt-4">
          <p className="truncate text-[13px] font-medium text-ink">{user}</p>
          <p className="text-xs text-ink-faint">{isAdmin ? 'Administrator' : role ? role.charAt(0) + role.slice(1).toLowerCase() : 'Signed in'}</p>
          <Button variant="ghost" size="sm" className="mt-2 w-full justify-start px-0" onClick={logout}><LogOut size={13} /> Sign out</Button>
        </div>
      </aside>

      <main className="min-w-0 flex-1">
        <header className="flex items-center justify-between border-b border-border bg-surface px-4 py-3 lg:px-8">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">XENOM Intelligence</p>
            <h1 className="text-[17px] font-semibold text-ink">Real-time signals</h1>
          </div>
          <Button variant="outline" size="sm" onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}>
            {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />} {theme === 'dark' ? 'Light' : 'Dark'}
          </Button>
        </header>

        {/* mobile tab bar */}
        <nav className="flex gap-1 overflow-x-auto border-b border-border bg-surface px-3 py-1.5 lg:hidden">
          {visibleNav.map(n => <button key={n.id} onClick={() => setTab(n.id)} className={cn('shrink-0 rounded-md px-2.5 py-1.5 text-xs font-medium', tab === n.id ? 'bg-accent-soft text-accent' : 'text-ink-muted')}>{n.label}</button>)}
        </nav>

        <div className="mx-auto max-w-[1400px] space-y-6 px-4 py-6 lg:px-8">
          {tab === 'today' && (
            <section className="space-y-8">
              {articlesPhase === 'loading' && <LoadingState label="Loading signals…" />}
              {articlesPhase === 'error' && <ErrorState message={error || 'Unknown error'} />}
              {articlesPhase === 'success' && (summary || pipeline) && (
                <KpiRow>
                  {summary && <KpiStat label="Accepted signals" value={summary.total_accepted} sub={`${summary.total_rejected} filtered out`} icon={CheckCircle2} tone="accent" />}
                  {summary && <KpiStat label="Clients tracked" value={summary.clients.length} sub={summary.clients.map(c => c.client).join(', ')} icon={Users2} />}
                  {pipeline && <KpiStat label="Sources active" value={`${pipeline.sources_active} / ${pipeline.sources_total}`} sub={pipeline.scheduler.enabled ? `Live · every ${pipeline.scheduler.interval_minutes}m` : 'Scheduler off'} icon={Radio} tone={pipeline.scheduler.enabled ? 'accent' : 'warn'} />}
                  {pipeline && <KpiStat label="Latest ingest" value={pipeline.hours_since_latest_ingest != null ? `${pipeline.hours_since_latest_ingest}h ago` : '—'} sub={`${pipeline.accepted_total} accepted total`} />}
                </KpiRow>
              )}
              {articlesPhase === 'success' && feed.length > 0 && (
                <>
                  <div><p className="text-xs font-medium uppercase tracking-wide text-ink-faint">Today's Intelligence</p><h2 className="text-lg font-semibold text-ink">The latest from across your sectors</h2></div>
                  <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.4fr_1fr_0.8fr]">
                    <HeroArticle article={feed[0]} />
                    <div><SectionEyebrow>Breaking news</SectionEyebrow>{feed.slice(1, 5).map(a => <ListArticle key={a.id} article={a} />)}</div>
                    <div><SectionEyebrow>Trending</SectionEyebrow>{feed.slice(0, 4).map(a => <SidebarCard key={a.id} article={a} />)}</div>
                  </div>
                  {feed.length > 5 && (
                    <div className="border-t border-border pt-6">
                      <SectionEyebrow>More intelligence</SectionEyebrow>
                      <NewsGrid articles={moreIntelligenceArticles} token={token} savedIds={savedIds} onToggleSaved={toggleSaved} />
                      {hasMoreIntelligence && (
                        <div className="mt-4 flex justify-center">
                          <Button type="button" variant="outline" onClick={() => setMoreIntelligenceCount(count => Math.min(count + 8, Math.max(0, feed.length - 5)))}>
                            Know more
                          </Button>
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
              {articlesPhase === 'success' && feed.length === 0 && <EmptyState text="No verified intelligence detected in current sources." />}
              <div className="border-t border-border pt-6"><MorningDigestPanel token={token} /></div>
            </section>
          )}

          {tab === 'clients' && <ClientsPanel token={token} isAdmin={isAdmin} renderArticles={list => <NewsGrid articles={list} token={token} savedIds={savedIds} onToggleSaved={toggleSaved} />} />}
          {tab === 'competitors' && <CompetitorsPanel token={token} />}
          {tab === 'technology' && <TechnologyView token={token} />}
          {tab === 'industry' && <IndustryPanel token={token} />}

          {tab === 'research' && (
            <section className="space-y-4">
              <h2 className="text-lg font-semibold text-ink">Intelligence Research</h2>
              <CapabilityStrip token={token} />
              <ResearchStatus token={token} />
              <ResearchersPanel token={token} isAdmin={isAdmin} />
              <div className="flex items-start gap-2 rounded-md border border-border bg-surface-2 px-3 py-2.5 text-xs text-ink-muted">
                <AlertCircle size={14} className="mt-0.5 shrink-0" />
                <span>Search note: results are searches over the stored corpus, each with its source, tier, why it matched and the stored relevance decision. A client named in the query is suggested as a filter, never applied silently.</span>
              </div>
              <form onSubmit={runResearch} className="flex flex-wrap items-center gap-2">
                <div className="relative flex-1 min-w-[220px]"><Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint" /><Input className="pl-8" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search for specific intelligence…" /></div>
                <Button type="submit" variant="primary" disabled={phase === 'loading'}>{phase === 'loading' ? 'Searching…' : 'Research'}</Button>
              </form>
              <div className="flex flex-wrap gap-2">
                <Select value={filters.entity_key || ''} onChange={e => setFilters({ ...filters, entity_key: e.target.value || undefined })}><option value="">All clients</option>{clientOptions.map(c => <option key={c} value={c.toLowerCase()}>{c}</option>)}</Select>
                <Select value={filters.source_class || ''} onChange={e => setFilters({ ...filters, source_class: e.target.value || undefined })}><option value="">Any source class</option>{['official', 'technology', 'trusted_news', 'news', 'directory', 'reference', 'research', 'forum'].map(c => <option key={c} value={c}>{c.replace('_', ' ')}</option>)}</Select>
                <Select value={filters.tier || ''} onChange={e => setFilters({ ...filters, tier: e.target.value || undefined })}><option value="">Any tier</option>{['TIER_1', 'TIER_2', 'TIER_3', 'TIER_4'].map(t => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}</Select>
                <Select value={filters.mode} onChange={e => setFilters({ ...filters, mode: e.target.value as SearchOptions['mode'] })} aria-label="Retrieval mode"><option value="lexical">Keyword</option><option value="semantic">Semantic</option><option value="hybrid">Hybrid</option></Select>
                <Select value={filters.label || ''} onChange={e => setFilters({ ...filters, label: e.target.value || undefined })} aria-label="Taxonomy label"><option value="">Any label</option>{['company', 'competitor', 'industry', 'technology', 'ai_model', 'research', 'opportunity', 'partnership', 'product_business_movement', 'regulatory_policy', 'market_financial_signal'].map(l => <option key={l} value={l}>{l.replace(/_/g, ' ')}</option>)}</Select>
                <Input className="w-32" value={filters.person || ''} onChange={e => setFilters({ ...filters, person: e.target.value || undefined })} placeholder="Person" />
                <Input className="w-36" value={filters.technology || ''} onChange={e => setFilters({ ...filters, technology: e.target.value || undefined })} placeholder="Technology" />
                <Input type="date" className="w-36" value={filters.date_from || ''} onChange={e => setFilters({ ...filters, date_from: e.target.value || undefined })} />
                {searchData?.suggested_filters?.client && !filters.entity_key && searchData.suggested_filters.client.map(c => (
                  <Button key={c} size="sm" variant="subtle" onClick={() => setFilters({ ...filters, entity_key: c })}>Filter to {c.toUpperCase()}? (named in query)</Button>
                ))}
              </div>
              {phase === 'loading' && <LoadingState label="Extracting intelligence…" />}
              {phase === 'error' && <ErrorState message={error} />}
              {phase === 'success' && searchData && intelligence && (
                <div className="space-y-4">
                  <EvidenceResults data={searchData} onDetail={setDetailId} />
                  {detailId && <ArticleDetailPanel token={token} id={detailId} onClose={() => setDetailId('')} />}
                  <EntitySection intelligence={intelligence} />
                  <Timeline intelligence={intelligence} />
                  <DerivedSections intelligence={intelligence} />
                  <Claims token={token} query={query} intelligence={intelligence} claims={claims} reload={() => listClaims(token).then(setClaims)} />
                </div>
              )}
            </section>
          )}

          {tab === 'opportunities' && (
            <section className="space-y-4">
              <div><h2 className="text-lg font-semibold text-ink">Client Briefing &amp; Opportunities</h2>
                <p className="mt-1 text-[13px] text-ink-muted">Deterministic insights, opportunities, alerts and briefings derived from stored articles. Every item links to its events and verbatim evidence; opportunities are rule-based readings of stated events, not capability matches.</p></div>
              <ClientBriefing token={token} />
              <RecommendationsPanel token={token} isAdmin={isAdmin} />
            </section>
          )}

          {tab === 'saved' && <SavedArticlesPanel token={token} />}

          {tab === 'sources' && <SourcesPanel token={token} />}
          {tab === 'factcheck' && <NewsDetectorPanel token={token} />}
        </div>
      </main>
    </div>
  )
}
