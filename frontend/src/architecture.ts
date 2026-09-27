// Typed client for the PDF-architecture endpoints: profiles, sources admin, engine (capabilities, recall, recommendations,
// evaluation, AI usage), researchers, article detail. Every call maps 1:1 onto an OpenAPI operation (checked by fe_static).
import { request, type Article } from './api'

const authed = (token: string) => ({ headers: { Authorization: `Bearer ${token}` } })
const send = (token: string, method: string, body?: unknown) => ({
  method, headers: { Authorization: `Bearer ${token}` }, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
})

// ---- capabilities / engine ------------------------------------------------------------------------------------------
export type Capability = { status: string; detail?: string | null; [key: string]: unknown }
export type Capabilities = {
  lexical_search: Capability; embeddings: Capability & { provider: string | null; model: string | null; chunks: number; embedded_chunks: number }
  hybrid_search: Capability; llm: Capability & { provider: string | null; model: string | null; operations: string[] }
  taxonomy: Capability & { version: string; labels: string[]; labelled_articles: number; label_rows: number }
}
export const getCapabilities = (token: string) => request<Capabilities>('/engine/capabilities', authed(token))

export type AiUsage = {
  by_operation: { provider: string; model: string; operation: string; calls: number; ok: number; failed: number; avg_latency_ms: number; tokens: number; cost_usd: number }[]
  recent_errors: { at: string; provider: string; model: string; operation: string; error: string | null }[]
  total_calls: number; total_cost_usd: number
}
export const getAiUsage = (token: string) => request<AiUsage>('/engine/ai-usage', authed(token))
export const getOperations = (token: string) => request<Record<string, Record<string, unknown>>>('/engine/evaluation/operations', authed(token))
export const runIndex = (token: string, kind: 'chunks' | 'embed' | 'labels') => request<Record<string, unknown>>(`/engine/index/${kind}`, send(token, 'POST'))

// ---- article detail -------------------------------------------------------------------------------------------------
export type ArticleDetail = {
  article_id: string; title: string | null; summary: string | null; url: string | null; url_note?: string
  published_at: string | null; fetched_at: string | null; author: string | null; status: string; relevance_score: number | null
  source: { name: string; tier: string; source_class: string | null; reliability_score: number | null } | null
  decision: { decision?: string; stage?: string; reason?: string | null; entity?: string | null; matched_terms?: string[]; matched_topics?: string[]; relevance_score?: number } | null
  clients: string[]
  labels: { label: string; method: string; confidence: number | null; evidence: Record<string, unknown> | null }[]
  authors: { name: string; affiliation: string | null; position: number }[]
  claims: { claim_id: string; claim: string; status: string }[]
  chunks: { field: string; index: number; start: number; end: number; embedded_with: string | null }[]
}
export const getArticleDetail = (token: string, id: string) => request<ArticleDetail>(`/articles/${id}/detail`, authed(token))
export const listSavedArticles = (token: string) => request<Article[]>('/articles/saved', authed(token))
export const saveArticle = (token: string, id: string) => request<Article>(`/articles/${id}/saved`, send(token, 'PUT'))
export const unsaveArticle = (token: string, id: string) => request<void>(`/articles/${id}/saved`, send(token, 'DELETE'))
export type RelatedArticles = { status: string; detail?: string; results: (EvidenceHit & { similarity?: number })[] }
export const getRelatedArticles = (token: string, id: string) => request<RelatedArticles>(`/search/articles/${id}/similar`, authed(token))

// ---- news verification check -------------------------------------------------------------------------------------
export type MatchedCoverage = { article_id: string; title: string | null; url: string; source: string | null; tier: string | null; published_at: string | null }
export type ExternalSource = { domain: string; title: string | null; published_at: string | null; author: string | null; fetch_status: 'ok' | 'error'; error: string | null }
export type FactCheckResult = {
  input: string; verdict: 'LIKELY_REAL' | 'LIKELY_FAKE' | 'UNVERIFIED'; reasoning: string
  matched_coverage: MatchedCoverage[]; external_coverage: { title: string; url: string; source: string | null; snippet: string | null; published_at: string | null; provider: string }[]
  checked_against: number; llm_used: boolean; note: string
  external_source: ExternalSource | null
}
export const checkNews = (token: string, input: string) => request<FactCheckResult>('/engine/fact-check', send(token, 'POST', { input }))

