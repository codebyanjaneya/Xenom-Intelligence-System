"""Event-extraction evaluation (read-only). Same principle as the relevance and search harnesses: ground truth is ONLY
what a human writes in the labelling sheet; unlabelled rows are "not measured"; nothing is inferred from the extractor.

Sheet columns (human fills the last five):
  event_id, client, article_url, event_type, event_time, source_count, sentence,
  valid_event (y/n) | correct_type (y/n or the right type) | correct_date (y/n/na) | duplicate_of (event_id) | notes
"""
from __future__ import annotations

import csv

NOT_MEASURED = "not measured"
EVENT_TYPES = {"acquisition", "funding", "investment", "partnership", "agreement", "contract", "appointment", "launch",
               "expansion", "announcement"}
COLUMNS = ["event_id", "client", "article_url", "event_type", "event_time", "source_count", "sentence",
           "valid_event", "correct_type", "correct_date", "duplicate_of", "notes"]


class EventLabelError(ValueError):
    pass


def write_event_sheet(path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([r["event_id"], r["client"], r["article_url"], r["event_type"], r.get("event_time") or "",
                        r.get("source_count", 1), r["sentence"], "", "", "", "", ""])


def read_event_labels(path, known_ids: set | None = None) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        if rd.fieldnames != COLUMNS:
            raise EventLabelError(f"header must be {COLUMNS}")
        rows, seen = [], set()
        for n, r in enumerate(rd, start=2):
            eid = r["event_id"].strip()
            if not eid or eid in seen:
                raise EventLabelError(f"line {n}: missing or duplicate event_id {eid!r}")
            seen.add(eid)
            if known_ids is not None and eid not in known_ids:
                raise EventLabelError(f"line {n}: unknown event_id {eid}")
            for col, allowed in (("valid_event", {"", "y", "n"}), ("correct_date", {"", "y", "n", "na"})):
                r[col] = r[col].strip().lower()
                if r[col] not in allowed:
                    raise EventLabelError(f"line {n}: {col} must be one of {sorted(allowed)}, got {r[col]!r}")
            r["correct_type"] = r["correct_type"].strip().lower()
            if r["correct_type"] not in {"", "y", "n"} | EVENT_TYPES:
                raise EventLabelError(f"line {n}: correct_type must be y, n or one of {sorted(EVENT_TYPES)}")
            r["duplicate_of"] = r["duplicate_of"].strip()
            if r["duplicate_of"] and r["duplicate_of"] == eid:
                raise EventLabelError(f"line {n}: an event cannot duplicate itself")
            if r["valid_event"] == "n" and (r["correct_type"] or r["correct_date"] not in ("", "na")):
                raise EventLabelError(f"line {n}: type/date labels only apply to valid events")
            rows.append(r)
    ids = {r["event_id"] for r in rows}
    for r in rows:
        if r["duplicate_of"] and r["duplicate_of"] not in ids:
            raise EventLabelError(f"{r['event_id']}: duplicate_of {r['duplicate_of']} is not a listed event")
    return rows


def _ratio(a: int, b: int):
    return round(a / b, 4) if b else NOT_MEASURED


def compute_event_metrics(rows: list[dict]) -> dict:
    """precision = valid / labelled; type accuracy over labelled valid events; date accuracy over events that state a date;
    duplicate rate = labelled-duplicate / labelled valid. Each is 'not measured' without the labels it needs."""
    labelled = [r for r in rows if r["valid_event"] in ("y", "n")]
    valid = [r for r in labelled if r["valid_event"] == "y"]
    typed = [r for r in valid if r["correct_type"]]
    dated = [r for r in valid if r["event_time"] and r["correct_date"] in ("y", "n")]
    return {
        "events_total": len(rows), "events_labelled": len(labelled),
        "precision": _ratio(len(valid), len(labelled)),
        "type_accuracy": _ratio(sum(1 for r in typed if r["correct_type"] == "y" or r["correct_type"] == r["event_type"]), len(typed)),
        "date_accuracy": _ratio(sum(1 for r in dated if r["correct_date"] == "y"), len(dated)),
        "duplicate_rate": _ratio(sum(1 for r in valid if r["duplicate_of"]), len(valid)),
        "note": "Human labels only; the extractor's output is never used as ground truth.",
    }
