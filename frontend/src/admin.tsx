import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Bookmark, Check, ExternalLink, RotateCcw, X } from 'lucide-react'
import { listClients } from './api'
import { humanConfidence, humanStatus, humanizeToken } from './format'
import { StatusPill, ConfidencePill, EvidenceRow, SectionEyebrow } from './components/evidence'
import { Card, CardContent, CardHeader, CardTitle, CardEyebrow, CardFooter } from './components/ui/card'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Input, Select } from './components/ui/input'
import { DataState, EmptyState, ErrorState } from './components/ui/states'
import { TabsRoot, TabsList, TabsTrigger, TabsContent } from './components/ui/tabs'
import { Sheet, SheetHeader, SheetBody } from './components/ui/sheet'
import {
  PROFILE_GROUPS, type AiUsage, type ArticleDetail, type AuditEntry, type Capabilities, type ClientLink, type DryRun, type EvalRunRow, type EvalSet, type Impact,
  type Job, type Profile, type ProfileData, type ProfileListField, type RecallItem, type Recommendation, type Researcher, type ResearcherList,
  type XenomCapability, type Similar, type SourceAudit, type SourceRow, type Suggestion,
  createEvalSet, createSource, decideRecall, decideSuggestion, dryRunSource, fetchSourceNow, generateSuggestions, getAiUsage, getArticleDetail, getAudit,
  getCapabilities, getProfile, getRecall, getRecommendations, getReevaluation, getResearcher, getSimilarResearchers, getSourceAudit, getSourceClients,
  listEvalRuns, listEvalSets, listResearchers, listXenomCapabilities, listSources, listSuggestions, previewProfile, rollbackProfile, rollbackSource,
  runEvalSet, runIndex, saveProfile, scanRecall, seedResearchers, setRecommendationStatus, setSourceClients, startReevaluation, syncRecommendations,
  updateSource, upsertXenomCapability, getOperations, listStoredInsights, setInsightStatus, materializeIntelligence, getNearDuplicates, type StoredInsight,
  listSavedArticles, saveArticle, unsaveArticle, getRelatedArticles,
  type FineTuneDataset, type FineTuneJob, listFineTuneDatasets, createFineTuneDataset, listFineTuneJobs, createFineTuneJob,
} from './architecture'

const formatDate = (value?: string | null) => value ? new Intl.DateTimeFormat('en', { dateStyle: 'medium' }).format(new Date(value)) : 'unknown'
const message = (err: unknown) => err instanceof Error ? err.message : 'Request failed'

function useAsync<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T>()
  const [error, setError] = useState('')
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let live = true
    setError('')
    load().then(d => { if (live) setData(d) }).catch(e => { if (live) setError(message(e)) })
    return () => { live = false }
  }, [...deps, tick]) // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error, reload: () => setTick(t => t + 1) }
}

const Note = ({ children }: { children: ReactNode }) => <p className="text-[13px] text-ink-muted">{children}</p>
const Fail = ({ text }: { text: string }) => text ? <p className="mt-2 text-xs text-danger">{text}</p> : null
const FormRow = ({ children }: { children: ReactNode }) => <div className="flex flex-wrap items-center gap-2">{children}</div>

// ---- capability status (honest: configured / not configured, with the reason) -------------------------------------------
export function CapabilityStrip({ token }: { token: string }) {
  const { data, error } = useAsync<Capabilities>(() => getCapabilities(token), [token])
  return <DataState error={error} ready={!!data}>{data && (
    <Card aria-label="Engine capabilities">
      <CardContent className="space-y-2">
        <div className="flex flex-wrap gap-1.5">
          <Badge title={String(data.lexical_search.index ?? '')}>Keyword search: {humanStatus(data.lexical_search.status)}</Badge>
          <Badge title={data.embeddings.detail ?? ''}>Embeddings: {humanStatus(data.embeddings.status)}{data.embeddings.model ? ` (${data.embeddings.model}, ${data.embeddings.embedded_chunks}/${data.embeddings.chunks})` : ''}</Badge>
          <Badge title={data.hybrid_search.detail ?? ''}>Semantic / hybrid: {humanStatus(data.hybrid_search.status)}</Badge>
          <Badge title={data.llm.detail ?? ''}>AI enrichment: {humanStatus(data.llm.status)}{data.llm.model ? ` (${data.llm.model})` : ''}</Badge>
          <Badge>Taxonomy v{data.taxonomy.version}: {data.taxonomy.labelled_articles} labelled</Badge>
        </div>
        {(data.embeddings.detail || data.llm.detail) && <Note>{[data.embeddings.detail, data.llm.detail].filter(Boolean).join(' ')}</Note>}
      </CardContent>
    </Card>
  )}</DataState>
}