// ---- search (modes + filters) ---------------------------------------------------------------------------------------
export type SearchMode = 'lexical' | 'semantic' | 'hybrid'
export type SearchOptions = {
  mode: SearchMode; entity_key?: string; tier?: string; source_class?: string; label?: string; person?: string; technology?: string; date_from?: string
}
export type EvidenceHit = {
  article_id: string; title: string | null; url: string; published_at: string | null; client: string | null; source: string | null
  source_class: string | null; tier: string | null; relevance: { decision?: string; relevance_score?: number; matched_terms?: string[] } | null
  score: number; retrieval: { lexical_rank: number | null; vector_rank: number | null; vector_similarity: number | null; model: string } | null
  spans: { field: string; start: number; end: number; quote: string; terms: string[] }[]
}
export type EvidenceSearch = {
  query: string; count: number; suggested_filters: Record<string, string[]>; mode: SearchMode; mode_requested: SearchMode
  fallback_reason: string | null; results: EvidenceHit[]
}
export function searchEvidence(token: string, query: string, options: SearchOptions, limit = 10) {
  const params = new URLSearchParams({ q: query, limit: String(limit), mode: options.mode })
  for (const key of ['entity_key', 'tier', 'source_class', 'label', 'person', 'technology'] as const) {
    if (options[key]) params.append(key, options[key] as string)
  }
  if (options.date_from) params.append('date_from', new Date(options.date_from).toISOString())
  return request<EvidenceSearch>(`/search/articles/evidence?${params.toString()}`, authed(token))
}

// ---- profiles -------------------------------------------------------------------------------------------------------
export type ProfileData = {
  display_name: string; description: string; source_priority: Record<string, number>; topic_keywords: Record<string, string[]>
  source_weights: Record<string, number>; competitor_notes: Record<string, string>
} & Record<ProfileListField, string[]>
export const PROFILE_GROUPS: { title: string; hint: string; fields: { key: ProfileListField; label: string }[] }[] = [
  { title: 'Identity', hint: 'Names the rule engine treats as the client', fields: [
    { key: 'aliases', label: 'Aliases' }, { key: 'legal_names', label: 'Legal names' }, { key: 'subsidiaries', label: 'Subsidiaries' },
    { key: 'official_domains', label: 'Official domains' }] },
  { title: 'Business', hint: 'What the client makes and where it operates', fields: [
    { key: 'products', label: 'Products / services' }, { key: 'industries', label: 'Industries' }, { key: 'sub_industries', label: 'Sub-industries' },
    { key: 'locations', label: 'Locations' }] },
  { title: 'Keywords', hint: 'Candidate signals; acronyms only count next to context', fields: [
    { key: 'strong_keywords', label: 'Strong (unambiguous) names' }, { key: 'ambiguous_names', label: 'Ambiguous names' },
    { key: 'ambiguous_context', label: 'Disambiguating context' }, { key: 'case_sensitive_acronyms', label: 'Case-sensitive acronyms' },
    { key: 'context_keywords', label: 'Context keywords' }] },
  { title: 'Exclusions', hint: 'Hard negatives reject unless a strong name is in the title; soft negatives subtract points', fields: [
    { key: 'hard_negative_keywords', label: 'Hard exclusions' }, { key: 'negative_keywords', label: 'Soft exclusions' }] },
  { title: 'People', hint: 'Executives and tracked researchers', fields: [{ key: 'executives', label: 'Executives' }, { key: 'people', label: 'Important people' }] },
  { title: 'Intelligence interests', hint: 'Feed competitor/technology entities, taxonomy and recall recovery', fields: [
    { key: 'competitors', label: 'Competitors' }, { key: 'technologies', label: 'Technologies' }, { key: 'ai_model_interests', label: 'AI / model interests' },
    { key: 'research_interests', label: 'Research interests' }] },
  { title: 'Queries and sources', hint: 'Google News discovery queries; publishers that reliably cover the client', fields: [
    { key: 'google_queries', label: 'Google News queries' }, { key: 'search_queries', label: 'Saved search queries' }, { key: 'trusted_sources', label: 'Trusted publishers' }] },
]
export type ProfileListField =
  | 'aliases' | 'legal_names' | 'subsidiaries' | 'official_domains' | 'products' | 'industries' | 'sub_industries' | 'locations'
  | 'strong_keywords' | 'ambiguous_names' | 'ambiguous_context' | 'case_sensitive_acronyms' | 'context_keywords'
  | 'hard_negative_keywords' | 'negative_keywords' | 'executives' | 'people' | 'competitors' | 'technologies' | 'ai_model_interests'
  | 'research_interests' | 'google_queries' | 'search_queries' | 'trusted_sources'
