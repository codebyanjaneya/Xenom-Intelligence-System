"""Search-quality harness tests: pure functions and temp files; no database access."""
import json

import pytest

from evaluation import search_quality as sq
from evaluation.corpus import CorpusDriftError, Manifest, digest, verify_corpus


def _q(id_, labelled=True, expected=(), query="q"):
    return {"id": id_, "query": query, "labelled": labelled, "expected_article_ids": list(expected)}


def test_metric_values():
    ranked = ["a", "x", "b", "y", "z", "c"]
    exp = {"a", "b", "c", "d"}
    assert sq.precision_at_k(ranked, exp, 5) == 0.4
    assert sq.recall_at_k(ranked, exp, 5) == 0.5
    assert sq.reciprocal_rank(ranked, {"b"}) == pytest.approx(1 / 3)
    assert sq.reciprocal_rank(ranked, {"nope"}) == 0.0
    assert sq.precision_at_k(["a"], {"a"}, 5) == 0.2  # divides by K, documented


def test_unlabelled_is_not_measured_and_aggregate_too():
    per = [sq.evaluate_query(_q("u1", labelled=False), ["a", "b"])]
    assert per[0]["status"] == sq.NOT_MEASURED and "mrr" not in per[0]
    agg = sq.aggregate(per)
    assert agg["mrr"] == sq.NOT_MEASURED and agg["precision@5"] == sq.NOT_MEASURED
    assert agg["queries_measured"] == 0 and agg["queries_unlabelled"] == 1


def test_aggregate_means_only_over_measured_queries():
    per = [sq.evaluate_query(_q("a", expected=["a1"]), ["a1"]),          # P@5 .2, R 1, MRR 1
           sq.evaluate_query(_q("b", expected=["b1"]), ["x", "b1"]),     # P@5 .2, R 1, MRR .5
           sq.evaluate_query(_q("c", labelled=False), ["z"]),
           sq.evaluate_query(_q("n", expected=[]), ["z"]),                # labelled no-match, violated
           sq.evaluate_query(_q("n2", expected=[]), [])]                  # labelled no-match, ok
    agg = sq.aggregate(per)
    assert agg["queries_measured"] == 2 and agg["mrr"] == 0.75 and agg["recall@5"] == 1.0
    assert agg["no_match_checks"] == 2 and agg["no_match_violations"] == 1


def test_engine_output_is_never_a_label():
    # same ranking, different human labels -> different metrics; nothing derives labels from ranking
    ranked = ["a", "b"]
    assert sq.evaluate_query(_q("x", expected=["a"]), ranked)["mrr"] == 1.0
    assert sq.evaluate_query(_q("x", expected=["b"]), ranked)["mrr"] == 0.5
    assert sq.evaluate_query(_q("x", labelled=False), ranked)["status"] == sq.NOT_MEASURED


def test_query_set_validation(tmp_path):
    def write(qs):
        p = tmp_path / "q.json"
        p.write_text(json.dumps({"queries": qs}), encoding="utf-8")
        return p
    assert len(sq.load_query_set(write([_q("a"), _q("b", labelled=False)]))) == 2
    for bad in ([_q("a"), _q("a")],                                   # duplicate id
                [_q("a", labelled=False, expected=["x"])],             # ids without labelled
                [{"id": "a", "query": "q"}],                           # missing fields
                [_q("a", query="  ")]):
        with pytest.raises(sq.QuerySetError):
            sq.load_query_set(write(bad))
    with pytest.raises(sq.QuerySetError):                              # expected id outside frozen corpus
        sq.load_query_set(write([_q("a", expected=["zzz"])]), corpus_ids={"other"})


def test_shipped_query_set_is_valid_deterministic_and_unlabelled():
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "evaluation" / "data" / "search_queries.json"
    a, b = sq.load_query_set(p), sq.load_query_set(p)
    assert a == b and 18 <= len(a) <= 25
    assert all(not q["labelled"] and q["expected_article_ids"] == [] for q in a)  # no auto-labels
    cats = {q["category"] for q in a}
    assert {"ambiguous", "no-match", "multi-word", "topic", "company-name"} <= cats
    assert len([c for c in cats if c.startswith("client:")]) == 6


