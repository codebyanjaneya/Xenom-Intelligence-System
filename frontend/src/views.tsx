import { useEffect, useRef, useState, type ReactNode } from 'react'
import { type ColumnDef } from '@tanstack/react-table'
import { ArrowUpRight, ExternalLink, Plus, Search, X } from 'lucide-react'
import {
  type Article, type ClientSummaryView, type CompetitorEntry, type CompetitorView, type IndustryView, type MorningDigest, type PipelineStatus, type ResearchRecord, type SignalRef,
  type ResearchView, type SourceStatusReport, type ThemeView, getArticles, getDigest, getResearch, getClientSummary, getCompetitors, getIndustry, getPipelineStatus,
  getSourceStatus, getTechnology, listClients, onboardClient, suggestClients, type ClientSuggestion,
} from './api'
import { formatDate, humanizeToken, humanBasis, competitorOf } from './format'
import { StatusPill, ConfidencePill, EvidenceRow, SectionEyebrow } from './components/evidence'
import { Card, CardContent, CardHeader, CardTitle, CardEyebrow } from './components/ui/card'
import { Badge } from './components/ui/badge'
import { Input, Select } from './components/ui/input'
import { Button } from './components/ui/button'
import { DataState, EmptyState, LoadingState, ErrorState } from './components/ui/states'
import { DataTable } from './components/ui/data-table'
import { Sheet, SheetHeader, SheetBody } from './components/ui/sheet'
import { checkNews, type FactCheckResult } from './architecture'

const DIMENSION_LABELS: Record<string, string> = {
  product_technology: 'Product / technology', ai_adoption: 'AI adoption', digital_capability: 'Digital capability',
  market_activity: 'Market / segment activity', partnerships: 'Partnerships', strategic_moves: 'Strategic moves',
}

function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T>()
  const [error, setError] = useState('')
  useEffect(() => { let live = true; load().then(d => { if (live) setData(d) }).catch(e => { if (live) setError(e instanceof Error ? e.message : 'Request failed') }); return () => { live = false } }, deps) // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error }
}

function Signal({ item }: { item: SignalRef }) {
  return (
    <div className="rounded-md border border-border p-3">
      <p className="text-xs text-ink-faint">{item.clients.join(', ') || 'No linked client'} · {item.publisher || item.source} · {formatDate(item.published_at)}</p>
      <h3 className="mt-0.5 text-[13px] font-medium text-ink">{item.title}</h3>
      <p className="mt-1 text-[13px] text-ink-muted">{item.evidence.quote}</p>
      <p className="mt-1 text-xs text-ink-faint">Matched: {item.matched_terms.join(', ')}</p>
      <a href={item.url} target="_blank" rel="noreferrer" className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline">View source <ExternalLink size={11} /></a>
    </div>
  )
}

// ---- Morning digest ---------------------------------------------------------------------------------------------
export function MorningDigestPanel({ token }: { token: string }) {
  const { data, error } = useLoad<MorningDigest>(() => getDigest(token), [token])
  return <DataState error={error} ready={!!data}>{data && (
    <Card>
      <CardHeader><div><CardEyebrow>Morning digest</CardEyebrow><CardTitle>Embodied AI, robotics, creative AI &amp; model signals</CardTitle></div><Badge>{formatDate(data.generated_at)}</Badge></CardHeader>
      <CardContent className="space-y-5">
        <p className="text-[13px] text-ink-muted">{data.note}</p>
        <div>
          <SectionEyebrow>Worth your attention</SectionEyebrow>
          {data.worth_your_attention.length ? (
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
              {data.worth_your_attention.map((item, i) => (
                <a key={`${item.url}-${i}`} href={item.url} target="_blank" rel="noreferrer" className="block rounded-md border border-border p-3 transition-colors hover:border-accent/40">
                  <div className="flex items-center gap-1.5 text-xs text-ink-faint"><Badge variant="accent">{i + 1}</Badge>{item.source || 'Unknown source'} · {formatDate(item.published_at)}</div>
                  <h3 className="mt-1.5 line-clamp-2 text-[13px] font-medium text-ink">{item.title || item.url}</h3>
                  {item.evidence?.quote && <p className="mt-1 line-clamp-2 text-xs text-ink-muted">{item.evidence.quote}</p>}
                </a>
              ))}
            </div>
          ) : <EmptyState text="No supporting articles found in the current corpus yet." />}
        </div>
        <div>
          <SectionEyebrow>By theme</SectionEyebrow>
          <div className="flex flex-wrap gap-1.5">{data.sections.map(s => <Badge key={s.key} variant={s.article_count ? 'accent' : 'neutral'}>{s.label}: {s.article_count || 'no evidence yet'}</Badge>)}</div>
        </div>
      </CardContent>
    </Card>
  )}</DataState>
}