export type ProfileVersion = { version: number; is_active: boolean; created_by: string | null; change_note: string | null; reverted_from: number | null; created_at: string }
export type Profile = { client: string; version: number; origin: 'saved' | 'code'; data: ProfileData; versions: ProfileVersion[] }
export type ImpactExample = { article_id: string; title: string | null; stored_status: string; current: { accepted: boolean; score: number; reason: string | null }; proposed: { accepted: boolean; score: number; reason: string | null } }
export type Impact = { client: string; evaluated_articles: number; would_add: number; would_remove: number; unchanged: number; added_examples: ImpactExample[]; removed_examples: ImpactExample[]; note: string }
export type Suggestion = { id: string; client_key: string; kind: string; value: string; reason: string; example_article_ids: string[] | null; method: string; status: string; created_at: string }
export type AuditEntry = { id: string; actor: string | null; action: string; object_type: string; object_key: string; created_at: string; changes: Record<string, unknown> | null }
export type Job = { job_id: string; client: string; status: string; started_at: string; finished_at?: string; result?: Record<string, number>; error?: string }
export const getProfile = (token: string, client: string) => request<Profile>(`/profiles/${encodeURIComponent(client)}`, authed(token))
export const saveProfile = (token: string, client: string, data: ProfileData, note?: string) => request<Profile>(`/profiles/${encodeURIComponent(client)}`, send(token, 'PUT', { data, note }))
export const previewProfile = (token: string, client: string, data: ProfileData) => request<Impact>(`/profiles/${encodeURIComponent(client)}/preview`, send(token, 'POST', { data }))
export const rollbackProfile = (token: string, client: string, version: number) => request<Profile>(`/profiles/${encodeURIComponent(client)}/rollback`, send(token, 'POST', { version }))
export const startReevaluation = (token: string, client: string) => request<Job>(`/profiles/${encodeURIComponent(client)}/reevaluate`, send(token, 'POST'))
export const getReevaluation = (token: string, client: string, job: string) => request<Job>(`/profiles/${encodeURIComponent(client)}/reevaluate/${job}`, authed(token))
export const listSuggestions = (token: string, client: string) => request<Suggestion[]>(`/profiles/${encodeURIComponent(client)}/suggestions`, authed(token))
export const generateSuggestions = (token: string, client: string) => request<Suggestion[]>(`/profiles/${encodeURIComponent(client)}/suggestions/generate`, send(token, 'POST'))
export const decideSuggestion = (token: string, client: string, id: string, accept: boolean) => request<{ id: string; status: string; profile_version: number | null }>(`/profiles/${encodeURIComponent(client)}/suggestions/${id}`, send(token, 'POST', { accept }))
export const getAudit = (token: string, objectType?: string) => request<AuditEntry[]>(`/profiles/audit?limit=30${objectType ? `&object_type=${objectType}` : ''}`, authed(token))

