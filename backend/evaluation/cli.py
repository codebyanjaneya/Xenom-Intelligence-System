"""python -m evaluation.cli sample|report [--manifest P] [--out-dir D] [--labels F]

Only SELECTs, inside a READ ONLY transaction.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from . import relevance as rel
from . import event_quality as eq
from . import search_quality as sq
from .corpus import DEFAULT_MANIFEST, load_manifest, verify_corpus

SQL = """select a.id::text, a.title, a.processing_status, a.score_reason, s.name, s.source_type
         from articles a left join sources s on s.id = a.source_id order by a.id"""


def to_row(r) -> dict:
    aid, title, status, reason, sname, stype = r
    try:
        ev = json.loads(reason) if reason else {}
    except ValueError:
        ev = {}
    return {
        "article_id": aid, "title": title, "source": sname or ev.get("source_domain") or "",
        "client": ev.get("entity") or "UNKNOWN",  # rejected rows: first candidate the engine considered
        "source_class": ev.get("source_type") or stype or "unknown",
        "current_decision": "accept" if status == "accepted" else "reject",
        "_status": status,
    }


async def fetch(url: str) -> list[dict]:
    e = create_async_engine(url)
    async with e.connect() as c:
        await c.execute(text("SET TRANSACTION READ ONLY"))
        rows = [to_row(r) for r in (await c.execute(text(SQL))).all()]
    await e.dispose()
    return rows


async def run_queries(url: str, queries: list[dict], limit: int) -> dict:
    """Run each query through the real search service inside one READ ONLY transaction."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.article_search import search_articles

    e = create_async_engine(url)
    out: dict = {}
    async with AsyncSession(e) as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        for q in queries:
            r = await search_articles(s, q["query"], entity_key=q.get("entity_key"), source=q.get("source"), limit=limit)
            out[q["id"]] = [str(h.article_id) for h in r.results]
    await e.dispose()
    return out


async def run_candidates(url: str, queries: list[dict], limit: int) -> dict:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.article_search import search_articles

    e = create_async_engine(url)
    out: dict = {}
    async with AsyncSession(e) as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        for q in queries:
            r = await search_articles(s, q["query"], entity_key=q.get("entity_key"), source=q.get("source"), limit=limit)
            out[q["id"]] = [{"article_id": str(h.article_id), "client": h.client, "title": h.title} for h in r.results]
    await e.dispose()
    return out


