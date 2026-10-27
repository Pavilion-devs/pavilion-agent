"""World Model engine: the spec's invariants (stable IDs, idempotent ingest, provenance, rebuildable views)."""

import pytest

from causeway.engines.world_model import IngestPipeline, ValidationError, WorldModel, stable_id

COLUMNS = ["ticker", "fiscal_year_end", "gross_margin_change", "revenue", "basis_filing"]
ROWS = [
    ("TGT", "2023-01-28", -0.0464, 109120000000.0, "0000027419-23-000015"),
    ("WMT", "2023-01-31", -0.0096, 611289000000.0, "0000104169-23-000020"),
]


@pytest.fixture
def world(tmp_path):
    return WorldModel(tmp_path / "world")


def test_stable_ids_are_content_derived():
    assert stable_id("company", "TGT") == stable_id("company", "  tgt ")
    assert stable_id("company", "TGT") != stable_id("company", "WMT")


def test_ingest_creates_facts_with_provenance(world):
    stats = world.ingest.ingest_query_result("q_1", "SELECT 1", COLUMNS, ROWS, agent="investigator")
    assert stats == {"facts": 4, "already_known": 0, "rejected": 0}  # 2 rows x 2 numeric measures
    facts = world.read.facts_for_query("q_1")
    assert {(f.subject, f.measure) for f in facts} == {
        ("TGT", "gross_margin_change"), ("TGT", "revenue"), ("WMT", "gross_margin_change"), ("WMT", "revenue")}
    assert all(f.query_id == "q_1" and f.sql_hash for f in facts)
    assert "basis_filing" not in {f.measure for f in facts}  # identifiers are not measures


def test_ingest_is_idempotent(world):
    world.ingest.ingest_query_result("q_1", "SELECT 1", COLUMNS, ROWS)
    lines_before = world.store.log_path.read_text().count("\n")
    again = world.ingest.ingest_query_result("q_1", "SELECT 1", COLUMNS, ROWS)
    assert again["facts"] == 0 and again["already_known"] == 4
    assert world.store.log_path.read_text().count("\n") == lines_before


def test_views_rebuild_from_the_log(tmp_path):
    first = WorldModel(tmp_path / "w")
    first.ingest.ingest_query_result("q_1", "SELECT 1", COLUMNS, ROWS)
    reopened = WorldModel(tmp_path / "w")  # a new process: everything comes back from events.jsonl
    assert len(reopened.store.view.facts) == len(first.store.view.facts) == 4
    assert reopened.ingest.ingest_query_result("q_1", "SELECT 1", COLUMNS, ROWS)["facts"] == 0


def test_validation_rejects_missing_provenance():
    with pytest.raises(ValidationError):
        IngestPipeline._validate({"type": "fact", "provenance": {}})
    with pytest.raises(ValidationError):
        IngestPipeline._validate({"type": "fact", "provenance": {"origin": "x"}})  # no source query


def test_conflicts_surface_disagreeing_queries(world):
    world.ingest.ingest_query_result("q_a", "A", ["ticker", "fiscal_year_end", "gross_margin"], [("TGT", "2023-01-28", 0.2457)])
    world.ingest.ingest_query_result("q_b", "B", ["ticker", "fiscal_year_end", "gross_margin"], [("TGT", "2023-01-28", 0.2464)])
    world.ingest.ingest_query_result("q_c", "C", ["ticker", "fiscal_year_end", "gross_margin"], [("WMT", "2023-01-31", 0.2414)])
    conflicts = world.read.conflicts(tolerance=0.001)
    assert [(c["subject"], c["measure"]) for c in conflicts] == [("TGT", "gross_margin")]
    assert {v["query_id"] for v in conflicts[0]["values"]} == {"q_a", "q_b"}


def test_context_builder_is_structured_and_budgeted(world):
    world.ingest.ingest_query_result("q_1", "SELECT 1", COLUMNS, ROWS)
    ctx = world.read.build_context(["tgt"], max_facts=1)
    assert len(ctx["facts"]) == 1 and ctx["truncated"] == 1
    assert ctx["facts"][0]["subject"] == "TGT" and ctx["facts"][0]["provenance"] == "q_1"


def test_findings_record_status_and_citations(world):
    finding = {"statement": "Target fell 4.64 pts", "evidence": ["q_1"], "scope": "company-specific"}
    world.ingest.record_finding(finding, "rejected", ["rounded"], 1, {"origin": "critic"})
    world.ingest.record_finding(finding, "approved", [], 2, {"origin": "critic"})
    (status,) = world.store.view.findings.values()
    assert status["verdict"] == "approved" and status["round"] == 2
    assert any(r["rel"] == "cites" and r["dst"] == "query:q_1" for r in world.store.view.relationships.values())


def test_subject_comes_from_single_ticker_filter(tmp_path):
    """Rows without a ticker column still become facts when the SQL filters on exactly one ticker."""
    wm = WorldModel(tmp_path)
    sql = "SELECT fiscal_year_end, revenue_change_pct FROM t WHERE ticker = 'bby'"
    stats = wm.ingest.ingest_query_result("q_b", sql, ["fiscal_year_end", "revenue_change_pct"],
                                          [("2022-01-29", None), ("2023-01-28", -10.55)])
    assert stats["facts"] == 1 and [f.value for f in wm.read.facts_for_query("q_b")] == [-10.55]
    many = "SELECT revenue FROM t WHERE ticker = 'BBY' OR ticker = 'TGT'"
    assert wm.ingest.ingest_query_result("q_m", many, ["revenue"], [(1.0,)])["facts"] == 0