// ---- sources admin --------------------------------------------------------------------------------------------------
export type FetchConfig = { fetch_mode: string; link_pattern?: string | null; render?: boolean; require_date?: boolean; source_kind?: string | null; query?: string | null; max_results?: number; managed?: boolean }
export type SourceRow = {
  id: string; name: string; canonical_url: string; feed_url: string | null; source_type: string | null; tier: string; is_active: boolean
  source_class: string | null; reliability_score: number | null; priority: number; config: FetchConfig | null; notes: string | null
}
export type SourceInput = { name: string; url: string; source_type: string; tier: string; source_class?: string; reliability_score?: number; priority?: number; config?: FetchConfig; notes?: string; feed_url?: string }
export type DryRun = {
  dry_run?: boolean; fetch_status: string; fetch_error: string | null; documents_fetched?: number; documents_accepted?: number; documents_rejected?: number
  documents_invalid?: number; documents_deduplicated?: number; rejection_reasons?: Record<string, number>
  samples?: { accepted: { title: string; client: string | null; relevance: number; quality: number }[]; rejected: { title: string; stage: string; reason: string }[]; invalid: { title: string; problems: string[] }[] }
}
export type SourceAudit = { audit_id: string; source_id: string; actor: string | null; action: string; created_at: string; before: Record<string, unknown> | null; after: Record<string, unknown> | null }
export type ClientLink = { client: string; priority: number; role: string }
export const listSources = (token: string) => request<SourceRow[]>('/sources', authed(token))
export const createSource = (token: string, body: SourceInput) => request<SourceRow>('/sources', send(token, 'POST', body))
export const updateSource = (token: string, id: string, body: Partial<SourceInput> & { is_active?: boolean }) => request<SourceRow>(`/sources/${id}`, send(token, 'PATCH', body))
export const dryRunSource = (token: string, body: Record<string, unknown>) => request<DryRun>('/sources/dry-run', send(token, 'POST', body))
export const fetchSourceNow = (token: string, id: string) => request<{ articles_created: number; documents_found: number }>(`/sources/${id}/fetch`, send(token, 'POST'))
export const getSourceAudit = (token: string, id?: string) => request<SourceAudit[]>(`/sources/audit?limit=20${id ? `&source_id=${id}` : ''}`, authed(token))
export const rollbackSource = (token: string, id: string, audit_id: string) => request<SourceRow>(`/sources/${id}/rollback`, send(token, 'POST', { audit_id }))
export const getSourceClients = (token: string, id: string) => request<ClientLink[]>(`/sources/${id}/clients`, authed(token))
export const setSourceClients = (token: string, id: string, links: ClientLink[]) => request<ClientLink[]>(`/sources/${id}/clients`, send(token, 'PUT', links))

// ---- review queue: recall recovery + recommendations ----------------------------------------------------------------
export type RecallItem = { id: string; type: string; status: string; article_id: string | null; title: string | null; url: string | null; article_status: string | null
  client?: string; similarity?: number; method?: string; matched_profile_terms?: string[]; prior_reason?: string | null; decision?: string; reason?: string; quote?: string | null }
export const getRecall = (token: string) => request<RecallItem[]>('/engine/recall', authed(token))
export const scanRecall = (token: string, client?: string) => request<{ method: string; queued: Record<string, number>; candidates_scored: number; threshold: number; detail?: string }>('/engine/recall/scan', send(token, 'POST', { client: client || null }))
export const decideRecall = (token: string, id: string, accept: boolean) => request<{ status: string }>(`/engine/recall/${id}`, send(token, 'POST', { accept }))
export type Recommendation = {
  id: string; client: string; title: string; action: string; rationale: string; confidence: number | null; confidence_basis: Record<string, unknown> | null
  article_ids: string[] | null; claim_ids: string[] | null; origin: string; generated: boolean; status: string; reviewed_by: string | null; created_at: string
}
export const getRecommendations = (token: string, client?: string) => request<Recommendation[]>(`/engine/recommendations${client ? `?client=${encodeURIComponent(client)}` : ''}`, authed(token))
export const syncRecommendations = (token: string, client: string) => request<{ created: number; updated: number; opportunities: number }>('/engine/recommendations/sync', send(token, 'POST', { client }))
export const setRecommendationStatus = (token: string, id: string, status: string) => request<Recommendation>(`/engine/recommendations/${id}`, send(token, 'PATCH', { status }))
export type XenomCapability = { id: string; name: string; description: string | null; category: string | null; verification_status: string; source_note: string | null }
export const listXenomCapabilities = (token: string) => request<XenomCapability[]>('/engine/xenom/capabilities', authed(token))
export const upsertXenomCapability = (token: string, body: { name: string; description?: string; verification_status: string; source_note?: string }) => request<{ id: string }>('/engine/xenom/capabilities', send(token, 'PUT', body))