async def run_event_rows(url: str) -> list[dict]:
    """Every event the extractor produces for each client (read-only), as rows for human labelling."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.config.clients import get_all_clients
    from app.services.client_engine import client_intelligence

    e = create_async_engine(url)
    rows: list[dict] = []
    async with AsyncSession(e) as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        for client in get_all_clients():
            for ev in (await client_intelligence(s, client, limit=500)).events:
                rows.append({"event_id": ev.event_key, "client": client, "article_url": ev.evidence[0].source_url,
                             "event_type": ev.event_type, "event_time": ev.event_time.isoformat() if ev.event_time else "",
                             "source_count": ev.source_count, "sentence": ev.title})
    await e.dispose()
    return rows


async def run_trace_audit(url: str) -> dict:
    """Read-only: every evidence carrier (client views, search evidence, briefings, stored claims) must resolve to a
    stored article, its canonical URL and exact stored text."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.config.clients import get_all_clients
    from app.services.article_evidence import search_with_evidence
    from app.services.client_engine import client_briefing, client_intelligence
    from app.services.trace_audit import audit_architecture, audit_briefing, audit_response, audit_search_evidence, audit_stored_claims

    e = create_async_engine(url)
    out: dict = {}
    queries = [q["query"] for q in sq.load_query_set(Path(__file__).parent / "data" / "search_queries.json")]
    async with AsyncSession(e) as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        for client in get_all_clients():
            out[client] = await audit_response(s, await client_intelligence(s, client, limit=500))
            out[f"briefing:{client}"] = await audit_briefing(s, await client_briefing(s, client, limit=500))
        hits, spans_v = [], []
        for query in queries:
            found = await search_with_evidence(s, query, limit=10)
            hits.extend(found.results)
        out["search_evidence"] = await audit_search_evidence(s, hits)
        out["claims_historical"] = await audit_stored_claims(s)
        out["architecture"] = await audit_architecture(s)
    await e.dispose()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sample", "report", "search-eval", "search-labels-sheet", "search-labels-apply", "trace-audit", "event-labels-sheet", "event-eval"])
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--out-dir", default=str(Path(__file__).parent / "data"))
    ap.add_argument("--labels")
    ap.add_argument("--no-corpus-guard", action="store_true", help="trace-audit only: audit any database (e.g. after an ingestion cycle)")
    ap.add_argument("--labels-csv", default=str(Path(__file__).parent / "data" / "search_labelling.csv"))
    ap.add_argument("--queries", default=str(Path(__file__).parent / "data" / "search_queries.json"))
    ap.add_argument("--k", type=int, nargs="+", default=list(sq.DEFAULT_KS))
    ap.add_argument("--result-limit", type=int, default=50)
    ap.add_argument("--seed", type=int, default=rel.DEFAULT_SEED)
    ap.add_argument("--per-stratum", type=int, default=rel.DEFAULT_PER_STRATUM)
    ap.add_argument("--min-labels", type=int, default=rel.DEFAULT_MIN_LABELS_PER_CLIENT)
    a = ap.parse_args(argv)

    from app.config.settings import get_settings
    if a.cmd == "trace-audit" and a.no_corpus_guard:
        live, man, statuses, digest = [], None, {}, "unguarded"   # traceability holds for any corpus; evaluation never skips the guard
    else:
        live = asyncio.run(fetch(get_settings().database_url))
        man = load_manifest(Path(a.manifest))
        statuses = {r["article_id"]: r["_status"] for r in live}
        digest = verify_corpus(man, statuses)  # raises CorpusDriftError -> nothing is evaluated
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"seed": a.seed, "per_stratum": a.per_stratum, "min_labels_per_client": a.min_labels,
              "strata": "client x decision, round-robin over source_class", "positive_class": "accept"}
    if a.cmd in ("event-labels-sheet", "event-eval"):
        sheet = out / "event_labelling.csv"
        events = asyncio.run(run_event_rows(get_settings().database_url))
        if a.cmd == "event-labels-sheet":
            eq.write_event_sheet(sheet, events)
            print(f"corpus OK; wrote {sheet} ({len(events)} events, all labels blank)")
            return 0
        rows = eq.read_event_labels(sheet, {e["event_id"] for e in events})
        rep = {"corpus_digest": digest, "generated_at": now, "metrics": eq.compute_event_metrics(rows)}
        (out / "event_eval_report.json").write_text(json.dumps(rep, indent=2))
        print(json.dumps(rep["metrics"], indent=2))
        return 0
    if a.cmd == "trace-audit":
        report = asyncio.run(run_trace_audit(get_settings().database_url))
        (out / "trace_audit_report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps({k: {x: v[x] for x in v if x != "violations"} | {"violations": len(v["violations"])}
                          for k, v in report.items()}, indent=2))
        # historical (fixture-era) claims are reported but do not fail the audit
        return 1 if any(v["violations"] for k, v in report.items() if k != "claims_historical") else 0
    if a.cmd == "search-labels-sheet":
        queries = sq.load_query_set(a.queries, man.ids)
        cands = asyncio.run(run_candidates(get_settings().database_url, queries, 10))
        sq.write_labelling_csv(a.labels_csv, queries, cands)
        print(f"corpus OK; wrote {a.labels_csv} ({len(queries)} queries)")
        return 0
    if a.cmd == "search-labels-apply":
        labelled = sq.apply_labels_csv(a.labels_csv, sq.load_query_set(a.queries, man.ids), man.ids)
        dest = out / "search_queries_labelled.json"
        dest.write_text(json.dumps({"queries": labelled}, indent=2), encoding="utf-8")
        print(f"labelled {sum(q['labelled'] for q in labelled)}/{len(labelled)} queries -> {dest}")
        print(f"next: python -m evaluation.cli search-eval --queries {dest}")
        return 0
    if a.cmd == "search-eval":
        queries = sq.load_query_set(a.queries, man.ids)
        ranked = asyncio.run(run_queries(get_settings().database_url, queries, a.result_limit))
        params = {"ks": a.k, "result_limit": a.result_limit, "queries_file": str(a.queries)}
        per_query = [sq.evaluate_query(q, ranked[q["id"]], tuple(a.k)) for q in queries]
        rep = sq.build_report(digest, man.frozen_at, params, per_query, now)
        (out / "search_eval_report.json").write_text(json.dumps(rep, indent=2))
        print(json.dumps(rep["aggregate"], indent=2))
        return 0
    if a.cmd == "sample":
        rows = rel.sample(live, a.seed, a.per_stratum)
        rel.write_labels_csv(out / "relevance_labels.csv", rows)
        (out / "relevance_sample_meta.json").write_text(json.dumps(
            {"corpus_digest": digest, "generated_at": now, "params": params, "count": len(rows)}, indent=2))
        print(f"corpus OK ({digest}); wrote {len(rows)} rows to {out / 'relevance_labels.csv'}")
        return 0
    labels = rel.read_labels_csv(a.labels or out / "relevance_labels.csv", statuses)
    rep = rel.build_report(digest, man.frozen_at, params, labels, now)
    (out / "relevance_report.json").write_text(json.dumps(rep, indent=2))
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
