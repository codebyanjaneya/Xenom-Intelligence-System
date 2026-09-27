const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export type Evidence = {
  source_url: string
  provider: string
  title: string
  snippet: string
  published_at: string | null
  discovered_at: string
  relevance_score: number
  page_title?: string | null
  content_span?: string | null
}

export type Entity = {
  canonical_name: string
  canonical_key: string
  entity_type: string
  mention: string
  mentions: string[]
  evidence: Evidence[]
  verification_status: string
}

export type Event = {
  event_key: string
  title: string
  event_type: string
  evidence: Evidence[]
  event_time: string | null
  event_time_precision: 'exact' | 'day' | 'unknown'
  event_time_source: 'article_text' | 'unknown'
  verification_status: string
  article_ids?: string[]
  source_count?: number
}

export type EntityEventRelationship = {
  entity_key: string
  event_key: string
  relationship_type: string
  evidence: Evidence[]
}

export type TimelineEntry = {
  event_key: string
  position: number
  ordering_time: string
  ordering_time_source: 'event_time' | 'publication_fallback' | 'discovery_fallback'
}

export type TemporalRelationship = {
  earlier_event_key: string
  later_event_key: string
  relationship_type: 'BEFORE' | 'AFTER' | 'SAME_TIME'
  evidence: Evidence[]
}

export type Insight = {
  insight_id: string
  status?: string
  limitations?: string[]
  insight_type: string
  rule_id: string
  client_key: string
  client_name: string
  title: string
  explanation: string
  event_keys: string[]
  article_ids: string[]
  evidence: Evidence[]
  strength: number
  recency_days: number | null
  unavailable: string[]
}

export type Opportunity = {
  opportunity_id: string
  client_key: string
  client_name: string
  title: string
  rationale: string
  rule_id: string
  interpretation: string
  status: string
  capability_match: string
  capability: string | null
  opportunity_score: number
  recommended_action: string
  action_basis: string
  review_status: string
  event_keys: string[]
  article_ids: string[]
  evidence: Evidence[]
}

export type Alert = {
  alert_id: string
  rule_id: string
  severity: 'info' | 'warning' | 'critical'
  client_key: string
  client_name: string
  title: string
  body: string
  event_keys: string[]
  article_ids: string[]
  evidence: Evidence[]
  as_of: string | null
}

export type BriefingEvent = {
  event_key: string
  event_type: string
  title: string
  when: string | null
  when_source: string
  publishers: string[]
  article_ids: string[]
  evidence: Evidence[]
}

export type Briefing = {
  scope: Record<string, string>
  as_of: string | null
  as_of_basis: string
  generated_from: Record<string, number>
  summary: string
  key_events: BriefingEvent[]
  entities: { canonical_key: string; canonical_name: string; type: string; article_count: number }[]
  sources: { publisher?: string; source_class?: string; articles: number }[]
  insights: Insight[]
  opportunities: Opportunity[]
  alerts: Alert[]
  limitations: string[]
}

export type IntelligenceResponse = {
  query: string
  provider: string
  source_result_count: number
  entities: Entity[]
  events: Event[]
  relationships: EntityEventRelationship[]
  timeline: TimelineEntry[]
  temporal_relationships: TemporalRelationship[]
  insights?: Insight[]
  opportunities?: Opportunity[]
  alerts?: Alert[]
  raw_event_count?: number
}

export type Claim = {
  claim_id: string
  claim_text: string
  entity_key: string | null
  event_key: string | null
  evidence: Evidence[]
  status: 'unreviewed' | 'verified' | 'rejected'
  provenance_status?: 'VERIFIED_IN_STORED_CORPUS' | 'UNRESOLVED' | 'UNCHECKED'
}

export type RegistryEntity = {
  id: string
  canonical_name: string
  canonical_key: string
  entity_type: string
  status: string
}

export type ApiError = { error?: { message?: string } }

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
  })
  const body = await response.json().catch(() => ({})) as ApiError
  if (!response.ok) throw new Error(body.error?.message ?? `Request failed (${response.status})`)
  return body as T
}

export function login(email: string, password: string) {
  return request<{ access_token: string; user: { email: string; role: string; name: string | null } }>('/auth/login', {
    method: 'POST', body: JSON.stringify({ email, password }),
  })
}

export function getCurrentUser(token: string) {
  return request<{ id: string; email: string; role: string; name: string | null; is_active: boolean }>('/auth/me', {
    headers: { Authorization: `Bearer ${token}` },
  })
}