// ---- Technology / Industry / Research (kept as evidenced-theme lists, restyled) ------------------------------------
export function TechnologyView({ token }: { token: string }) {
  const { data, error } = useLoad<ThemeView>(() => getTechnology(token), [token])
  return <DataState error={error} ready={!!data}>{data && (
    <div className="space-y-4">
      <div className="flex items-center justify-between"><h2 className="text-lg font-semibold text-ink">AI &amp; Technology Watch</h2><Badge>{data.themes.filter(t => t.article_count).length} of {data.themes.length} themes evidenced</Badge></div>
      <p className="text-[13px] text-ink-muted">{data.scope}. {data.note}</p>
      {data.themes.filter(t => t.article_count).map(theme => (
        <Card key={theme.key}>
          <CardHeader><div><CardTitle>{theme.label}</CardTitle><p className="mt-1 flex flex-wrap gap-1.5">{Object.entries(theme.by_client).map(([c, n]) => <Badge key={c}>{c}: {n}</Badge>)}</p></div><Badge>{theme.article_count} articles</Badge></CardHeader>
          <CardContent className="space-y-2">{theme.signals.map(s => <Signal key={s.article_id} item={s} />)}</CardContent>
        </Card>
      ))}
      <Card>
        <CardHeader><div><CardEyebrow>Unavailable</CardEyebrow><CardTitle>No stored evidence in the current corpus</CardTitle></div><Badge>{data.themes_without_evidence.length}</Badge></CardHeader>
        <CardContent className="space-y-2">
          <p className="text-[13px] text-ink-muted">These themes are tracked, but no stored article uses their terms. This is missing data, not a finding that nothing is happening.</p>
          {data.themes.filter(t => !t.article_count).map(t => (
            <div key={t.key} className="rounded-md border border-border p-2.5">
              <p className="text-xs font-medium text-ink-faint">{t.label} · No supporting evidence found yet</p>
              <p className="mt-1 text-xs text-ink-muted">{t.discovery.configured ? `Discovery: ${t.discovery.queries} queries (${t.discovery.phrase} per client) every ${t.discovery.cadence_minutes ?? '—'} min via Google News RSS. Status: ${humanizeToken(t.discovery.status)}${t.discovery.last_attempt_at ? `, last attempt ${formatDate(t.discovery.last_attempt_at)}` : ''}.` : (t.discovery.reason || 'This has not been configured yet.')}</p>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  )}</DataState>
}

export function IndustryPanel({ token }: { token: string }) {
  const { data, error } = useLoad<IndustryView>(() => getIndustry(token), [token])
  return <DataState error={error} ready={!!data}>{data && (
    <div className="space-y-4">
      <div className="flex items-center justify-between"><h2 className="text-lg font-semibold text-ink">Industry Intelligence</h2><Badge>{data.registered_industries.length} registered industries</Badge></div>
      <p className="text-[13px] text-ink-muted">{data.scope}. {data.note}</p>
      <Card>
        <CardHeader><div><CardTitle>Registered industries</CardTitle></div><Badge>{data.industry_entities.filter(e => e.article_count).length} of {data.industry_entities.length} evidenced</Badge></CardHeader>
        <CardContent className="space-y-3">
          {data.industry_entities.map(e => (
            <div key={e.industry}>
              <p className="text-xs font-medium text-ink-faint">{humanizeToken(e.industry)} · {e.article_count ? `${e.article_count} articles` : 'No supporting evidence found yet'} {Object.entries(e.by_client).map(([c, n]) => `${c} ${n}`).join(' · ')}</p>
              {e.signals.length > 0 && <div className="mt-2 space-y-2">{e.signals.map(s => <Signal key={s.article_id} item={s} />)}</div>}
            </div>
          ))}
        </CardContent>
      </Card>
      <p className="text-xs text-ink-faint">Topic coverage: {data.topic_coverage.stored_labelled} of {data.topic_coverage.accepted_articles} accepted articles have a stored topic. {data.topic_coverage.explanation}</p>
      {data.clients.map(c => (
        <Card key={c.client}>
          <CardHeader><div><CardTitle>{c.client}</CardTitle></div><Badge>{c.accepted_articles} accepted articles</Badge></CardHeader>
          <CardContent className="space-y-3">
            {c.industry_terms.filter(t => t.article_count).length ? c.industry_terms.filter(t => t.article_count).map(t => (
              <div key={t.term}>
                <p className="text-xs font-medium text-ink-faint">{humanizeToken(t.term)} · {t.article_count} articles</p>
                <div className="mt-2 space-y-2">{t.signals.map(s => <Signal key={s.article_id} item={s} />)}</div>
              </div>
            )) : <p className="text-[13px] text-ink-muted">No configured industry term appears in this client's stored articles.</p>}
          </CardContent>
        </Card>
      ))}
    </div>
  )}</DataState>
}

// ---- Competitors: comparison table + detail drawer ------------------------------------------------------------------
function DimensionCell({ label, ev }: { label: string; ev: import('./api').DimensionEvidence }) {
  if (!ev) return <span className="text-xs text-ink-faint">Not enough evidence yet</span>
  return <div><p className="line-clamp-2 text-xs text-ink-muted">{ev.quote}</p><p className="mt-0.5 text-[11px] text-ink-faint">{ev.source ?? label} · {formatDate(ev.date)}</p></div>
}

function CompetitorDrawer({ c, onClose }: { c: CompetitorEntry; onClose: () => void }) {
  const basis = humanBasis(c.profile.basis)
  return (
    <Sheet open onOpenChange={v => !v && onClose()}>
      <SheetHeader title={c.competitor} subtitle={competitorOf(c.competitor_of)} onClose={onClose} />
      <SheetBody className="space-y-5">
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusPill value={c.profile.relationship_status} />
          <ConfidencePill value={c.profile.confidence} />
          <Badge>{c.article_count} article{c.article_count === 1 ? '' : 's'}</Badge>
        </div>
        {c.profile.domain_industry && c.profile.domain_industry !== 'needs_review' && <p className="text-[13px] text-ink-muted">Industry: {humanizeToken(c.profile.domain_industry)}</p>}
        {basis && <p className="text-xs text-ink-faint">Why we track this: {basis}</p>}

        <div>
          <SectionEyebrow>Latest developments</SectionEyebrow>
          {c.developments.length ? (
            <div className="space-y-2">{c.developments.slice(0, 6).map((d, i) => (
              <div key={`${d.url}-${i}`} className="rounded-md border border-border p-2.5">
                <div className="flex items-center justify-between"><Badge variant="accent">{humanizeToken(d.type)}</Badge><ConfidencePill value={d.confidence} /></div>
                <p className="mt-1.5 text-xs text-ink-faint">{d.source} · {formatDate(d.published_at)}</p>
                <p className="mt-1 text-[13px] text-ink-muted">{d.quote}</p>
                <a href={d.url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline">View source <ArrowUpRight size={11} /></a>
              </div>
            ))}</div>
          ) : <EmptyState text="No supporting articles found in the current corpus." />}
        </div>

        <div>
          <SectionEyebrow>How they compare to the client</SectionEyebrow>
          <div className="space-y-2">
            {Object.entries(c.comparison).map(([dim, row]) => (
              <div key={dim} className="rounded-md border border-border p-2.5">
                <div className="mb-1.5 flex items-center justify-between"><p className="text-xs font-medium text-ink-faint">{DIMENSION_LABELS[dim] ?? humanizeToken(dim)}</p><StatusPill value={row.status} /></div>
                <div className="grid grid-cols-2 gap-3"><div><p className="mb-1 text-[11px] font-medium text-ink-faint">Client</p><DimensionCell label="Client" ev={row.client_evidence} /></div><div><p className="mb-1 text-[11px] font-medium text-ink-faint">Competitor</p><DimensionCell label="Competitor" ev={row.competitor_evidence} /></div></div>
              </div>
            ))}
          </div>
        </div>

        <div>
          <SectionEyebrow>Impact on the client</SectionEyebrow>
          {c.impact.length ? c.impact.map((im, i) => (
            <div key={i} className="mb-2 rounded-md border border-border p-2.5">
              <p className="text-[13px] font-medium text-ink">{im.development.title}</p>
              <p className="text-xs text-ink-faint">{formatDate(im.development.date)} · {im.development.source}</p>
              <p className="mt-1.5 text-[13px] text-ink-muted"><strong className="text-ink">Why this matters:</strong> {im.why_it_matters}</p>
              <p className="mt-1 text-[13px] text-ink-muted"><strong className="text-ink">Potential risk:</strong> {im.potential_risk}</p>
              <p className="mt-1 text-[13px] text-ink-muted"><strong className="text-ink">Potential opportunity</strong> ({humanizeToken(im.potential_opportunity.status)}, {humanizeToken(im.potential_opportunity.capability_match)}): {im.potential_opportunity.text}</p>
              <p className="mt-1 text-[13px] text-ink-muted"><strong className="text-ink">Recommended review</strong> ({humanizeToken(im.recommended_action.review_status)}): {im.recommended_action.text}</p>
            </div>
          )) : <EmptyState text="No supporting articles found in the current corpus to assess client impact yet." />}
        </div>
      </SheetBody>
    </Sheet>
  )
}

export function CompetitorsPanel({ token }: { token: string }) {
  const [clients, setClients] = useState<string[]>([]); const [client, setClient] = useState(''); const [open, setOpen] = useState<CompetitorEntry>()
  useEffect(() => { listClients(token).then(list => { setClients(list); if (list.length) setClient(list[0]) }).catch(() => setClients([])) }, [token])
  const { data, error } = useLoad<CompetitorView>(() => getCompetitors(token, client || undefined), [token, client])

  const columns: ColumnDef<CompetitorEntry, any>[] = [
    { accessorKey: 'competitor', header: 'Competitor', cell: ({ row }) => <span className="font-medium text-ink">{row.original.competitor}</span> },
    { id: 'of', header: 'Relation', cell: ({ row }) => <span className="text-xs text-ink-muted">{competitorOf(row.original.competitor_of)}</span> },
    { accessorFn: r => r.profile.relationship_status, id: 'status', header: 'Status', cell: ({ row }) => <StatusPill value={row.original.profile.relationship_status} /> },
    { accessorFn: r => r.profile.confidence ?? -1, id: 'confidence', header: 'Confidence', cell: ({ row }) => <ConfidencePill value={row.original.profile.confidence} /> },
    { accessorKey: 'article_count', header: 'Articles', cell: ({ row }) => row.original.article_count },
    { id: 'developments', header: 'Recent developments', cell: ({ row }) => <span className="text-xs text-ink-muted">{row.original.developments.length ? `${row.original.developments.length} tracked` : 'None yet'}</span> },
  ]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-ink">Competitor Intelligence</h2>
        <Select value={client} onChange={e => setClient(e.target.value)} aria-label="Client"><option value="">All clients</option>{clients.map(c => <option key={c}>{c}</option>)}</Select>
      </div>
      <DataState error={error} ready={!!data}>{data && (
        data.configured ? (
          <DataTable columns={columns} data={data.competitors} searchPlaceholder="Search competitors…" emptyText="No competitor is configured yet." onRowClick={setOpen} />
        ) : (
          <Card><CardContent className="space-y-1.5">
            <p className="text-[13px] text-ink-muted">{data.reason}</p>
            <p className="text-xs text-ink-faint">{data.how_to_configure}</p>
            <p className="text-xs text-ink-faint">{data.unverified_competitor_language_articles} stored articles use competitor-style wording in the keyword classifier; this is not a verified competitor mapping and is not shown as competitor intelligence.</p>
          </CardContent></Card>
        )
      )}</DataState>
      {open && <CompetitorDrawer c={open} onClose={() => setOpen(undefined)} />}
    </div>
  )
}

// ---- Research dashboard: trending models + papers, real metrics only, never fabricated ------------------------------
function titleCell(r: ResearchRecord) {
  return (
    <a href={r.url} target="_blank" rel="noreferrer" className="group block">
      <span className="text-[13px] font-medium text-ink group-hover:text-accent">{r.title}</span>
      <span className="ml-1.5 inline-flex items-center text-ink-faint"><ExternalLink size={10} /></span>
    </a>
  )
}

function modelColumns(): ColumnDef<ResearchRecord, any>[] {
  return [
    { id: 'title', header: 'Model / project', cell: ({ row }) => titleCell(row.original) },
    { id: 'task', header: 'Task', cell: ({ row }) => row.original.metrics.pipeline_tag ? <Badge>{humanizeToken(String(row.original.metrics.pipeline_tag))}</Badge> : <span className="text-ink-faint">—</span> },
    { id: 'downloads', accessorFn: r => Number(r.metrics.downloads ?? -1), header: 'Downloads', cell: ({ row }) => row.original.metrics.downloads ?? '—' },
    { id: 'likes', accessorFn: r => Number(r.metrics.likes ?? -1), header: 'Likes', cell: ({ row }) => row.original.metrics.likes ?? '—' },
    { id: 'published', accessorFn: r => r.published_at ?? '', header: 'Updated', cell: ({ row }) => formatDate(row.original.published_at) },
  ]
}

function paperColumns(): ColumnDef<ResearchRecord, any>[] {
  return [
    { id: 'title', header: 'Paper', cell: ({ row }) => titleCell(row.original) },
    { id: 'venue', header: 'Venue', cell: ({ row }) => row.original.metrics.venue || row.original.publisher || <span className="text-ink-faint">—</span> },
    { id: 'authors', header: 'Authors', cell: ({ row }) => row.original.authors.length ? row.original.authors.slice(0, 3).join(', ') + (row.original.authors.length > 3 ? ` +${row.original.authors.length - 3}` : '') : <span className="text-ink-faint">Not stored</span> },
    { id: 'citations', accessorFn: r => Number(r.metrics.cited_by_count ?? -1), header: 'Citations', cell: ({ row }) => row.original.metrics.cited_by_count ?? '—' },
    { id: 'published', accessorFn: r => r.published_at ?? '', header: 'Published', cell: ({ row }) => formatDate(row.original.published_at) },
  ]
}

export function ResearchStatus({ token }: { token: string }) {
  const { data, error } = useLoad<ResearchView>(() => getResearch(token), [token])
  return <DataState error={error} ready={!!data}>{data && (data.status === 'RESEARCH_RECORDS_FOUND' ? (
    <div className="space-y-4">
      <Card>
        <CardHeader><div><CardEyebrow>Trending</CardEyebrow><CardTitle>Models &amp; projects</CardTitle></div><Badge>{data.models.length}</Badge></CardHeader>
        <CardContent>{data.models.length ? (
          <DataTable columns={modelColumns()} data={data.models} searchPlaceholder="Search models…" emptyText="No models found." defaultSort={[{ id: 'downloads', desc: true }]} />
        ) : <EmptyState text="No model/project data found in the current corpus." />}</CardContent>
      </Card>
      <Card>
        <CardHeader><div><CardEyebrow>Papers</CardEyebrow><CardTitle>Research publications</CardTitle></div><Badge>{data.papers.length}</Badge></CardHeader>
        <CardContent>{data.papers.length ? (
          <DataTable columns={paperColumns()} data={data.papers} searchPlaceholder="Search papers…" emptyText="No papers found." defaultSort={[{ id: 'published', desc: true }]} />
        ) : <EmptyState text="No papers found in the current corpus." />}</CardContent>
      </Card>
    </div>
  ) : (
    <div className="flex items-start gap-2 rounded-md border border-border bg-surface-2 px-3 py-2.5 text-xs text-ink-muted">
      <span><strong className="text-ink">{humanizeToken(data.status)}</strong> — {data.reason} To appear here a research source must provide: {data.interface.required.join(', ')}.</span>
    </div>
  ))}</DataState>
}

// ---- Sources: data table ------------------------------------------------------------------------------------------
type SourceRowT = SourceStatusReport['sources'][number]
export function SourcesPanel({ token }: { token: string }) {
  const { data, error } = useLoad<SourceStatusReport>(() => getSourceStatus(token), [token])
  const pipe = useLoad<PipelineStatus>(() => getPipelineStatus(token), [token])

  const columns: ColumnDef<SourceRowT, any>[] = [
    { accessorKey: 'name', header: 'Source', cell: ({ row }) => <a href={row.original.url} target="_blank" rel="noreferrer" className="font-medium text-ink hover:text-accent">{row.original.name}</a> },
    { id: 'type', header: 'Type / tier', cell: ({ row }) => <span className="text-xs text-ink-muted">{humanizeToken(row.original.source_type)} · {humanizeToken(row.original.tier)}</span> },
    { id: 'configured', header: 'Configured', cell: ({ row }) => <StatusPill value={row.original.configured_active ? 'active' : 'inactive'} /> },
    { id: 'health', header: 'Health', cell: ({ row }) => <span title={row.original.last_health_error || ''} className="text-xs text-ink-muted">{humanizeToken(row.original.health_status)}{row.original.consecutive_failures ? ` (${row.original.consecutive_failures} failing)` : ''}</span> },
    { id: 'productivity', header: 'Productivity', cell: ({ row }) => <span className="text-xs text-ink-muted">{humanizeToken(row.original.productivity_state)}</span> },
    { accessorKey: 'accepted', header: 'Accepted' },
    { accessorKey: 'rejected', header: 'Rejected' },
    { id: 'rate', header: 'Accept rate', cell: ({ row }) => row.original.acceptance_rate === null ? '—' : `${Math.round(row.original.acceptance_rate * 100)}%` },
    { id: 'latest', header: 'Latest accepted', cell: ({ row }) => formatDate(row.original.latest_accepted_published_at) },
    { id: 'clients', header: 'Clients', cell: ({ row }) => row.original.clients.join(', ') || '—' },
  ]

  return <DataState error={error || pipe.error} ready={!!data && !!pipe.data}>{data && pipe.data && (
    <div className="space-y-4">
      <div className="flex items-center justify-between"><h2 className="text-lg font-semibold text-ink">Sources, Health &amp; Live Updates</h2><Badge>{data.total} sources</Badge></div>
      <Card>
        <CardHeader><div><CardEyebrow>Continuous updates</CardEyebrow><CardTitle>{humanizeToken(pipe.data.continuous_updates)}</CardTitle></div><Badge>Scheduler {pipe.data.scheduler.enabled ? 'enabled' : 'off'} · every {pipe.data.scheduler.interval_minutes} min</Badge></CardHeader>
        <CardContent className="space-y-2">
          <p className="text-[13px] text-ink-muted">{pipe.data.explanation} Health checks recorded: {pipe.data.health_checks_recorded}. Sources: {pipe.data.sources_active} active of {pipe.data.sources_total}. Stored: {pipe.data.accepted_total} accepted / {pipe.data.rejected_total} rejected.</p>
          <div className="flex flex-wrap gap-1.5">{pipe.data.clients.map(c => <Badge key={c.client}>{c.client}: {c.accepted_articles} accepted</Badge>)}</div>
        </CardContent>
      </Card>
      <DataTable columns={columns} data={data.sources} searchPlaceholder="Search sources…" emptyText="No source is registered yet." />
    </div>
  )}</DataState>
}

// ---- Clients ---------------------------------------------------------------------------------------------------
export function ClientsPanel({ token, isAdmin, renderArticles }: { token: string; isAdmin: boolean; renderArticles: (list: Article[]) => ReactNode }) {
  const PAGE = 24
  const [reload, setReload] = useState(0)
  const summary = useLoad<ClientSummaryView>(() => getClientSummary(token), [token, reload])
  const [adding, setAdding] = useState(false)
  const [query, setQuery] = useState('')
  const [suggestions, setSuggestions] = useState<ClientSuggestion[]>([])
  const [selected, setSelected] = useState<ClientSuggestion>()
  const [onboardBusy, setOnboardBusy] = useState(false)
  const [onboardError, setOnboardError] = useState('')
  const [onboarded, setOnboarded] = useState('')
  const [articles, setArticles] = useState<Record<string, Article[]>>({})
  const [loading, setLoading] = useState<Record<string, boolean>>({})
  const [error, setError] = useState('')
  const requested = useRef<Set<string>>(new Set())
  async function more(client: string) {
    const have = (articles[client] || []).length
    const key = `${client}:${have}`
    if (requested.current.has(key)) return
    requested.current.add(key)
    setLoading(l => ({ ...l, [client]: true }))
    try { const page = await getArticles(token, client.toLowerCase(), undefined, PAGE, have); setArticles(a => ({ ...a, [client]: [...(a[client] || []), ...page] })) }
    catch (e) { requested.current.delete(key); setError(e instanceof Error ? e.message : 'Request failed') } finally { setLoading(l => ({ ...l, [client]: false })) }
  }
  useEffect(() => { summary.data?.clients.forEach(c => { if (c.accepted_articles) void more(c.client) }) }, [summary.data]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!adding || query.trim().length < 2) { setSuggestions([]); return }
    const timer = window.setTimeout(() => { suggestClients(token, query.trim()).then(setSuggestions).catch(() => setSuggestions([])) }, 300)
    return () => window.clearTimeout(timer)
  }, [adding, query, token])
  async function createClient() {
    if (!query.trim() || onboardBusy) return
    setOnboardBusy(true); setOnboardError(''); setOnboarded('')
    try {
      const result = await onboardClient(token, query.trim(), selected?.url || query.trim())
      setOnboarded(`${result.canonical_name} was verified and added.`); setQuery(''); setSelected(undefined); setSuggestions([]); setReload(value => value + 1)
    } catch (err) { setOnboardError(err instanceof Error ? err.message : 'Could not add this company') }
    finally { setOnboardBusy(false) }
  }
  return <DataState error={error || summary.error} ready={!!summary.data}>{summary.data && (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-semibold text-ink">Client Intelligence</h2><p className="mt-1 text-xs text-ink-muted">Track a new company from its verified public information.</p></div><div className="flex items-center gap-2"><Badge>{summary.data.total_accepted} accepted · {summary.data.total_rejected} filtered out</Badge>{isAdmin && <Button size="sm" variant="primary" onClick={() => { setAdding(true); setOnboardError(''); setOnboarded('') }}><Plus size={14} /> Add client</Button>}</div></div>
      {isAdmin && adding && <Card>
        <CardHeader><div><CardEyebrow>Self-service onboarding</CardEyebrow><CardTitle>Find a company to monitor</CardTitle></div><button aria-label="Close add client" onClick={() => setAdding(false)} className="text-ink-faint hover:text-ink"><X size={16} /></button></CardHeader>
        <CardContent className="space-y-3">
          <p className="text-[13px] text-ink-muted">Paste the company website or select a website suggestion. The platform reads the company page and available RSS feeds, then verifies the evidence before creating its profile.</p>
          <div className="relative flex gap-2"><div className="relative min-w-0 flex-1"><Search size={15} className="pointer-events-none absolute left-2.5 top-2.5 text-ink-faint" /><Input value={query} onChange={event => { setQuery(event.target.value); setSelected(undefined) }} placeholder="Paste company website, e.g. company.com" className="pl-8" /></div><Button variant="primary" onClick={() => void createClient()} disabled={onboardBusy || query.trim().length < 2}>{onboardBusy ? 'Verifying…' : 'Add company'}</Button></div>
          {suggestions.length > 0 && !selected && <div className="divide-y divide-border rounded-md border border-border bg-surface">{suggestions.slice(0, 5).map(item => <button key={`${item.domain}-${item.url}`} onClick={() => { setSelected(item); setQuery(item.name) }} className="block w-full px-3 py-2 text-left hover:bg-surface-2"><p className="text-[13px] font-medium text-ink">{item.name}</p><p className="text-xs text-ink-faint">{item.domain} · {item.snippet}</p></button>)}</div>}
          {selected && <p className="text-xs text-ink-muted">Selected source: <span className="font-medium text-ink">{selected.domain}</span>. Web evidence will be checked before saving.</p>}
          {onboardError && <ErrorState message={onboardError} />}
          {onboarded && <p className="text-xs font-medium text-accent">{onboarded}</p>}
        </CardContent>
      </Card>}
      {summary.data.clients.map(c => {
        const list = articles[c.client] || []
        return (
          <Card key={c.client}>
            <CardHeader><div><CardTitle>{c.client}</CardTitle></div><div className="flex gap-1.5"><Badge>{c.accepted_articles} signals</Badge><Badge>{c.publishers} publishers</Badge>{c.latest_published_at && <Badge>latest {formatDate(c.latest_published_at)}</Badge>}</div></CardHeader>
            <CardContent>
              {c.accepted_articles ? (
                <>
                  {renderArticles(list)}
                  <p className="mt-3 text-xs text-ink-faint">Showing {list.length} of {c.accepted_articles}.</p>
                  {list.length < c.accepted_articles && (
                    <button onClick={() => void more(c.client)} disabled={loading[c.client]} className="mt-2 rounded-md bg-ink px-3 py-1.5 text-xs font-medium text-surface hover:bg-ink/90 disabled:opacity-50">
                      {loading[c.client] ? 'Loading…' : `Show ${Math.min(PAGE, c.accepted_articles - list.length)} more`}
                    </button>
                  )}
                </>
              ) : <EmptyState text="No verified intelligence detected in current sources." />}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )}</DataState>
}

// ---- news verification check ----------------------------------------------------------------------------------
const VERDICT_STYLE: Record<string, { label: string; tone: 'accent' | 'danger' | 'outline' }> = {
  LIKELY_REAL: { label: 'Likely real', tone: 'accent' },
  LIKELY_FAKE: { label: 'Likely fake', tone: 'danger' },
  UNVERIFIED: { label: 'Unverified', tone: 'outline' },
}

export function NewsDetectorPanel({ token }: { token: string }) {
  const [input, setInput] = useState('')
  const [phase, setPhase] = useState<'idle' | 'loading' | 'error'>('idle')
  const [error, setError] = useState('')
  const [result, setResult] = useState<FactCheckResult>()

  async function run(event: React.FormEvent) {
    event.preventDefault()
    const clean = input.trim()
    if (!clean) return
    setPhase('loading'); setError(''); setResult(undefined)
    try { setResult(await checkNews(token, clean)); setPhase('idle') }
    catch (err) { setPhase('error'); setError(err instanceof Error ? err.message : 'Could not check this') }
  }

  const style = result ? VERDICT_STYLE[result.verdict] ?? VERDICT_STYLE.UNVERIFIED : undefined

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><div><CardEyebrow>Verification</CardEyebrow><CardTitle>News Detector</CardTitle></div></CardHeader>
        <CardContent className="space-y-3">
          <p className="text-[13px] text-ink-muted">Paste a headline, a paragraph, or a URL from anywhere — it doesn't have to be a source already tracked by Xenom Intel. If it's a URL, the page is fetched live; either way it's checked against Xenom's own stored, evidenced corpus, never a guess from outside knowledge.</p>
          <form onSubmit={run} className="space-y-2">
            <textarea
              value={input} onChange={e => setInput(e.target.value)} rows={4} placeholder="Paste the news text or any news URL here…"
              className="w-full rounded-md border border-border bg-surface px-2.5 py-2 text-[13px] text-ink placeholder:text-ink-faint focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/40"
            />
            <Button type="submit" variant="primary" disabled={phase === 'loading' || !input.trim()}>{phase === 'loading' ? 'Checking…' : 'Check this news'}</Button>
          </form>
          {phase === 'error' && <ErrorState message={error} />}
        </CardContent>
      </Card>

      {result && (
        <Card>
          <CardHeader>
            <div><CardEyebrow>Result</CardEyebrow><CardTitle>Verdict</CardTitle></div>
            {style && <Badge variant={style.tone}>{style.label}</Badge>}
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-[13px] text-ink">{result.reasoning}</p>
            <p className="text-xs text-ink-faint">{result.note} Searched {result.checked_against} stored article(s){result.llm_used ? '' : ' — reasoning is coverage-based only; no LLM was available for this check'}.</p>
            {result.external_source && (
              <div className="rounded-md border border-border p-2.5 text-xs">
                <SectionEyebrow>Fetched source page</SectionEyebrow>
                {result.external_source.fetch_status === 'ok' ? (
                  <p className="mt-1 text-ink-muted"><span className="font-medium text-ink">{result.external_source.title || result.external_source.domain}</span> — {result.external_source.domain}{result.external_source.author ? ` · by ${result.external_source.author}` : ''}{result.external_source.published_at ? ` · ${formatDate(result.external_source.published_at)}` : ''}</p>
                ) : (
                  <p className="mt-1 text-ink-muted">Could not fetch {result.external_source.domain}: {result.external_source.error}</p>
                )}
              </div>
            )}
            {result.matched_coverage.length > 0 && (
              <div className="space-y-2">
                <SectionEyebrow>Where this is covered</SectionEyebrow>
                {result.matched_coverage.map(m => (
                  <a key={m.article_id} href={m.url} target="_blank" rel="noreferrer" className="block rounded-md border border-border p-2.5 text-xs hover:border-accent/40">
                    <span className="font-medium text-ink">{m.title || m.url}</span>
                    <span className="mt-1 block text-ink-faint">{m.source || 'Unknown source'}{m.tier ? ` · ${m.tier.replace('_', ' ')}` : ''}{m.published_at ? ` · ${formatDate(m.published_at)}` : ''}</span>
                  </a>
                ))}
              </div>
            )}
            {result.external_coverage.length > 0 && (
              <div className="space-y-2">
                <SectionEyebrow>External coverage found</SectionEyebrow>
                {result.external_coverage.map((item, index) => (
                  <a key={`${item.url}-${index}`} href={item.url} target="_blank" rel="noreferrer" className="block rounded-md border border-border p-2.5 text-xs hover:border-accent/40">
                    <span className="font-medium text-ink">{item.title}</span>
                    <span className="mt-1 block text-ink-faint">{item.source || item.provider}{item.published_at ? ` · ${formatDate(item.published_at)}` : ''}</span>
                    {item.snippet && <span className="mt-1 block line-clamp-2 text-ink-muted">{item.snippet}</span>}
                  </a>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