def test_report_is_deterministic_apart_from_timestamp():
    per = [sq.evaluate_query(_q("a", expected=["a1"]), ["a1", "b"])]
    p = {"ks": [5, 10]}
    r1 = sq.build_report("d", "f", p, per, "t")
    assert r1 == sq.build_report("d", "f", p, per, "t")
    assert r1["corpus_digest"] == "d"


def test_corpus_drift_blocks_evaluation():
    ids = [f"id{i}" for i in range(5)]
    m = Manifest(digest(ids), "t", {i: "accepted" for i in ids})
    verify_corpus(m, {i: "accepted" for i in ids}, expected_digest=digest(ids))
    with pytest.raises(CorpusDriftError):
        verify_corpus(m, {**{i: "accepted" for i in ids}, "new": "accepted"}, expected_digest=digest(ids))


# ---- CSV labelling round-trip -----------------------------------------------------------------

def _csv(tmp_path, rows):
    import csv as _c
    p = tmp_path / "l.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = _c.writer(f)
        w.writerow(sq.LABEL_COLUMNS)
        w.writerows(rows)
    return p


def test_labelling_sheet_roundtrip_and_never_prelabelled(tmp_path):
    import csv as _c
    qs = [_q("a", labelled=False, query="alpha"), _q("b", labelled=False, query="beta")]
    p = tmp_path / "s.csv"
    sq.write_labelling_csv(p, qs, {"a": [{"article_id": "1", "client": "JCB", "title": "T1"},
                                         {"article_id": "2", "client": None, "title": "T2"}]})
    rows = list(_c.DictReader(open(p, encoding="utf-8-sig", newline="")))
    assert [r["query_id"] for r in rows] == ["a", "a", "a", "b"]  # candidates + one none-row per query
    assert all(r["relevant"] == "" for r in rows)
    out = sq.apply_labels_csv(p, qs)  # an untouched sheet labels nothing
    assert all(not q["labelled"] and q["expected_article_ids"] == [] for q in out)


def test_apply_labels_yes_none_and_unlabelled(tmp_path):
    qs = [_q("a", labelled=False), _q("b", labelled=False), _q("c", labelled=False), _q("d", labelled=False)]
    p = _csv(tmp_path, [["a", "q", "1", "", "T", "y"], ["a", "q", "2", "", "T", "n"], ["a", "q", "9", "", "T", "Y"],
                        ["b", "q", "", "", sq.NONE_ROW_TITLE, "y"],
                        ["c", "q", "5", "", "T", "n"]])  # only 'n': not enough to label
    got = {q["id"]: q for q in sq.apply_labels_csv(p, qs)}
    assert got["a"]["labelled"] and got["a"]["expected_article_ids"] == ["1", "9"]
    assert got["b"]["labelled"] and got["b"]["expected_article_ids"] == []  # human said: nothing relevant
    assert not got["c"]["labelled"] and not got["d"]["labelled"]  # -> "not measured"
    assert sq.aggregate([sq.evaluate_query(q, ["1"]) for q in got.values()])["queries_measured"] == 1


def test_apply_labels_validation(tmp_path):
    qs = [_q("a", labelled=False)]
    for row, msg in ((["zz", "q", "1", "", "T", "y"], "unknown query_id"),
                     (["a", "q", "1", "", "T", "maybe"], "relevant"),
                     (["a", "q", "1", "", "T", "y"], "frozen corpus")):
        with pytest.raises(sq.QuerySetError, match=msg):
            sq.apply_labels_csv(_csv(tmp_path, [row]), qs, corpus_ids={"other"} if msg == "frozen corpus" else None)
    with pytest.raises(sq.QuerySetError, match="both"):
        sq.apply_labels_csv(_csv(tmp_path, [["a", "q", "1", "", "T", "y"], ["a", "q", "", "", "x", "y"]]), qs)
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(sq.QuerySetError, match="header"):
        sq.apply_labels_csv(bad, qs)