// ---- article detail: provenance + stored relevance decision ---------------------------------------------------------------
export function ArticleDetailPanel({ token, id, onClose }: { token: string; id: string; onClose: () => void }) {
  const { data, error } = useAsync<ArticleDetail>(() => getArticleDetail(token, id), [token, id])
  const { data: related } = useAsync(() => getRelatedArticles(token, id), [token, id])
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState('')
  return (
    <Sheet open onOpenChange={v => !v && onClose()}>
      <SheetHeader title={data?.title ?? 'Stored item'} subtitle="Detail / source information" onClose={onClose} />
      <SheetBody>
        <DataState error={error} ready={!!data}>{data && (
          <div className="space-y-4">
            <p className="text-[13px] text-ink-muted">{data.source ? `${data.source.name} · ${humanizeToken(data.source.tier)}${data.source.source_class ? ` · ${humanizeToken(data.source.source_class)}` : ''}${data.source.reliability_score != null ? ` · reliability ${data.source.reliability_score}` : ''}` : 'No source recorded'} · published {formatDate(data.published_at)} · retrieved {formatDate(data.fetched_at)}</p>
            <div className="flex flex-wrap items-center gap-1.5"><StatusPill value={data.status} />{data.clients.length > 0 && <Badge>clients: {data.clients.join(', ')}</Badge>}</div>
            {data.url ? <a href={data.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline">View source <ExternalLink size={11} /></a> : <Note>{data.url_note}</Note>}
            <Button size="sm" variant={saved ? 'subtle' : 'outline'} onClick={() => { setSaveError(''); (saved ? unsaveArticle(token, id) : saveArticle(token, id)).then(() => setSaved(!saved)).catch(e => setSaveError(message(e))) }}><Bookmark size={13} /> {saved ? 'Saved' : 'Save article'}</Button>
            <Fail text={saveError} />
            {data.summary && <p className="text-[13px] text-ink">{data.summary}</p>}
            <div><SectionEyebrow>Stored relevance decision</SectionEyebrow>
              {data.decision ? <p className="text-[13px] text-ink-muted">{humanStatus(data.decision.decision)} at stage {humanizeToken(data.decision.stage)}{data.decision.reason ? ` (${data.decision.reason})` : ''}{data.decision.entity ? ` · client ${data.decision.entity}` : ''} · score {data.decision.relevance_score ?? data.relevance_score ?? 'n/a'}</p> : <Note>No decision is stored for this item.</Note>}
            </div>
            <div><SectionEyebrow>Taxonomy labels</SectionEyebrow>
              {data.labels.length ? <div className="flex flex-wrap gap-1.5">{data.labels.map(l => <Badge key={`${l.label}-${l.method}`} title={JSON.stringify(l.evidence)}>{humanizeToken(l.label)} · {humanizeToken(l.method)}{l.confidence != null ? ` ${Math.round(l.confidence * 100)}%` : ''}</Badge>)}</div> : <Note>Unlabelled: no taxonomy rule matched. The item stays searchable.</Note>}
            </div>
            {data.authors.length > 0 && <div><SectionEyebrow>Authors</SectionEyebrow><p className="text-[13px] text-ink-muted">{data.authors.map(a => `${a.name}${a.affiliation ? ` (${a.affiliation})` : ''}`).join('; ')}</p></div>}
            {data.claims.length > 0 && <div><SectionEyebrow>Claims citing this item</SectionEyebrow><ul className="list-inside list-disc text-[13px] text-ink-muted">{data.claims.map(c => <li key={c.claim_id}>{c.claim} <span className="text-xs text-ink-faint">({c.status})</span></li>)}</ul></div>}
            <div><SectionEyebrow>Evidence chunks</SectionEyebrow>
              {data.chunks.length ? <p className="text-xs text-ink-faint">{data.chunks.length} chunk(s) with character offsets{data.chunks.some(c => c.embedded_with) ? `; embedded with ${[...new Set(data.chunks.map(c => c.embedded_with).filter(Boolean))].join(', ')}` : '; not embedded'}</p> : <Note>No chunks are stored for this item.</Note>}
            </div>
            {related?.results?.length ? <div><SectionEyebrow>Related articles</SectionEyebrow><div className="space-y-2">{related.results.map(item => <a key={item.article_id} href={item.url} target="_blank" rel="noreferrer" className="block rounded-md border border-border p-2 text-xs hover:border-accent/40"><span className="font-medium text-ink">{item.title || item.url}</span><span className="mt-1 block text-ink-faint">{item.source || 'Stored article'} · similarity {item.similarity ?? 'n/a'}</span></a>)}</div></div> : related?.detail ? <Note>{related.detail}</Note> : null}
          </div>
        )}</DataState>
      </SheetBody>
    </Sheet>
  )
}

export function SavedArticlesPanel({ token }: { token: string }) {
  const { data, error, reload } = useAsync(() => listSavedArticles(token), [token])
  return <DataState error={error} ready={!!data}>{data && <Card>
    <CardHeader><div><CardEyebrow>Reading list</CardEyebrow><CardTitle>Saved articles</CardTitle></div><Badge>{data.length}</Badge></CardHeader>
    <CardContent className="space-y-2">
      {data.length ? data.map(article => <div key={article.id} className="flex items-start justify-between gap-3 rounded-md border border-border p-3">
        <div className="min-w-0"><a href={article.url} target="_blank" rel="noreferrer" className="text-[13px] font-medium text-ink hover:text-accent">{article.title || article.url}</a><p className="mt-1 text-xs text-ink-faint">{article.source_name || 'Stored article'} · {formatDate(article.published_at)}</p></div>
        <Button size="sm" variant="ghost" onClick={() => unsaveArticle(token, article.id).then(reload).catch(() => undefined)} aria-label={`Remove ${article.title || 'saved article'}`}><X size={14} /></Button>
      </div>) : <EmptyState text="No saved articles yet." />}
    </CardContent>
  </Card>}</DataState>
}

// ---- researchers ----------------------------------------------------------------------------------------------------
export function ResearchersPanel({ token, isAdmin }: { token: string; isAdmin: boolean }) {
  const [q, setQ] = useState('')
  const { data, error, reload } = useAsync<ResearcherList>(() => listResearchers(token, q || undefined), [token, q])
  const [open, setOpen] = useState<Researcher>()
  const [context, setContext] = useState('')
  const [similar, setSimilar] = useState<Similar>()
  const [seed, setSeed] = useState('')
  const [fail, setFail] = useState('')
  async function show(id: string) { setFail(''); setSimilar(undefined); try { setOpen(await getResearcher(token, id)) } catch (e) { setFail(message(e)) } }
  async function findSimilar() { if (!open) return; setFail(''); try { setSimilar(await getSimilarResearchers(token, open.person_id, context || undefined)) } catch (e) { setFail(message(e)) } }
  async function saveSeed() {
    setFail('')
    const people = seed.split('\n').map(l => l.trim()).filter(Boolean).map(line => { const [name, organisation, areas] = line.split('|').map(s => s.trim()); return { name, organisation: organisation || undefined, areas: areas ? areas.split(',').map(a => a.trim()).filter(Boolean) : [] } })
    try { await seedResearchers(token, people); setSeed(''); reload() } catch (e) { setFail(message(e)) }
  }
  return (
    <Card>
      <CardHeader><div><CardEyebrow>Researchers</CardEyebrow><CardTitle>Who works on what, from stored work</CardTitle></div><Badge>{data?.total ?? 0}</Badge></CardHeader>
      <CardContent className="space-y-3">
        <DataState error={error} ready={!!data}>{data && (
          <>
            <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Filter by name" className="max-w-xs" />
            {data.status && <Note>{data.detail}</Note>}
            <div className="grid gap-3 sm:grid-cols-2">
              {data.items.map(r => (
                <div key={r.person_id} className="rounded-md border border-border p-3">
                  <p className="text-xs text-ink-faint">{r.documents} stored document(s){r.latest_document ? ` · latest ${formatDate(r.latest_document)}` : ''}</p>
                  <button className="text-[13px] font-medium text-ink underline-offset-2 hover:text-accent hover:underline" onClick={() => show(r.person_id)}>{r.name}</button>
                  <p className="mt-1 text-xs text-ink-muted">{r.organisations.length ? r.organisations.map(o => `${o.organisation} (${o.verification_status}, ${o.evidence_count} doc)`).join('; ') : 'organisation not stored'}</p>
                  {r.areas.length > 0 && <div className="mt-1.5 flex flex-wrap gap-1">{r.areas.map(a => <Badge key={a.term}>{a.term}</Badge>)}</div>}
                  {r.evidence_status && <Note>{r.detail}</Note>}
                </div>
              ))}
            </div>
          </>
        )}</DataState>
        {open && (
          <div className="rounded-md border border-border p-3">
            <h3 className="text-[13px] font-semibold text-ink">{open.name}</h3>
            {open.recent_work?.length ? <ul className="mt-1.5 space-y-1 text-[13px]">{open.recent_work.map(w => <li key={w.article_id}><a href={w.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">{w.title}</a> <span className="text-xs text-ink-faint">{formatDate(w.published_at)} · {w.source}</span></li>)}</ul> : <Note>No stored recent work.</Note>}
            <FormRow>
              <Input value={context} onChange={e => setContext(e.target.value)} placeholder="Context, e.g. robotics manipulation" className="max-w-xs" />
              <Button variant="primary" size="sm" onClick={findSimilar}>Find similar researchers</Button>
            </FormRow>
            {similar && (
              <div className="mt-2 space-y-2">
                {similar.status && <Note>{similar.detail}</Note>}
                {similar.method && <Note>Method: {humanizeToken(similar.method)}</Note>}
                {similar.results.map(s => (
                  <div key={s.person_id} className="rounded-md border border-border p-2.5">
                    <p className="text-[13px] font-medium text-ink">{s.name} <Badge>{s.similarity}</Badge></p>
                    <p className="text-xs text-ink-muted">{s.organisations.map(o => o.organisation).join('; ') || 'organisation not stored'} · recent: {s.recent_work?.map(w => w.title).join('; ')}</p>
                    {s.relevance_in_context && <p className="mt-1 text-xs text-ink-muted">{s.relevance_in_context.documents.length ? `Relevant to "${s.relevance_in_context.context}": ${s.relevance_in_context.documents.map(d => `${d.title} (${d.matched_terms.join(', ')})`).join('; ')}` : s.relevance_in_context.detail}</p>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {isAdmin && (
          <div className="rounded-md border border-border p-3">
            <h3 className="text-[13px] font-semibold text-ink">Seed list</h3>
            <Note>One person per line: name | organisation | areas (comma separated). Seeds are tracked, never ranked above people with stored work.</Note>
            <textarea value={seed} onChange={e => setSeed(e.target.value)} rows={3} className="mt-2 w-full rounded-md border border-border bg-surface p-2 text-[13px]" />
            <Button variant="primary" size="sm" className="mt-2" disabled={!seed.trim()} onClick={saveSeed}>Add to seed list</Button>
          </div>
        )}
        <Fail text={fail} />
      </CardContent>
    </Card>
  )
}

// ---- recommendations ------------------------------------------------------------------------------------------------
export function RecommendationsPanel({ token, isAdmin, client }: { token: string; isAdmin: boolean; client?: string }) {
  const [choice, setChoice] = useState('')
  const [names, setNames] = useState<string[]>([])
  useEffect(() => { listClients(token).then(setNames).catch(() => setNames([])) }, [token])
  const active = client || choice || undefined
  const { data, error, reload } = useAsync<Recommendation[]>(() => getRecommendations(token, active), [token, active])
  const [fail, setFail] = useState('')
  const [info, setInfo] = useState('')
  async function sync() { if (!active) return; setFail(''); try { const r = await syncRecommendations(token, active); setInfo(`${r.created} new, ${r.updated} refreshed from ${r.opportunities} opportunities`); reload() } catch (e) { setFail(message(e)) } }
  async function status(id: string, s: string) { setFail(''); try { await setRecommendationStatus(token, id, s); reload() } catch (e) { setFail(message(e)) } }
  return (
    <Card>
      <CardHeader><div><CardEyebrow>Recommended actions</CardEyebrow><CardTitle>Drafts for review</CardTitle></div><Badge>{data?.length ?? 0}</Badge></CardHeader>
      <CardContent className="space-y-3">
        <Note>Actions start as drafts. Confidence comes from the evidence (independent publishers, source tier), never from a model. Nothing is published until a person changes its status.</Note>
        <FormRow>
          {!client && <Select value={choice} onChange={e => setChoice(e.target.value)}><option value="">All clients</option>{names.map(c => <option key={c}>{c}</option>)}</Select>}
          {isAdmin && active && <Button size="sm" variant="primary" onClick={sync}>Persist current opportunities as drafts</Button>}
        </FormRow>
        {info && <Note>{info}</Note>}
        <DataState error={error} ready={!!data}>{data && (data.length ? (
          <div className="space-y-2">
            {data.map(r => (
              <div key={r.id} className="rounded-md border border-border p-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="flex flex-wrap items-center gap-1.5 text-xs text-ink-faint">{r.client} · <StatusPill value={r.status} /> · {humanizeToken(r.origin)}{r.generated ? ' · AI-drafted text' : ''}</div>
                    <h3 className="mt-1 text-[13px] font-medium text-ink">{r.title}</h3>
                  </div>
                  <ConfidencePill value={r.confidence} />
                </div>
                <p className="mt-1 text-[13px] text-ink-muted">{r.action}</p>
                <p className="mt-0.5 text-xs text-ink-faint">{r.rationale}</p>
                <p className="mt-1 text-xs text-ink-faint">{r.article_ids?.length ?? 0} stored article(s){r.claim_ids?.length ? ` · ${r.claim_ids.length} cited claim(s)` : ''}</p>
                {isAdmin && <div className="mt-2 flex gap-1.5">{['reviewed', 'published', 'dismissed'].filter(s => s !== r.status).map(s => <Button key={s} size="sm" variant="outline" onClick={() => status(r.id, s)}>{humanStatus(s)}</Button>)}</div>}
              </div>
            ))}
          </div>
        ) : <EmptyState text={`No recommendation is stored${active ? ` for ${active}` : ''}.`} hint="They are created from evidence-backed opportunities on request." />)}</DataState>
        <Fail text={fail} />
      </CardContent>
    </Card>
  )
}

// ---- profiles admin ---------------------------------------------------------------------------------------------------
const lines = (value: string) => value.split('\n').map(s => s.trim()).filter(Boolean)

function ProfilesAdmin({ token, isAdmin }: { token: string; isAdmin: boolean }) {
  const [clients, setClients] = useState<string[]>([])
  const [client, setClient] = useState('')
  const [profile, setProfile] = useState<Profile>()
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [impact, setImpact] = useState<Impact>()
  const [note, setNote] = useState('')
  const [fail, setFail] = useState('')
  const [job, setJob] = useState<Job>()
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [audit, setAudit] = useState<AuditEntry[]>([])
  useEffect(() => { listClients(token).then(list => { setClients(list); if (list.length) setClient(list[0]) }).catch(e => setFail(message(e))) }, [token])
  const load = useCallback(async () => {
    if (!client) return
    setFail(''); setImpact(undefined)
    try {
      const p = await getProfile(token, client)
      setProfile(p)
      setDraft(Object.fromEntries(PROFILE_GROUPS.flatMap(g => g.fields).map(f => [f.key, (p.data[f.key] ?? []).join('\n')])))
      setSuggestions(await listSuggestions(token, client)); setAudit(await getAudit(token, 'client_profile'))
    } catch (e) { setFail(message(e)) }
  }, [token, client])
  useEffect(() => { load() }, [load])
  const proposed = (): ProfileData | undefined => profile ? ({ ...profile.data, ...Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, lines(v)])) } as ProfileData) : undefined
  async function preview() { const d = proposed(); if (!d) return; setFail(''); try { setImpact(await previewProfile(token, client, d)) } catch (e) { setFail(message(e)) } }
  async function save() { const d = proposed(); if (!d) return; setFail(''); try { await saveProfile(token, client, d, note || undefined); setNote(''); await load() } catch (e) { setFail(message(e)) } }
  async function rollback(v: number) { setFail(''); try { await rollbackProfile(token, client, v); await load() } catch (e) { setFail(message(e)) } }
  async function reevaluate() {
    setFail('')
    try {
      let j = await startReevaluation(token, client); setJob(j)
      for (let i = 0; i < 60 && j.status === 'running'; i++) { await new Promise(r => setTimeout(r, 1000)); j = await getReevaluation(token, client, j.job_id); setJob(j) }
    } catch (e) { setFail(message(e)) }
  }
  async function suggest() { setFail(''); try { await generateSuggestions(token, client); setSuggestions(await listSuggestions(token, client)) } catch (e) { setFail(message(e)) } }
  async function decide(id: string, accept: boolean) { setFail(''); try { await decideSuggestion(token, client, id, accept); await load() } catch (e) { setFail(message(e)) } }
  return (
    <Card>
      <CardHeader><div><CardEyebrow>Client profiles</CardEyebrow><CardTitle>Versioned rule configuration</CardTitle></div><Select value={client} onChange={e => setClient(e.target.value)}>{clients.map(c => <option key={c}>{c}</option>)}</Select></CardHeader>
      <CardContent className="space-y-4">
        {!isAdmin && <Note>Read-only: the administrator role is required to change profiles.</Note>}
        {profile && (
          <>
            <Note>{profile.origin === 'code' ? 'No saved version: the rule engine uses the code configuration (fallback).' : `Active version v${profile.version}.`} A saved profile drives relevance, queries and taxonomy; competitors and technologies become entities.</Note>
            <div className="space-y-2">
              {PROFILE_GROUPS.map(g => (
                <details key={g.title} open={g.title === 'Identity'} className="rounded-md border border-border p-2.5">
                  <summary className="cursor-pointer text-[13px] font-medium text-ink">{g.title} <span className="text-xs font-normal text-ink-faint">{g.hint}</span></summary>
                  <div className="mt-2 grid gap-2 sm:grid-cols-2">
                    {g.fields.map(f => (
                      <label key={f.key} className="block space-y-1 text-xs font-medium text-ink-muted">{f.label} <span className="text-ink-faint">({lines(draft[f.key] ?? '').length})</span>
                        <textarea value={draft[f.key] ?? ''} rows={4} disabled={!isAdmin} onChange={e => setDraft({ ...draft, [f.key as ProfileListField]: e.target.value })}
                          className="w-full rounded-md border border-border bg-surface p-2 text-[13px] text-ink disabled:opacity-60" />
                      </label>
                    ))}
                  </div>
                </details>
              ))}
            </div>
            {isAdmin && (
              <FormRow>
                <Input value={note} onChange={e => setNote(e.target.value)} placeholder="Change note (optional)" className="max-w-xs" />
                <Button variant="outline" size="sm" onClick={preview}>Preview impact</Button>
                <Button variant="primary" size="sm" onClick={save}>Save new version</Button>
              </FormRow>
            )}
            {impact && (
              <div className="rounded-md border border-border p-3">
                <h3 className="text-[13px] font-semibold text-ink">Impact on stored articles</h3>
                <p className="mt-1 text-[13px] text-ink-muted">This change would add <strong className="text-ink">{impact.would_add}</strong> and remove <strong className="text-ink">{impact.would_remove}</strong> of {impact.evaluated_articles} stored articles ({impact.unchanged} unchanged). {impact.note}</p>
                <div className="mt-2 space-y-1">{[...impact.added_examples.map(e => ({ ...e, sign: '+' })), ...impact.removed_examples.map(e => ({ ...e, sign: '−' }))].map(e => <p key={e.article_id} className="text-xs text-ink-faint">{e.sign} {e.title} ({humanStatus(e.stored_status)}; score {e.current.score} → {e.proposed.score})</p>)}</div>
              </div>
            )}
            <div className="rounded-md border border-border p-3">
              <h3 className="text-[13px] font-semibold text-ink">Versions</h3>
              {profile.versions.length ? (
                <ul className="mt-1.5 space-y-1.5">{profile.versions.map(v => (
                  <li key={v.version} className="flex items-center justify-between gap-2 text-[13px]">
                    <span>v{v.version}{v.is_active ? ' (active)' : ''} · {formatDate(v.created_at)} · {v.created_by ?? 'unknown'}{v.change_note ? ` · ${v.change_note}` : ''}{v.reverted_from ? ` · rollback of v${v.reverted_from}` : ''}</span>
                    {isAdmin && !v.is_active && <Button size="sm" variant="ghost" title={`Roll back to v${v.version}`} onClick={() => rollback(v.version)}><RotateCcw size={13} /> Roll back</Button>}
                  </li>
                ))}</ul>
              ) : <Note>No saved versions yet.</Note>}
              {isAdmin && <Button size="sm" variant="outline" className="mt-2" onClick={reevaluate}>Re-evaluate stored articles under the active profile</Button>}
              {job && <Note>Re-evaluation {humanStatus(job.status)}{job.result ? `: ${Object.entries(job.result).map(([k, v]) => `${humanizeToken(k)}: ${v}`).join(', ')}` : ''}{job.error ? `: ${job.error}` : ''}</Note>}
            </div>
            <div className="rounded-md border border-border p-3">
              <div className="flex items-center justify-between"><h3 className="text-[13px] font-semibold text-ink">Suggestions</h3>{isAdmin && <Button size="sm" variant="outline" onClick={suggest}>Generate from stored articles</Button>}</div>
              {suggestions.length ? (
                <div className="mt-2 space-y-1.5">{suggestions.map(s => (
                  <div key={s.id} className="flex items-center justify-between gap-2 rounded-md bg-surface-2 px-2.5 py-1.5">
                    <div><p className="text-[13px] font-medium text-ink">{humanizeToken(s.kind)}: {s.value}</p><p className="text-xs text-ink-faint">{s.reason}</p></div>
                    {isAdmin && <div className="flex shrink-0 gap-1"><Button size="icon" variant="ghost" title="Accept" onClick={() => decide(s.id, true)}><Check size={14} /></Button><Button size="icon" variant="ghost" title="Dismiss" onClick={() => decide(s.id, false)}><X size={14} /></Button></div>}
                  </div>
                ))}</div>
              ) : <Note>No pending suggestions.</Note>}
            </div>
            <div className="rounded-md border border-border p-3">
              <h3 className="text-[13px] font-semibold text-ink">Audit trail</h3>
              {audit.length ? <ul className="mt-1.5 space-y-1 text-xs text-ink-faint">{audit.slice(0, 8).map(a => <li key={a.id}>{formatDate(a.created_at)} · {a.actor ?? 'system'} · {humanizeToken(a.action)} · {a.object_key}</li>)}</ul> : <Note>No changes recorded.</Note>}
            </div>
          </>
        )}
        <Fail text={fail} />
      </CardContent>
    </Card>
  )
}

// ---- sources admin ----------------------------------------------------------------------------------------------------
const FETCH_MODES = ['rss', 'seed_article', 'link_listing', 'table_rows', 'arxiv_api', 'huggingface_hub', 'openalex_api']
const TIERS = ['TIER_1', 'TIER_2', 'TIER_3', 'TIER_4']
const CLASSES = ['official', 'trusted_news', 'news', 'directory', 'reference', 'research', 'forum']
const TYPES = ['RSS', 'HTML', 'OFFICIAL_NEWSROOM', 'PRESS_RELEASE', 'GOVERNMENT', 'REGULATORY', 'OTHER_PUBLIC', 'RESEARCH_API', 'MODEL_HUB']

function DryRunResult({ report }: { report: DryRun }) {
  return (
    <div className="rounded-md border border-border p-3">
      <h3 className="text-[13px] font-semibold text-ink">Dry run {report.fetch_status === 'failed' ? 'failed' : '(nothing was stored)'}</h3>
      {report.fetch_error ? <p className="mt-1 text-xs text-danger">{report.fetch_error}</p> : (
        <>
          <p className="mt-1 text-[13px] text-ink-muted">{report.documents_fetched} fetched · {report.documents_accepted} would be accepted · {report.documents_rejected} rejected · {report.documents_invalid} invalid · {report.documents_deduplicated} already stored</p>
          {report.rejection_reasons && Object.keys(report.rejection_reasons).length > 0 && <p className="mt-1 text-xs text-ink-faint">Rejection reasons: {Object.entries(report.rejection_reasons).map(([k, v]) => `${humanizeToken(k)} (${v})`).join(' · ')}</p>}
          <div className="mt-2 space-y-1 text-xs">
            {report.samples?.accepted.map((a, i) => <p key={`a${i}`} className="text-accent">✓ {a.title} <span className="text-ink-faint">({a.client ?? 'research topic'}, relevance {a.relevance}, quality {a.quality})</span></p>)}
            {report.samples?.rejected.map((a, i) => <p key={`r${i}`} className="text-ink-faint">✗ {a.title} ({humanizeToken(a.stage)}: {a.reason})</p>)}
            {report.samples?.invalid.map((a, i) => <p key={`i${i}`} className="text-warn">! {a.title} ({a.problems.join(', ')})</p>)}
          </div>
        </>
      )}
    </div>
  )
}

function SourcesAdmin({ token, isAdmin, clients }: { token: string; isAdmin: boolean; clients: string[] }) {
  const { data, error, reload } = useAsync<SourceRow[]>(() => listSources(token), [token])
  const blank = { name: '', url: '', source_type: 'RSS', tier: 'TIER_2', source_class: 'news', fetch_mode: 'rss', query: '', link_pattern: '', reliability: '', client: '' }
  const [form, setForm] = useState(blank)
  const [report, setReport] = useState<DryRun>()
  const [fail, setFail] = useState('')
  const [audit, setAudit] = useState<SourceAudit[]>([])
  const [links, setLinks] = useState<{ id: string; rows: ClientLink[] }>()
  const config = () => ({ fetch_mode: form.fetch_mode, query: form.query || undefined, link_pattern: form.link_pattern || undefined, source_kind: form.source_class === 'research' ? 'research' : undefined })
  const body = () => ({ name: form.name, url: form.url, source_type: form.source_type, tier: form.tier, source_class: form.source_class, config: config(), ...(form.reliability ? { reliability_score: Number(form.reliability) } : {}) })
  async function dry() { setFail(''); setReport(undefined); try { setReport(await dryRunSource(token, { name: form.name, url: form.url, source_type: form.source_type, tier: form.tier, config: config(), client: form.client || undefined })) } catch (e) { setFail(message(e)) } }
  async function add() { setFail(''); try { const s = await createSource(token, body()); if (form.client) await setSourceClients(token, s.id, [{ client: form.client, priority: 0, role: 'primary' }]); setForm(blank); reload() } catch (e) { setFail(message(e)) } }
  async function dryExisting(id: string) { setFail(''); setReport(undefined); try { setReport(await dryRunSource(token, { source_id: id })) } catch (e) { setFail(message(e)) } }
  async function patch(id: string, change: Record<string, unknown>) { setFail(''); try { await updateSource(token, id, change); reload() } catch (e) { setFail(message(e)) } }
  async function runNow(id: string) { setFail(''); try { const r = await fetchSourceNow(token, id); setFail(`Fetched ${r.documents_found}, stored ${r.articles_created}`) } catch (e) { setFail(message(e)) } }
  async function showAudit(id: string) { setFail(''); try { setAudit(await getSourceAudit(token, id)); setLinks({ id, rows: await getSourceClients(token, id) }) } catch (e) { setFail(message(e)) } }
  async function undo(id: string, auditId: string) { setFail(''); try { await rollbackSource(token, id, auditId); reload(); setAudit(await getSourceAudit(token, id)) } catch (e) { setFail(message(e)) } }
  async function saveLinks(rows: ClientLink[]) { if (!links) return; try { setLinks({ id: links.id, rows: await setSourceClients(token, links.id, rows) }) } catch (e) { setFail(message(e)) } }
  return (
    <Card>
      <CardHeader><div><CardEyebrow>Sources</CardEyebrow><CardTitle>Registry, tiers and dry run</CardTitle></div><Badge>{data?.length ?? 0}</Badge></CardHeader>
      <CardContent className="space-y-4">
        {!isAdmin && <Note>Read-only: the administrator role is required to add, edit or test sources.</Note>}
        <DataState error={error} ready={!!data}>{data && (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full min-w-max border-collapse text-[13px]">
              <thead><tr className="border-b border-border bg-surface-2 text-left text-ink-muted"><th className="px-3 py-2 font-medium">Name</th><th className="px-3 py-2 font-medium">Type</th><th className="px-3 py-2 font-medium">Tier</th><th className="px-3 py-2 font-medium">Class</th><th className="px-3 py-2 font-medium">Reliability</th><th className="px-3 py-2 font-medium">Priority</th><th className="px-3 py-2 font-medium">Active</th><th className="px-3 py-2" /></tr></thead>
              <tbody>{data.map(s => (
                <tr key={s.id} className="border-b border-border last:border-0">
                  <td className="px-3 py-2" title={s.canonical_url}>{s.name}</td>
                  <td className="px-3 py-2 text-ink-muted">{s.source_type ? humanizeToken(s.source_type) : '—'}</td>
                  <td className="px-3 py-2">{isAdmin ? <Select value={s.tier} onChange={e => patch(s.id, { tier: e.target.value })}>{TIERS.map(t => <option key={t} value={t}>{humanizeToken(t)}</option>)}</Select> : humanizeToken(s.tier)}</td>
                  <td className="px-3 py-2 text-ink-muted">{s.source_class ? humanizeToken(s.source_class) : '—'}</td>
                  <td className="px-3 py-2">{s.reliability_score ?? '—'}</td>
                  <td className="px-3 py-2">{s.priority}</td>
                  <td className="px-3 py-2">{isAdmin ? <input type="checkbox" checked={s.is_active} onChange={e => patch(s.id, { is_active: e.target.checked })} /> : s.is_active ? 'Yes' : 'No'}</td>
                  <td className="px-3 py-2"><div className="flex gap-1.5">{s.config?.fetch_mode && isAdmin && <><Button size="sm" variant="outline" onClick={() => dryExisting(s.id)}>Test</Button><Button size="sm" variant="outline" onClick={() => runNow(s.id)}>Run now</Button></>}<Button size="sm" variant="ghost" onClick={() => showAudit(s.id)}>History</Button></div></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}</DataState>
        {report && <DryRunResult report={report} />}
        {links && (
          <div className="rounded-md border border-border p-3">
            <h3 className="text-[13px] font-semibold text-ink">Serves clients</h3>
            {links.rows.length ? <div className="mt-1 flex flex-wrap gap-1.5">{links.rows.map(l => <Badge key={l.client}>{l.client} · {l.role}</Badge>)}</div> : <Note>No client link.</Note>}
            {isAdmin && <Select className="mt-2" value="" onChange={e => e.target.value && saveLinks([...links.rows, { client: e.target.value, priority: 0, role: 'primary' }])}><option value="">Link a client…</option>{clients.filter(c => !links.rows.some(r => r.client === c.toLowerCase())).map(c => <option key={c}>{c}</option>)}</Select>}
            <h3 className="mt-3 text-[13px] font-semibold text-ink">Change history</h3>
            <div className="mt-1 space-y-1">{audit.map(a => (
              <div key={a.audit_id} className="flex items-center justify-between gap-2 text-xs text-ink-faint">
                <span>{formatDate(a.created_at)} · {a.actor ?? 'system'} · {humanizeToken(a.action)}</span>
                {isAdmin && a.before && <Button size="sm" variant="ghost" onClick={() => undo(a.source_id, a.audit_id)}><RotateCcw size={12} /> Restore</Button>}
              </div>
            ))}</div>
          </div>
        )}
        {isAdmin && (
          <div className="rounded-md border border-border p-3">
            <h3 className="text-[13px] font-semibold text-ink">Add a source</h3>
            <Note>Test before saving: a dry run fetches and parses the source and shows what the relevance engine would accept or reject. Nothing is stored.</Note>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <label className="block space-y-1 text-xs font-medium text-ink-muted">Name<Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
              <label className="block space-y-1 text-xs font-medium text-ink-muted">URL<Input value={form.url} onChange={e => setForm({ ...form, url: e.target.value })} /></label>
              <label className="block space-y-1 text-xs font-medium text-ink-muted">Type<Select className="w-full" value={form.source_type} onChange={e => setForm({ ...form, source_type: e.target.value })}>{TYPES.map(t => <option key={t} value={t}>{humanizeToken(t)}</option>)}</Select></label>
              <label className="block space-y-1 text-xs font-medium text-ink-muted">Fetch mode<Select className="w-full" value={form.fetch_mode} onChange={e => setForm({ ...form, fetch_mode: e.target.value })}>{FETCH_MODES.map(t => <option key={t} value={t}>{humanizeToken(t)}</option>)}</Select></label>
              <label className="block space-y-1 text-xs font-medium text-ink-muted">Tier<Select className="w-full" value={form.tier} onChange={e => setForm({ ...form, tier: e.target.value })}>{TIERS.map(t => <option key={t} value={t}>{humanizeToken(t)}</option>)}</Select></label>
              <label className="block space-y-1 text-xs font-medium text-ink-muted">Class<Select className="w-full" value={form.source_class} onChange={e => setForm({ ...form, source_class: e.target.value })}>{CLASSES.map(t => <option key={t} value={t}>{humanizeToken(t)}</option>)}</Select></label>
              <label className="block space-y-1 text-xs font-medium text-ink-muted">Reliability 0–1<Input value={form.reliability} onChange={e => setForm({ ...form, reliability: e.target.value })} inputMode="decimal" /></label>
              <label className="block space-y-1 text-xs font-medium text-ink-muted">Client<Select className="w-full" value={form.client} onChange={e => setForm({ ...form, client: e.target.value })}><option value="">(none)</option>{clients.map(c => <option key={c}>{c}</option>)}</Select></label>
              <label className="block space-y-1 text-xs font-medium text-ink-muted">Search query (research APIs)<Input value={form.query} onChange={e => setForm({ ...form, query: e.target.value })} /></label>
              <label className="block space-y-1 text-xs font-medium text-ink-muted">Link pattern (listing pages)<Input value={form.link_pattern} onChange={e => setForm({ ...form, link_pattern: e.target.value })} /></label>
            </div>
            <FormRow><Button variant="outline" size="sm" onClick={dry} disabled={!form.name || !form.url}>Test (dry run)</Button><Button variant="primary" size="sm" onClick={add} disabled={!form.name || !form.url}>Save source</Button></FormRow>
          </div>
        )}
        <Fail text={fail} />
      </CardContent>
    </Card>
  )
}

// ---- review queue: recall recovery -------------------------------------------------------------------------------------
function ReviewQueue({ token, isAdmin, clients }: { token: string; isAdmin: boolean; clients: string[] }) {
  const { data, error, reload } = useAsync<RecallItem[]>(() => getRecall(token), [token])
  const [client, setClient] = useState('')
  const [info, setInfo] = useState('')
  const [fail, setFail] = useState('')
  async function scan() { setFail(''); try { const r = await scanRecall(token, client || undefined); setInfo(`${humanizeToken(r.method)}: ${r.candidates_scored} scored, ${Object.entries(r.queued).map(([k, v]) => `${humanizeToken(k)}: ${v}`).join(', ')} (threshold ${r.threshold})${r.detail ? ` — ${r.detail}` : ''}`); reload() } catch (e) { setFail(message(e)) } }
  async function decide(id: string, accept: boolean) { setFail(''); try { await decideRecall(token, id, accept); reload() } catch (e) { setFail(message(e)) } }
  return (
    <Card>
      <CardHeader><div><CardEyebrow>Recall recovery</CardEyebrow><CardTitle>Rejected items queued for review</CardTitle></div><Badge>{data?.length ?? 0}</Badge></CardHeader>
      <CardContent className="space-y-3">
        <Note>Rejected items keep their reason. This is the only route back: profile similarity queues candidates, a person accepts or dismisses. Hard exclusions are never candidates.</Note>
        {isAdmin && <FormRow><Select value={client} onChange={e => setClient(e.target.value)}><option value="">All clients</option>{clients.map(c => <option key={c}>{c}</option>)}</Select><Button size="sm" variant="outline" onClick={scan}>Scan rejected items</Button></FormRow>}
        {info && <Note>{info}</Note>}
        <DataState error={error} ready={!!data}>{data && (data.length ? (
          <div className="space-y-2">{data.map(i => (
            <div key={i.id} className="rounded-md border border-border p-3">
              <p className="text-xs text-ink-faint">{humanizeToken(i.type)} · {i.client ?? 'Unknown client'}{i.similarity != null ? ` · similarity ${i.similarity} (${humanizeToken(i.method ?? '')})` : ''}{i.decision ? ` · suggested: ${humanStatus(i.decision)}` : ''}</p>
              <h3 className="mt-0.5 text-[13px] font-medium text-ink">{i.title}</h3>
              {i.matched_profile_terms?.length ? <p className="mt-1 text-xs text-ink-muted">Profile terms: {i.matched_profile_terms.join(', ')}</p> : null}
              {i.quote && <p className="mt-1 text-[13px] text-ink-muted">&ldquo;{i.quote}&rdquo;</p>}
              <p className="mt-1 text-xs text-ink-faint">Rejected because: {i.prior_reason ?? 'Not recorded'}{i.url ? '' : ' · No publisher link (cannot be accepted)'}</p>
              {isAdmin && <div className="mt-2 flex gap-1"><Button size="icon" variant="ghost" title="Accept" onClick={() => decide(i.id, true)}><Check size={14} /></Button><Button size="icon" variant="ghost" title="Dismiss" onClick={() => decide(i.id, false)}><X size={14} /></Button></div>}
            </div>
          ))}</div>
        ) : <EmptyState text="No candidate is waiting for review." />)}</DataState>
        <Fail text={fail} />
      </CardContent>
    </Card>
  )
}

// ---- persisted insights: reviewed by a person before they count as published ----------------------------------------------
export function InsightReview({ token, isAdmin }: { token: string; isAdmin: boolean }) {
  const { data, error, reload } = useAsync<StoredInsight[]>(() => listStoredInsights(token), [token])
  const dup = useAsync(() => getNearDuplicates(token), [token])
  const [fail, setFail] = useState('')
  const [info, setInfo] = useState('')
  async function persist() { setFail(''); try { const r = await materializeIntelligence(token); setInfo(`Persisted: ${r.map(x => `${x.client}: ${x.insights_created ?? 0} insights, ${x.opportunities_created ?? 0} opportunities, ${x.alerts_created ?? 0} alerts`).join(' · ')}`); reload() } catch (e) { setFail(message(e)) } }
  async function status(id: string, s: string) { setFail(''); try { await setInsightStatus(token, id, s); reload() } catch (e) { setFail(message(e)) } }
  return (
    <Card>
      <CardHeader><div><CardEyebrow>Insights</CardEyebrow><CardTitle>Stored insights awaiting review</CardTitle></div><Badge>{data?.length ?? 0}</Badge></CardHeader>
      <CardContent className="space-y-3">
        <Note>Rule-derived and generated insights begin as drafts. Each lists the publisher URLs of its evidence; nothing is published until a person changes its status.</Note>
        <FormRow>{isAdmin && <Button size="sm" variant="outline" onClick={persist}>Persist current insights, opportunities and alerts</Button>}</FormRow>
        {info && <Note>{info}</Note>}
        <DataState error={error} ready={!!data}>{data && (data.length ? (
          <div className="space-y-2">{data.map(i => (
            <div key={i.id} className="rounded-md border border-border p-3">
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-ink-faint">{i.client ?? 'No linked client'} · <StatusPill value={i.status} /> · {humanizeToken(i.insight_type ?? '')}{i.generated ? ' · AI-drafted text' : ''}</div>
              <h3 className="mt-1 text-[13px] font-medium text-ink">{i.title}</h3>
              <p className="mt-1 whitespace-pre-line text-[13px] text-ink-muted">{i.body}</p>
              {isAdmin && <div className="mt-2 flex gap-1.5">{['draft', 'published', 'archived'].filter(x => x !== i.status).map(x => <Button key={x} size="sm" variant="outline" onClick={() => status(i.id, x)}>{humanStatus(x)}</Button>)}</div>}
            </div>
          ))}</div>
        ) : <EmptyState text="No insight is stored yet." />)}</DataState>
        <div>
          <h3 className="text-[13px] font-semibold text-ink">Possible near-duplicates (report only)</h3>
          <DataState error={dup.error} ready={!!dup.data}>{dup.data && (dup.data.pairs.length ? dup.data.pairs.map((p, i) => <p key={i} className="text-xs text-ink-faint">{p.similarity}: {p.a.title} ↔ {p.b.title}</p>) : <Note>None at threshold {dup.data.threshold} (needs embedded chunks). {dup.data.note}</Note>)}</DataState>
        </div>
        <Fail text={fail} />
      </CardContent>
    </Card>
  )
}

// ---- engine status, AI usage, evaluation, Xenom capabilities -------------------------------------------------------
function EngineStatus({ token, isAdmin }: { token: string; isAdmin: boolean }) {
  const usage = useAsync<AiUsage>(() => getAiUsage(token), [token])
  const ops = useAsync<Record<string, Record<string, unknown>>>(() => getOperations(token), [token])
  const sets = useAsync<EvalSet[]>(() => listEvalSets(token), [token])
  const runs = useAsync<EvalRunRow[]>(() => listEvalRuns(token), [token])
  const caps = useAsync<XenomCapability[]>(() => listXenomCapabilities(token), [token])
  const [fail, setFail] = useState('')
  const [info, setInfo] = useState('')
  const [setName, setSetName] = useState('')
  const [kind, setKind] = useState('retrieval')
  const [cap, setCap] = useState({ name: '', verification_status: 'UNVERIFIED', source_note: '' })
  async function index(kindName: 'chunks' | 'embed' | 'labels') { setFail(''); try { setInfo(`${kindName}: ${JSON.stringify(await runIndex(token, kindName))}`) } catch (e) { setFail(message(e)) } }
  async function addSet() { setFail(''); try { await createEvalSet(token, { name: setName, kind }); setSetName(''); sets.reload() } catch (e) { setFail(message(e)) } }
  async function run(id: string) { setFail(''); try { await runEvalSet(token, id); runs.reload(); sets.reload() } catch (e) { setFail(message(e)) } }
  async function addCap() { setFail(''); try { await upsertXenomCapability(token, cap); setCap({ name: '', verification_status: 'UNVERIFIED', source_note: '' }); caps.reload() } catch (e) { setFail(message(e)) } }
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><div><CardEyebrow>AI usage</CardEyebrow><CardTitle>Embedding and LLM calls</CardTitle></div><Badge>{usage.data?.total_calls ?? 0} calls</Badge></CardHeader>
        <CardContent className="space-y-2">
          <DataState error={usage.error} ready={!!usage.data}>{usage.data && (usage.data.by_operation.length ? (
            <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full min-w-max border-collapse text-[13px]">
              <thead><tr className="border-b border-border bg-surface-2 text-left text-ink-muted"><th className="px-3 py-2 font-medium">Operation</th><th className="px-3 py-2 font-medium">Model</th><th className="px-3 py-2 font-medium">Calls</th><th className="px-3 py-2 font-medium">Failed</th><th className="px-3 py-2 font-medium">Avg ms</th><th className="px-3 py-2 font-medium">Tokens</th><th className="px-3 py-2 font-medium">Cost USD</th></tr></thead>
              <tbody>{usage.data.by_operation.map(o => <tr key={`${o.operation}-${o.model}`} className="border-b border-border last:border-0"><td className="px-3 py-2">{o.operation}</td><td className="px-3 py-2">{o.model}</td><td className="px-3 py-2">{o.calls}</td><td className="px-3 py-2">{o.failed}</td><td className="px-3 py-2">{o.avg_latency_ms}</td><td className="px-3 py-2">{o.tokens}</td><td className="px-3 py-2">{o.cost_usd}</td></tr>)}</tbody>
            </table></div>
          ) : <Note>No embedding or LLM call has been made.</Note>)}</DataState>
          {usage.data?.recent_errors.map((e, i) => <p key={i} className="text-xs text-ink-faint">{formatDate(e.at)} · {e.operation} · {e.error}</p>)}
          {isAdmin && <FormRow><Button size="sm" variant="outline" onClick={() => index('chunks')}>Chunk accepted articles</Button><Button size="sm" variant="outline" onClick={() => index('labels')}>Label unlabelled articles</Button><Button size="sm" variant="outline" onClick={() => index('embed')}>Embed pending chunks</Button></FormRow>}
          {info && <Note>{info}</Note>}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><div><CardEyebrow>Operations</CardEyebrow><CardTitle>Measured operational facts</CardTitle></div></CardHeader>
        <CardContent className="space-y-1.5">
          <DataState error={ops.error} ready={!!ops.data}>{ops.data && (
            <><Note>{String(ops.data.accuracy_note)}</Note>{Object.entries(ops.data).filter(([, v]) => typeof v === 'object').map(([k, v]) => <p key={k} className="text-xs text-ink-faint"><strong className="text-ink-muted">{humanizeToken(k)}</strong>: {Object.entries(v).map(([kk, vv]) => `${humanizeToken(kk)}: ${vv}`).join(' · ')}</p>)}</>
          )}</DataState>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><div><CardEyebrow>Evaluation</CardEyebrow><CardTitle>Labelled sets and runs</CardTitle></div><Badge>{sets.data?.length ?? 0} sets</Badge></CardHeader>
        <CardContent className="space-y-2">
          <DataState error={sets.error} ready={!!sets.data}>{sets.data && (sets.data.length ? sets.data.map(s => (
            <div key={s.id} className="flex items-center justify-between gap-2 text-[13px]"><span>{s.name} · {humanizeToken(s.kind)} · {s.labels} human labels · {s.runs} runs</span>{isAdmin && <Button size="sm" variant="outline" onClick={() => run(s.id)}>Run</Button>}</div>
          )) : <Note>No evaluation set exists. Metrics are reported as "not measured" until humans label a set.</Note>)}</DataState>
          {runs.data?.slice(0, 5).map(r => <p key={r.id} className="text-xs text-ink-faint">{formatDate(r.created_at)} · {humanizeToken(r.kind)}{r.mode ? ` / ${humanizeToken(r.mode)}` : ''} · {Object.entries(r.metrics).map(([k, v]) => `${humanizeToken(k)}: ${typeof v === 'number' ? v.toFixed(3) : v}`).join(' · ')}</p>)}
          {isAdmin && <FormRow><Input value={setName} onChange={e => setSetName(e.target.value)} placeholder="New evaluation set name" className="max-w-xs" /><Select value={kind} onChange={e => setKind(e.target.value)}>{['retrieval', 'relevance', 'taxonomy'].map(k => <option key={k} value={k}>{humanizeToken(k)}</option>)}</Select><Button size="sm" variant="primary" onClick={addSet} disabled={setName.length < 2}>Create set</Button></FormRow>}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><div><CardEyebrow>Xenom capabilities</CardEyebrow><CardTitle>Capability data for opportunity matching</CardTitle></div><Badge>{caps.data?.length ?? 0}</Badge></CardHeader>
        <CardContent className="space-y-2">
          <Note>An opportunity is marked as verified only when a verified capability named here is literally described by its stated event. With no verified capability stored, every opportunity stays not yet verified.</Note>
          <DataState error={caps.error} ready={!!caps.data}>{caps.data?.map(c => <p key={c.id} className="text-[13px]"><strong className="text-ink">{c.name}</strong> · <StatusPill value={c.verification_status} />{c.source_note ? ` · ${c.source_note}` : ''}</p>)}</DataState>
          {isAdmin && <FormRow><Input value={cap.name} onChange={e => setCap({ ...cap, name: e.target.value })} placeholder="Capability name" className="max-w-xs" /><Select value={cap.verification_status} onChange={e => setCap({ ...cap, verification_status: e.target.value })}><option value="UNVERIFIED">Not yet verified</option><option value="VERIFIED">Verified</option></Select><Input value={cap.source_note} onChange={e => setCap({ ...cap, source_note: e.target.value })} placeholder="Where this was verified" className="max-w-xs" /><Button size="sm" variant="primary" onClick={addCap} disabled={cap.name.length < 2}>Save capability</Button></FormRow>}
        </CardContent>
      </Card>
      <FineTuningPanel token={token} isAdmin={isAdmin} />
      <Fail text={fail} />
    </div>
  )
}

// ---- fine-tuning: dataset export + job records; never a fabricated training result --------------------------------
function FineTuningPanel({ token, isAdmin }: { token: string; isAdmin: boolean }) {
  const datasets = useAsync<FineTuneDataset[]>(() => listFineTuneDatasets(token), [token])
  const jobs = useAsync<FineTuneJob[]>(() => listFineTuneJobs(token), [token])
  const [fail, setFail] = useState('')
  const [name, setName] = useState('')
  const [kind, setKind] = useState('taxonomy_labels')
  const [dataset, setDataset] = useState('')
  const [baseModel, setBaseModel] = useState('')
  async function addDataset() { setFail(''); try { await createFineTuneDataset(token, { name, kind }); setName(''); datasets.reload() } catch (e) { setFail(message(e)) } }
  async function addJob() { setFail(''); try { await createFineTuneJob(token, { dataset_id: dataset, base_model: baseModel }); setBaseModel(''); jobs.reload() } catch (e) { setFail(message(e)) } }
  return (
    <Card>
      <CardHeader><div><CardEyebrow>Fine-tuning</CardEyebrow><CardTitle>Datasets and jobs</CardTitle></div><Badge>{datasets.data?.length ?? 0} datasets</Badge></CardHeader>
      <CardContent className="space-y-3">
        <Note>Datasets are exported only from real, already-reviewed stored data (taxonomy labels or resolved claims) — nothing synthetic. A job is created and versioned here, but no training runs without a configured fine-tuning provider; its status stays honest rather than simulated.</Note>
        <DataState error={datasets.error} ready={!!datasets.data}>{datasets.data && (datasets.data.length ? datasets.data.map(d => <p key={d.id} className="text-[13px]"><strong className="text-ink">{d.name}</strong> · {humanizeToken(d.kind)} · {d.item_count} item(s) · version {d.digest}</p>) : <Note>No dataset has been built yet.</Note>)}</DataState>
        {isAdmin && <FormRow><Input value={name} onChange={e => setName(e.target.value)} placeholder="Dataset name" className="max-w-xs" /><Select value={kind} onChange={e => setKind(e.target.value)}><option value="taxonomy_labels">Taxonomy labels</option><option value="relevance_adjudication">Relevance adjudication</option></Select><Button size="sm" variant="primary" onClick={addDataset} disabled={name.length < 2}>Build dataset</Button></FormRow>}
        <h4 className="text-[13px] font-semibold text-ink">Jobs</h4>
        <DataState error={jobs.error} ready={!!jobs.data}>{jobs.data && (jobs.data.length ? jobs.data.map(j => <p key={j.id} className="text-[13px]">{j.base_model} · <StatusPill value={j.status} />{j.status_reason ? ` — ${j.status_reason}` : ''} · <span className="text-xs text-ink-faint">{formatDate(j.created_at)}</span></p>) : <Note>No fine-tuning job has been created yet.</Note>)}</DataState>
        {isAdmin && <FormRow><Select value={dataset} onChange={e => setDataset(e.target.value)}><option value="">Select dataset…</option>{datasets.data?.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</Select><Input value={baseModel} onChange={e => setBaseModel(e.target.value)} placeholder="Base model" className="max-w-xs" /><Button size="sm" variant="primary" onClick={addJob} disabled={!dataset || baseModel.length < 1}>Create job</Button></FormRow>}
        <Fail text={fail} />
      </CardContent>
    </Card>
  )
}

// ---- the configuration area (PDF s.12): profiles, sources, review, engine ---------------------------------------------------
export function AdminTab({ token, isAdmin }: { token: string; isAdmin: boolean }) {
  const [clients, setClients] = useState<string[]>([])
  useEffect(() => { listClients(token).then(setClients).catch(() => setClients([])) }, [token])
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between"><h2 className="text-lg font-semibold text-ink">Configuration and engine</h2><Badge>{isAdmin ? 'administrator' : 'read-only'}</Badge></div>
      <CapabilityStrip token={token} />
      <TabsRoot defaultValue="profiles">
        <TabsList>
          <TabsTrigger value="profiles">Client profiles</TabsTrigger>
          <TabsTrigger value="sources">Sources</TabsTrigger>
          <TabsTrigger value="review">Review queue</TabsTrigger>
          <TabsTrigger value="engine">Engine &amp; evaluation</TabsTrigger>
        </TabsList>
        <TabsContent value="profiles" className="pt-4"><ProfilesAdmin token={token} isAdmin={isAdmin} /></TabsContent>
        <TabsContent value="sources" className="pt-4"><SourcesAdmin token={token} isAdmin={isAdmin} clients={clients} /></TabsContent>
        <TabsContent value="review" className="space-y-4 pt-4"><ReviewQueue token={token} isAdmin={isAdmin} clients={clients} /><InsightReview token={token} isAdmin={isAdmin} /><RecommendationsPanel token={token} isAdmin={isAdmin} /></TabsContent>
        <TabsContent value="engine" className="pt-4"><EngineStatus token={token} isAdmin={isAdmin} /></TabsContent>
      </TabsRoot>
    </div>
  )
}
