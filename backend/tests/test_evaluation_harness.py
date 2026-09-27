"""Evaluation harness tests. Pure functions and temp files only; no database access."""
import pytest

from evaluation import relevance as rel
from evaluation.cli import to_row
from evaluation.corpus import CorpusDriftError, Manifest, digest, load_manifest, verify_corpus


def _ids(n=30):
    return [f"00000000-0000-0000-0000-{i:012d}" for i in range(n)]


def _manifest(ids, stored=None):
    st = {i: ("accepted" if k % 2 else "rejected") for k, i in enumerate(ids)}
    return Manifest(stored or digest(ids), "t", st), st


def test_digest_guard_ok():
    ids = _ids()
    m, st = _manifest(ids)
    assert verify_corpus(m, st, expected_digest=digest(ids)) == digest(ids)


def test_guard_rejects_wrong_digest():
    ids = _ids()
    m, st = _manifest(ids, stored="deadbeef")
    with pytest.raises(CorpusDriftError):
        verify_corpus(m, st, expected_digest=digest(ids))


def test_guard_refuses_drift_new_missing_and_changed():
    ids = _ids()
    m, st = _manifest(ids)
    d = digest(ids)
    with pytest.raises(CorpusDriftError):
        verify_corpus(m, {**st, "extra-id": "accepted"}, expected_digest=d)
    with pytest.raises(CorpusDriftError):
        verify_corpus(m, {k: v for k, v in st.items() if k != ids[0]}, expected_digest=d)
    with pytest.raises(CorpusDriftError):
        verify_corpus(m, {**st, ids[0]: "accepted" if st[ids[0]] == "rejected" else "rejected"}, expected_digest=d)


def test_manifest_parsing(tmp_path):
    ids = _ids(4)
    p = tmp_path / "m.txt"
    p.write_text("hdr\nfrozen_at_utc: 2026-09-20T10:00:00Z\ncorpus_digest_md5 (x): abc\n---\n"
                 + "\n".join(f"{i},accepted" for i in ids) + "\n", encoding="utf-8")
    m = load_manifest(p)
    assert m.stored_digest == "abc" and m.frozen_at == "2026-09-20T10:00:00Z" and m.ids == set(ids)


def _rows():
    out = []
    for i, aid in enumerate(_ids(60)):
        out.append({"article_id": aid, "title": f"t{i}", "source": "s", "client": ["JCB", "ISM"][i % 2],
                    "source_class": ["news", "official", "blog"][i % 3],
                    "current_decision": "accept" if i % 4 < 2 else "reject"})
    return out


def test_sampling_deterministic_and_order_independent():
    rows = _rows()
    a = rel.sample(rows, seed=1, per_stratum=3)
    assert a == rel.sample(list(reversed(rows)), seed=1, per_stratum=3)
    assert a != rel.sample(rows, seed=2, per_stratum=3)
    assert {r["current_decision"] for r in a} == {"accept", "reject"}
    assert {r["client"] for r in a} == {"JCB", "ISM"}
    assert all(sum(1 for r in a if (r["client"], r["current_decision"]) == k) <= 3
               for k in {(r["client"], r["current_decision"]) for r in a})


def test_labels_roundtrip_and_validation(tmp_path):
    rows = rel.sample(_rows(), per_stratum=2)
    p = tmp_path / "l.csv"
    rel.write_labels_csv(p, rows)
    got = rel.read_labels_csv(p)
    assert len(got) == len(rows) and all(r["human_label"] == "" for r in got)
    st = {r["article_id"]: "accepted" if r["current_decision"] == "accept" else "rejected" for r in rows}
    rel.read_labels_csv(p, st)
    bad = tmp_path / "b.csv"
    bad.write_text(p.read_text().replace(",accept,,", ",accept,maybe,", 1), encoding="utf-8")
    with pytest.raises(rel.LabelFileError):
        rel.read_labels_csv(bad)
    with pytest.raises(rel.LabelFileError):
        rel.read_labels_csv(p, {**st, rows[0]["article_id"]: "rejected" if st[rows[0]["article_id"]] == "accepted" else "accepted"})
    with pytest.raises(rel.LabelFileError):
        rel.read_labels_csv(p, {})


def _lab(client, dec, human):
    return {"client": client, "current_decision": dec, "human_label": human}


def test_metrics_values_and_not_measured():
    assert rel.compute_metrics([_lab("A", "accept", "")]) == rel.NOT_MEASURED
    rows = [_lab("A", "accept", "accept")] * 3 + [_lab("A", "accept", "reject")] \
        + [_lab("A", "reject", "accept")] + [_lab("A", "reject", "reject")] * 5 + [_lab("A", "accept", "unsure")]
    m = rel.compute_metrics(rows)
    assert (m.tp, m.fp, m.fn, m.tn, m.n) == (3, 1, 1, 5, 10)
    assert m.precision == 0.75 and m.recall == 0.75 and m.f1 == 0.75
    assert m.as_dict()["confusion_matrix"]["reject"]["accept"] == 1


def test_undefined_metric_is_not_measured():
    m = rel.compute_metrics([_lab("A", "reject", "reject")])
    assert m.precision == rel.NOT_MEASURED and m.recall == rel.NOT_MEASURED and m.f1 == rel.NOT_MEASURED


def test_per_client_needs_enough_labels():
    rows = [_lab("A", "accept", "accept")] * 10 + [_lab("B", "accept", "accept")] * 2
    pc = rel.per_client_metrics(rows, min_labels=10)
    assert isinstance(pc["A"], dict) and pc["B"].startswith(rel.NOT_MEASURED)


def test_report_without_labels_is_not_measured():
    rep = rel.build_report("d", "f", {}, rel.sample(_rows(), per_stratum=2), "now")
    assert rep["metrics"] == rel.NOT_MEASURED and rep["labelled_count"] == 0


def test_to_row_preserves_engine_decision():
    r = to_row(("id1", "T", "rejected", '{"entity":"VIKAS","source_type":"news"}', "Src", "rss"))
    assert r["current_decision"] == "reject" and r["client"] == "VIKAS" and r["source_class"] == "news"
    assert to_row(("i", "T", "accepted", "legacy text", None, None))["client"] == "UNKNOWN"
