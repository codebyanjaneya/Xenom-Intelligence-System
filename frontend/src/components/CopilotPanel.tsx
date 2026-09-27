import { FormEvent, useState } from 'react'
import { Bot, Send, ExternalLink, ArrowLeft } from 'lucide-react'
import { request } from '../api'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'

type Citation = { title: string; url: string; date?: string | null; quote?: string | null }
type CopilotResponse = { query: string; intent: string; client?: string | null; answer: string; insufficient_evidence: boolean; citations: Citation[]; data: Record<string, unknown> }

function StructuredResults({ data }: { data: Record<string, unknown> }) {
  const groups = ['opportunities', 'insights', 'alerts', 'records', 'items', 'key_events']
    .flatMap(key => Array.isArray(data[key]) ? [{ key, items: data[key] as Record<string, unknown>[] }] : [])
  if (!groups.length) return null
  return <div className="mt-3 space-y-3 border-t border-border pt-3">
    {groups.map(group => <section key={group.key}>
      <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">{group.key.replace('_', ' ')}</p>
      <div className="space-y-1.5">
        {group.items.slice(0, 6).map((item, itemIndex) => {
          const title = String(item.title || item.name || item.label || item.event_type || 'Verified engine result')
          const detailValue = item.rationale || item.explanation || item.body || item.summary || item.when
          const detail = detailValue == null ? null : String(detailValue)
          const url = typeof item.url === 'string' ? item.url : null
          return <div className="rounded-md border border-border bg-surface px-2.5 py-2" key={`${group.key}-${itemIndex}`}>
            {url ? <a href={url} target="_blank" rel="noreferrer" className="text-xs font-medium text-accent hover:underline">{title}</a> : <p className="text-xs font-medium text-ink">{title}</p>}
            {detail && <p className="mt-0.5 line-clamp-2 text-xs text-ink-muted">{detail}</p>}
          </div>
        })}
      </div>
    </section>)}
  </div>
}

const prompts = ['What changed for JCB?', 'What opportunities exist for ACE?', 'Show recent research', "Give me today's intelligence summary"]

export function CopilotPanel({ token, name = 'Xenom' }: { token: string; name?: string }) {
  const [query, setQuery] = useState('')
  const [messages, setMessages] = useState<CopilotResponse[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)

  async function submit(event?: FormEvent, value = query) {
    event?.preventDefault()
    const clean = value.trim()
    if (!clean || loading) return
    setLoading(true); setError('')
    try {
      const result = await request<CopilotResponse>('/copilot', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify({
        query: clean,
        history: messages.slice(-6).map(message => ({ query: message.query, answer: message.answer, intent: message.intent, client: message.client || null })),
      }) })
      setMessages(current => [...current, result]); setQuery('')
    } catch (err) { setError(err instanceof Error ? err.message : 'Copilot is unavailable.') }
    finally { setLoading(false) }
  }

  if (!open) return <button onClick={() => setOpen(true)} className="fixed bottom-6 right-6 z-40 flex items-center gap-2 rounded-full border border-accent/20 bg-surface px-4 py-3 text-left text-sm font-semibold text-ink shadow-lg transition hover:-translate-y-0.5 hover:shadow-xl" aria-label="Open platform assistant"><Bot size={17} className="text-accent" />Ask your assistant</button>

  return <div className="fixed bottom-6 right-6 z-40 w-[min(440px,calc(100vw-2rem))]"><Card className="overflow-hidden shadow-2xl"><CardHeader className="flex-row items-center justify-between"><CardTitle><span className="mr-2 inline-flex rounded-md bg-accent-soft p-1.5 text-accent"><Bot size={15} /></span>Your platform assistant</CardTitle><button onClick={() => setOpen(false)} className="inline-flex items-center gap-1 rounded-md p-1 text-xs text-ink-faint hover:bg-surface-2 hover:text-ink" aria-label="Go back to dashboard"><ArrowLeft size={16} />Back</button></CardHeader><CardContent className="space-y-4">
    <div className="flex flex-wrap gap-2">{prompts.map(prompt => <button key={prompt} onClick={() => { setQuery(prompt); void submit(undefined, prompt) }} className="rounded-md border border-border px-2.5 py-1.5 text-xs text-ink-muted hover:border-accent/40 hover:text-accent">{prompt}</button>)}</div>
    {messages.length > 0 && <div className="max-h-[32rem] space-y-3 overflow-y-auto">{messages.map((message, index) => <article className="rounded-md border border-border bg-surface-2 p-3" key={`${message.query}-${index}`}><div className="mb-2 text-xs font-medium text-ink-faint">{message.query}</div><p className="text-sm leading-6 text-ink">{message.answer}</p>{message.insufficient_evidence && <p className="mt-2 text-xs text-amber-700">Insufficient evidence in the stored corpus.</p>}<StructuredResults data={message.data} />{message.citations.length > 0 && <div className="mt-3 space-y-2 border-t border-border pt-3">{message.citations.map((citation, citationIndex) => <a className="flex items-start gap-2 text-xs text-accent hover:underline" href={citation.url} target="_blank" rel="noreferrer" key={`${citation.url}-${citationIndex}`}><ExternalLink size={13} className="mt-0.5 shrink-0" /><span><b>{citation.title}</b>{citation.date && <span className="ml-2 text-ink-faint">{citation.date}</span>}{citation.quote && <span className="mt-1 block text-ink-muted">{citation.quote}</span>}</span></a>)}</div>}</article>)}</div>}
    {messages.length === 0 && !loading && <div className="rounded-md border border-accent/15 bg-accent-soft/40 p-3"><div className="mb-1 flex items-center gap-2 text-xs font-medium text-accent"><Bot size={14} />Assistant</div><p className="text-sm leading-6 text-ink">Hi {name}, how can I assist you today?</p></div>}
    {error && <p className="text-xs text-red-600">{error}</p>}
    <form onSubmit={submit} className="flex gap-2"><Input value={query} onChange={event => setQuery(event.target.value)} placeholder="Ask a question about verified intelligence…" /><Button type="submit" variant="primary" disabled={loading || !query.trim()}>{loading ? 'Thinking…' : 'Ask'}<Send size={14} /></Button></form>
  </CardContent></Card></div>
}