export function logout(token: string) {
  return request<void>('/auth/logout', { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
}

export function extractIntelligence(token: string, query: string, limit = 10) {
  return request<IntelligenceResponse>('/intelligence/extract', {
    method: 'POST', headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ query, limit }),
  })
}

export function createClaim(token: string, payload: { query: string; claim_text: string; entity_key?: string; event_key?: string; evidence: Evidence[] }) {
  return request<Claim>('/claims', {
    method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify(payload),
  })
}

export function updateClaimStatus(token: string, claimId: string, status: Claim['status']) {
  return request<Claim>(`/claims/${claimId}/status`, {
    method: 'PATCH', headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify({ status }),
  })
}

export function listEntities(token: string) {
  return request<RegistryEntity[]>('/entities', { headers: { Authorization: `Bearer ${token}` } })
}

export function listClaims(token: string) {
  return request<Claim[]>('/claims', { headers: { Authorization: `Bearer ${token}` } })
}

export type Article = {
  id: string
  title: string | null
  url: string
  canonical_url?: string | null
  image_url?: string | null
  source_name: string | null
  published_at: string | null
  fetched_at: string | null
  summary: string | null
  source_id: string | null
  entity_names: string[]  // Multiple clients this article belongs to
  topic?: string  // Industry/topic classification
  quality_score?: number | null
}

export function getArticles(token: string, entityKey?: string, sourceName?: string, limit = 500, offset = 0) {
  const params = new URLSearchParams()
  if (offset) params.append('offset', offset.toString())
  if (entityKey) params.append('entity_key', entityKey)
  if (sourceName) params.append('source_name', sourceName)
  params.append('limit', limit.toString())
  return request<Article[]>(`/articles?${params}`, { headers: { Authorization: `Bearer ${token}` } })
}

export function listClients(token: string) {
  return request<string[]>('/intelligence/clients', { headers: { Authorization: `Bearer ${token}` } })
}

export type ClientSuggestion = { name: string; domain: string; url: string; snippet: string; provider: string }
export type OnboardedClient = { client_key: string; canonical_name: string; official_domain: string; description: string; evidence: ClientSuggestion[]; status: string }
export const suggestClients = (token: string, query: string) => request<ClientSuggestion[]>(`/clients/suggest?q=${encodeURIComponent(query)}`, { headers: { Authorization: `Bearer ${token}` } })
export const onboardClient = (token: string, query: string, website?: string) => request<OnboardedClient>('/clients/onboard', {
  method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify({ query, website: website || null }),
})

export function getClientBriefing(token: string, client: string) {
  return request<Briefing>(`/intelligence/clients/${encodeURIComponent(client)}/briefing?limit=500`, {
    headers: { Authorization: `Bearer ${token}` },
  })
}

const authed = (token: string) => ({ headers: { Authorization: `Bearer ${token}` } })

export function getFeed(token: string, limit = 24) {
  return request<Article[]>(`/articles/feed?limit=${limit}`, authed(token))
}