// ---- evaluation -----------------------------------------------------------------------------------------------------
export type EvalSet = { id: string; name: string; kind: string; description: string | null; labels: number; runs: number }
export type EvalRunRow = { id: string; set_id: string | null; kind: string; mode: string | null; metrics: Record<string, unknown>; corpus_digest: string | null; created_at: string }
export const listEvalSets = (token: string) => request<EvalSet[]>('/engine/evaluation/sets', authed(token))
export const createEvalSet = (token: string, body: { name: string; kind: string; description?: string }) => request<{ id: string }>('/engine/evaluation/sets', send(token, 'POST', body))
export const runEvalSet = (token: string, id: string) => request<{ runs: EvalRunRow[] }>(`/engine/evaluation/sets/${id}/run`, send(token, 'POST', {}))
export const listEvalRuns = (token: string) => request<EvalRunRow[]>('/engine/evaluation/runs', authed(token))

// ---- fine-tuning (datasets + jobs; no training performed here) ------------------------------------------------------
export type FineTuneDataset = { id: string; name: string; kind: string; item_count: number; digest: string; created_by: string | null; created_at: string }
export type FineTuneJob = { id: string; dataset_id: string; base_model: string; status: string; status_reason: string | null; provider: string | null; created_at: string }
export const listFineTuneDatasets = (token: string) => request<FineTuneDataset[]>('/engine/fine-tuning/datasets', authed(token))
export const createFineTuneDataset = (token: string, body: { name: string; kind: string; label?: string }) => request<FineTuneDataset>('/engine/fine-tuning/datasets', send(token, 'POST', body))
export const listFineTuneJobs = (token: string) => request<FineTuneJob[]>('/engine/fine-tuning/jobs', authed(token))
export const createFineTuneJob = (token: string, body: { dataset_id: string; base_model: string; config?: Record<string, unknown> }) => request<FineTuneJob>('/engine/fine-tuning/jobs', send(token, 'POST', body))

// ---- researchers ----------------------------------------------------------------------------------------------------
export type Researcher = {
  person_id: string; name: string; seed_note: string | null; documents: number; latest_document: string | null; evidence_status?: string; detail?: string
  organisations: { organisation: string; verification_status: string; evidence_count: number }[]
  areas: { term: string; kind: string; documents: number }[]; linked_clients: string[]
  recent_work?: { article_id: string; title: string | null; url: string; published_at: string | null; source: string | null; tier: string | null }[]
}
export type ResearcherList = { total: number; items: Researcher[]; status?: string; detail?: string }
export type SimilarResult = Researcher & { similarity: number; shared_terms: string[]; relevance_in_context: { context: string; documents: { article_id: string; title: string | null; url: string; matched_terms: string[] }[]; detail?: string } | null }
export type Similar = { person: string; method: string | null; context?: string | null; results: SimilarResult[]; status?: string; detail?: string }
export const listResearchers = (token: string, q?: string) => request<ResearcherList>(`/intelligence/researchers${q ? `?q=${encodeURIComponent(q)}` : ''}`, authed(token))
export const getResearcher = (token: string, id: string) => request<Researcher>(`/intelligence/researchers/${id}`, authed(token))
export const getSimilarResearchers = (token: string, id: string, context?: string) => request<Similar>(`/intelligence/researchers/${id}/similar${context ? `?context=${encodeURIComponent(context)}` : ''}`, authed(token))
export const seedResearchers = (token: string, people: { name: string; organisation?: string; areas?: string[] }[]) => request<{ created: number; existing: number }>('/intelligence/researchers/seed', send(token, 'POST', { people }))

// ---- persisted rule-derived intelligence (draft -> published / archived) ---------------------------------------------
export type StoredInsight = { id: string; client: string | null; title: string; body: string | null; insight_type: string | null; priority: number | null; status: string; generated: boolean; created_at: string }
export const listStoredInsights = (token: string, status?: string) => request<StoredInsight[]>(`/engine/insights${status ? `?status=${status}` : ''}`, authed(token))
export const setInsightStatus = (token: string, id: string, status: string) => request<{ id: string; status: string }>(`/engine/insights/${id}`, send(token, 'PATCH', { status }))
export const materializeIntelligence = (token: string, client?: string) => request<Record<string, unknown>[]>('/engine/materialize', send(token, 'POST', { client: client || null }))
export const getNearDuplicates = (token: string) => request<{ threshold: number; pairs: { similarity: number; a: { title: string | null; url: string }; b: { title: string | null; url: string } }[]; note: string }>('/engine/near-duplicates', authed(token))
