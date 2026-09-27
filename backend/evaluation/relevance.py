"""Relevance evaluation scaffolding: deterministic stratified sampling, label file I/O, metrics.

The rule-engine decision (`current_decision`) is stored separately from `human_label`.
Positive class = "accept". Nothing here calls an LLM or writes to the database.
"""
from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from dataclasses import dataclass

FIELDS = ["article_id", "title", "source", "client", "source_class", "current_decision", "human_label", "notes"]
LABELS = {"accept", "reject", "unsure"}
DECISIONS = {"accept", "reject"}
NOT_MEASURED = "not measured"
DEFAULT_SEED = 20260920
DEFAULT_PER_STRATUM = 12
DEFAULT_MIN_LABELS_PER_CLIENT = 10


class LabelFileError(ValueError):
    pass


def _rank(seed: int, article_id: str) -> str:
    return hashlib.sha256(f"{seed}:{article_id}".encode()).hexdigest()


def sample(rows: list[dict], seed: int = DEFAULT_SEED, per_stratum: int = DEFAULT_PER_STRATUM) -> list[dict]:
    """Stratify by (client, decision); inside a stratum round-robin across source classes.

    Input row order does not matter: ranking is a seeded hash of the article id.
    """
    strata: dict = defaultdict(list)
    for r in rows:
        strata[(r["client"], r["current_decision"])].append(r)
    picked: list[dict] = []
    for key in sorted(strata):
        by_class: dict = defaultdict(list)
        for r in sorted(strata[key], key=lambda r: _rank(seed, r["article_id"])):
            by_class[r["source_class"]].append(r)
        queues = [by_class[c] for c in sorted(by_class)]
        chosen: list[dict] = []
        while len(chosen) < per_stratum and any(queues):
            for q in queues:
                if q and len(chosen) < per_stratum:
                    chosen.append(q.pop(0))
        picked.extend(chosen)
    return sorted(picked, key=lambda r: (r["client"], r["current_decision"], r["article_id"]))


def write_labels_csv(path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS} | {"human_label": "", "notes": ""})


def read_labels_csv(path, corpus_statuses: dict | None = None) -> list[dict]:
    """Parse and validate a label file. `corpus_statuses` (id -> 'accepted'/'rejected') enables cross-checks."""
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        if rd.fieldnames != FIELDS:
            raise LabelFileError(f"header must be exactly {FIELDS}, got {rd.fieldnames}")
        rows, seen = [], set()
        for n, r in enumerate(rd, start=2):
            aid = r["article_id"].strip()
            if not aid or aid in seen:
                raise LabelFileError(f"line {n}: missing or duplicate article_id {aid!r}")
            seen.add(aid)
            r["human_label"] = r["human_label"].strip().lower()
            if r["human_label"] and r["human_label"] not in LABELS:
                raise LabelFileError(f"line {n}: human_label {r['human_label']!r} not in {sorted(LABELS)} or empty")
            if r["current_decision"] not in DECISIONS:
                raise LabelFileError(f"line {n}: current_decision {r['current_decision']!r} invalid")
            if corpus_statuses is not None:
                if aid not in corpus_statuses:
                    raise LabelFileError(f"line {n}: article {aid} not in frozen corpus")
                want = "accept" if corpus_statuses[aid] == "accepted" else "reject"
                if r["current_decision"] != want:
                    raise LabelFileError(f"line {n}: current_decision {r['current_decision']} != frozen {want}")
            rows.append(r)
    return rows


@dataclass
class Metrics:
    n: int
    tp: int
    fp: int
    fn: int
    tn: int
    precision: object
    recall: object
    f1: object

    def as_dict(self) -> dict:
        return {**self.__dict__, "confusion_matrix": {"rows": "human", "cols": "engine",
                "accept": {"accept": self.tp, "reject": self.fn}, "reject": {"accept": self.fp, "reject": self.tn}}}


def _ratio(a: int, b: int):
    return round(a / b, 4) if b else NOT_MEASURED


def compute_metrics(rows: list[dict]):
    """Rule engine vs human label; positive = accept. 'unsure'/unlabelled rows are excluded."""
    lab = [r for r in rows if r.get("human_label") in DECISIONS]
    if not lab:
        return NOT_MEASURED
    tp = sum(r["current_decision"] == "accept" and r["human_label"] == "accept" for r in lab)
    fp = sum(r["current_decision"] == "accept" and r["human_label"] == "reject" for r in lab)
    fn = sum(r["current_decision"] == "reject" and r["human_label"] == "accept" for r in lab)
    tn = sum(r["current_decision"] == "reject" and r["human_label"] == "reject" for r in lab)
    p, rc = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    ok = isinstance(p, float) and isinstance(rc, float) and (p + rc) > 0
    return Metrics(len(lab), tp, fp, fn, tn, p, rc, round(2 * p * rc / (p + rc), 4) if ok else NOT_MEASURED)


def per_client_metrics(rows: list[dict], min_labels: int = DEFAULT_MIN_LABELS_PER_CLIENT) -> dict:
    out = {}
    for c in sorted({r["client"] for r in rows}):
        sub = [r for r in rows if r["client"] == c]
        n = sum(r.get("human_label") in DECISIONS for r in sub)
        m = compute_metrics(sub) if n >= min_labels else NOT_MEASURED
        out[c] = m.as_dict() if isinstance(m, Metrics) else f"{NOT_MEASURED} ({n}/{min_labels} labels)"
    return out


def build_report(corpus_digest: str, frozen_at: str, params: dict, rows: list[dict], generated_at: str) -> dict:
    m = compute_metrics(rows)
    by: dict = defaultdict(int)
    for r in rows:
        by[(r["client"], r["current_decision"])] += 1
    return {
        "corpus_digest": corpus_digest,
        "corpus_frozen_at": frozen_at,
        "generated_at": generated_at,
        "sampling": params,
        "sample_count": len(rows),
        "sample_by_client_decision": {f"{c}/{d}": n for (c, d), n in sorted(by.items())},
        "labelled_count": sum(r.get("human_label") in DECISIONS for r in rows),
        "metrics": m.as_dict() if isinstance(m, Metrics) else NOT_MEASURED,
        "per_client": per_client_metrics(rows, params.get("min_labels_per_client", DEFAULT_MIN_LABELS_PER_CLIENT)),
        "caveat": "Sample is stratified and includes rejected rows on purpose; precision/recall describe the "
                  "sample, not the population. Not an accuracy claim.",
    }