export type EvidenceQuote = { field: string; start: number; end: number; quote: string }
export type SignalRef = {
  article_id: string; title: string | null; url: string; published_at: string | null; source: string | null
  publisher: string | null; clients: string[]; matched_terms: string[]; evidence: EvidenceQuote
}
export type ThemeResult = {
  key: string; label: string; status: string; article_count: number; by_client: Record<string, number>; signals: SignalRef[]
  discovery: { configured: boolean; phrase?: string; queries?: number; cadence_minutes?: number | null; status?: string; last_attempt_at?: string | null; sources_registered?: number; ingestion_path?: string; reason?: string }
}
export type ThemeView = { scope: string; themes_with_evidence: string[]; themes_without_evidence: string[]; themes: ThemeResult[]; excluded_without_clean_evidence: number; note: string }
export type IndustryView = {
  scope: string
  clients: { client: string; accepted_articles: number; industry_terms: { term: string; article_count: number; signals: SignalRef[] }[] }[]
  registered_industries: string[]
  industry_entities: { industry: string; status: string; article_count: number; by_client: Record<string, number>; signals: SignalRef[] }[]
  taxonomy_labels: Record<string, number>
  topic_coverage: { accepted_articles: number; stored_labelled: number; stored_unlabelled: number; unlabelled_the_current_classifier_would_label: number; stored_labels_reproduced_by_current_classifier: number; explanation: string }
  note: string
}
export type Development = { type: string; title: string | null; url: string; published_at: string | null; source: string | null; quote: string; confidence: number | null }
export type DimensionEvidence = { quote: string; url: string; date: string | null; source: string | null; confidence: number | null } | null
export type ComparisonDimension = { client_evidence: DimensionEvidence; competitor_evidence: DimensionEvidence; status: string }
export type CompetitorImpact = {
  development: { type: string; title: string | null; url: string; date: string | null; source: string | null }
  why_it_matters: string; potential_risk: string
  potential_opportunity: { text: string; status: string; capability_match: string }
  recommended_action: { text: string; review_status: string }
}
export type CompetitorEntry = {
  competitor: string; competitor_of: string | null
  profile: { name: string; status: string; relationship_status: string; confidence: number | null; basis: string[]; domain_industry: string }
  article_count: number; signals: SignalRef[]; developments: Development[]
  comparison: Record<string, ComparisonDimension>; impact: CompetitorImpact[]
  relationship_status: string; basis: string[]; coverage: string | null
}
export type CompetitorView = {
  configured: boolean; competitors: CompetitorEntry[]
  unverified_competitor_language_articles: number; reason: string; how_to_configure: string
}
export type ClientSummaryView = {
  clients: { client: string; accepted_articles: number; latest_published_at: string | null; publishers: number
             source_classes: Record<string, number>; topic_labels: Record<string, number> }[]
  total_accepted: number; total_rejected: number
}
export type SourceStatus = {
  source_id: string; name: string; source_type: string | null; tier: string; url: string; configured_active: boolean
  health_status: string; last_checked_at: string | null; last_health_error: string | null; consecutive_failures: number
  productivity_state: string; accepted: number; rejected: number; acceptance_rate: number | null
  latest_accepted_published_at: string | null; clients: string[]
}
export type SourceStatusReport = {
  total: number; by_health: Record<string, number>; by_productivity: Record<string, number>; sources: SourceStatus[]; note: string
}
export type PipelineStatus = {
  scheduler: { enabled: boolean; running: boolean; interval_minutes: number; last_started_at: string | null; next_run_at: string | null }
  sources_total: number; sources_active: number; accepted_total: number; rejected_total: number
  last_cycle: Record<string, string | number | null> | null; next_cycle_at: string | null
  rejection_reasons?: Record<string, number>
  latest_article_ingested_at: string | null; hours_since_latest_ingest: number | null
  health_checks_recorded: number; continuous_updates: string; explanation: string
  clients: { client: string; accepted_articles: number; latest_published_at: string | null; hours_since_latest_ingest: number | null }[]
}

export type ResearchRecord = {
  kind: string // 'paper' | 'model'
  title: string | null; url: string; published_at: string | null; publisher: string | null
  authors: string[]; authors_status: string; institutions: string[]; institutions_status: string
  evidence: EvidenceQuote
  metrics: Record<string, number | string | null> // real, source-stated only (HF downloads/likes, OpenAlex cited_by_count)
}
export type ResearchView = {
  status: string
  records: ResearchRecord[]
  papers: ResearchRecord[]
  models: ResearchRecord[]
  reason: string
  interface: { required: string[]; not_yet_stored: string[]; how_to_add: string }
}
export type DigestItem = { kind: string; title: string | null; url: string; published_at: string | null; source: string | null; theme: string | null; evidence: EvidenceQuote | null }
export type DigestSection = { key: string; label: string; status: string; article_count: number; signals: SignalRef[] }
export type MorningDigest = {
  generated_at: string
  sections: DigestSection[]
  researchers: { person_id: string; name: string; documents: number; organisations: { organisation: string; verification_status: string }[]; latest_document: string | null }[]
  worth_your_attention: DigestItem[]
  note: string
}
export const getDigest = (token: string) => request<MorningDigest>('/intelligence/digest', authed(token))
export const getResearch = (token: string) => request<ResearchView>('/intelligence/research', authed(token))
export const getClientSummary = (token: string) => request<ClientSummaryView>('/intelligence/clients/summary', authed(token))
export const getTechnology = (token: string) => request<ThemeView>('/intelligence/technology?per_theme=5', authed(token))
export const getIndustry = (token: string) => request<IndustryView>('/intelligence/industry?per_term=2', authed(token))
export const getCompetitors = (token: string, client?: string) => request<CompetitorView>(`/intelligence/competitors${client ? `?client=${encodeURIComponent(client)}` : ''}`, authed(token))
export const getSourceStatus = (token: string) => request<SourceStatusReport>('/intelligence/sources', authed(token))
export const getPipelineStatus = (token: string) => request<PipelineStatus>('/intelligence/pipeline/status', authed(token))
