"""Search-quality evaluation for GET /search/articles (via its service). Read-only.

Ground truth is ONLY `expected_article_ids` written by a human in the query-set file with
`labelled: true`. The engine's own ranking is never used as a label. Unlabelled queries are
reported as "not measured".
"""
from __future__ import annotations

import json

NOT_MEASURED = "not measured"
DEFAULT_KS = (5, 10)


class QuerySetError(ValueError):
    pass


def load_query_set(path, corpus_ids: set | None = None) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    queries = data["queries"] if isinstance(data, dict) else data
    seen: set = set()
    for i, q in enumerate(queries):
        missing = {"id", "query", "labelled", "expected_article_ids"} - set(q)
        if missing:
            raise QuerySetError(f"query #{i}: missing {sorted(missing)}")
        if q["id"] in seen or not str(q["query"]).strip():
            raise QuerySetError(f"query #{i}: duplicate id or blank query")
        seen.add(q["id"])
        if not isinstance(q["labelled"], bool) or not isinstance(q["expected_article_ids"], list):
            raise QuerySetError(f"{q['id']}: 'labelled' must be bool and 'expected_article_ids' a list")
        if q["expected_article_ids"] and not q["labelled"]:
            raise QuerySetError(f"{q['id']}: expected ids present but labelled=false")
        if len(set(q["expected_article_ids"])) != len(q["expected_article_ids"]):
            raise QuerySetError(f"{q['id']}: duplicate expected ids")
        if corpus_ids is not None:
            unknown = set(q["expected_article_ids"]) - corpus_ids
            if unknown:
                raise QuerySetError(f"{q['id']}: {len(unknown)} expected ids not in frozen corpus")
    return queries


def precision_at_k(ranked: list[str], expected: set, k: int) -> float:
    return sum(a in expected for a in ranked[:k]) / k


def recall_at_k(ranked: list[str], expected: set, k: int) -> float:
    return sum(a in expected for a in ranked[:k]) / len(expected)


def ndcg_at_k(ranked: list[str], expected: set, k: int) -> float:
    """Binary-relevance nDCG@K against the human-labelled set."""
    import math
    dcg = sum(1 / math.log2(i + 2) for i, a in enumerate(ranked[:k]) if a in expected)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(expected))))
    return dcg / ideal if ideal else 0.0


def reciprocal_rank(ranked: list[str], expected: set) -> float:
    for pos, a in enumerate(ranked, start=1):
        if a in expected:
            return 1.0 / pos
    return 0.0


def evaluate_query(q: dict, ranked: list[str], ks=DEFAULT_KS) -> dict:
    base = {"id": q["id"], "query": q["query"], "category": q.get("category"), "returned": len(ranked)}
    if not q["labelled"]:
        return {**base, "status": NOT_MEASURED}
    exp = set(q["expected_article_ids"])
    if not exp:  # human-labelled "should match nothing": a check, not a ranking metric
        return {**base, "status": "no-match expected", "unexpected_results": len(ranked)}
    return {**base, "status": "measured", "expected": len(exp),
            **{f"precision@{k}": round(precision_at_k(ranked, exp, k), 4) for k in ks},
            **{f"recall@{k}": round(recall_at_k(ranked, exp, k), 4) for k in ks},
            **{f"ndcg@{k}": round(ndcg_at_k(ranked, exp, k), 4) for k in ks},
            "mrr": round(reciprocal_rank(ranked, exp), 4)}


def aggregate(per_query: list[dict], ks=DEFAULT_KS) -> dict:
    measured = [r for r in per_query if r["status"] == "measured"]
    nomatch = [r for r in per_query if r["status"] == "no-match expected"]
    out: dict = {"queries_total": len(per_query), "queries_measured": len(measured),
                 "queries_unlabelled": sum(r["status"] == NOT_MEASURED for r in per_query),
                 "no_match_checks": len(nomatch),
                 "no_match_violations": sum(r["unexpected_results"] > 0 for r in nomatch)}
    keys = [f"precision@{k}" for k in ks] + [f"recall@{k}" for k in ks] + [f"ndcg@{k}" for k in ks] + ["mrr"]
    for key in keys:
        out[key] = round(sum(r[key] for r in measured) / len(measured), 4) if measured else NOT_MEASURED
    return out


def build_report(digest: str, frozen_at: str, params: dict, per_query: list[dict], generated_at: str) -> dict:
    return {
        "system_under_test": "GET /search/articles (services.article_search.search_articles)",
        "corpus_digest": digest, "corpus_frozen_at": frozen_at, "generated_at": generated_at,
        "params": params, "per_query": per_query, "aggregate": aggregate(per_query, tuple(params["ks"])),
        "caveat": "Metrics exist only for queries with human labels; the engine's ranking is never ground truth. "
                  "Precision@K divides by K even when fewer than K results are returned.",
    }


# ---- CSV labelling round-trip -------------------------------------------------------------------
import csv  # noqa: E402

LABEL_COLUMNS = ["query_id", "query", "article_id", "client", "title", "relevant"]
NONE_ROW_TITLE = "[NO RELEVANT ARTICLE EXISTS FOR THIS QUERY - put y here if true]"


def write_labelling_csv(path, queries: list[dict], candidates: dict) -> None:
    """One row per (query, engine candidate) plus one 'none exists' row per query. `relevant` starts blank.

    candidates: query_id -> list of dicts (article_id, client, title). Candidates are only a
    reading aid; you may append rows with other article ids the engine missed.
    """
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(LABEL_COLUMNS)
        for q in queries:
            for c in candidates.get(q["id"], []):
                w.writerow([q["id"], q["query"], c["article_id"], c.get("client") or "", c.get("title") or "", ""])
            w.writerow([q["id"], q["query"], "", "", NONE_ROW_TITLE, ""])


def apply_labels_csv(path, queries: list[dict], corpus_ids: set | None = None) -> list[dict]:
    """Return a copy of `queries` with human labels from the CSV. Values in `relevant`: y / n / blank.

    A query becomes labelled only if it has at least one 'y' article row, or 'y' on its 'none exists'
    row (meaning: should return nothing). Anything else stays unlabelled ("not measured").
    """
    by_id = {q["id"]: q for q in queries}
    yes: dict = {qid: [] for qid in by_id}
    none_exists: set = set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        if rd.fieldnames != LABEL_COLUMNS:
            raise QuerySetError(f"labelling CSV header must be {LABEL_COLUMNS}")
        for n, r in enumerate(rd, start=2):
            qid, aid, val = r["query_id"], r["article_id"].strip(), r["relevant"].strip().lower()
            if qid not in by_id:
                raise QuerySetError(f"line {n}: unknown query_id {qid!r}")
            if val not in ("", "y", "n"):
                raise QuerySetError(f"line {n}: 'relevant' must be y, n or blank, got {val!r}")
            if val != "y":
                continue
            if not aid:
                none_exists.add(qid)
            else:
                if corpus_ids is not None and aid not in corpus_ids:
                    raise QuerySetError(f"line {n}: article {aid} not in frozen corpus")
                if aid not in yes[qid]:
                    yes[qid].append(aid)
    out = []
    for q in queries:
        qid = q["id"]
        if yes[qid] and qid in none_exists:
            raise QuerySetError(f"{qid}: marked both 'relevant articles' and 'none exist'")
        labelled = bool(yes[qid]) or qid in none_exists
        out.append({**q, "labelled": labelled, "expected_article_ids": sorted(yes[qid]) if labelled else []})
    return out
