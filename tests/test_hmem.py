"""H-MEM engine: the spec's exact contracts (mandatory source, scoring formula, threshold, deltas, persistence)."""

import math
import time
from types import SimpleNamespace

import pytest

from causeway.engines.hmem import THRESHOLD, HMem, Memory, embed, tokens

PROV = {"origin": "test", "label": "unit test"}


@pytest.fixture
def hmem(tmp_path):
    return HMem(tmp_path / "hmem")


def add(h, text, **kw):
    defaults = dict(source="critic", confidence=0.9, provenance=PROV, domain="retail_trade", tags=["retail_trade"])
    return h.store.add(text, **{**defaults, **kw})


def test_source_and_provenance_are_mandatory(hmem):
    with pytest.raises(ValueError):
        hmem.store.add("x" * 30, source="", confidence=0.5, provenance=PROV)
    with pytest.raises(ValueError):
        hmem.store.add("x" * 30, source="critic", confidence=0.5, provenance={})


def test_score_matches_the_canonical_formula(hmem):
    m = add(hmem, "Compute gross margin changes from edgar.margin_change so both years share one filing basis.")
    query = "Why did Walmart's gross margin change in fiscal 2022?"
    now = m.lastAccessed
    q_tokens = tokens(query)
    top8 = set(q_tokens[:8])
    m_tokens = set(tokens(m.content))
    expected_raw = (0.35 * max(0.0, sum(a * b for a, b in zip(embed(query), m.embedding)))
                    + 0.20 * len(top8 & m_tokens) / len(top8 | m_tokens)
                    + 0.10 * 1.0                     # tag overlap: retail_trade
                    + 0.10 * 1.0                     # same domain
                    + 0.15 * len({"gross_margin"} & set(m.entities)) / 1
                    + 0.05 * 0.8                     # working layer
                    + 0.05 * math.exp(0))            # just accessed
    score = hmem.retrieval.score(m, query, embed(query), q_tokens, {"retail_trade"}, "retail_trade", {"gross_margin"}, now)
    assert score == pytest.approx(min(1.0, expected_raw) * 0.9)


def test_retrieval_threshold_top_n_and_superseded_penalty(hmem):
    lesson = add(hmem, "Never exclude a peer from the median because its gross margin moved differently; that is cherry-picking.")
    add(hmem, "Kroger cost of sales excludes depreciation so its gross margin is not comparable to peers.")
    add(hmem, "Unrelated memory about weather in Lisbon and holiday plans for the summer.", domain="travel", tags=["travel"])
    out = hmem.retrieval.retrieve("Which peers belong in the gross margin peer median for Target?", tags=["retail_trade"],
                                  domain="retail_trade")
    ids = [m["id"] for m in out["memories"]]
    assert lesson.id in ids
    assert all(m["score"] >= THRESHOLD for m in out["memories"]) and len(ids) <= 5
    assert "## retail_trade" in out["context"] and out["trace"]["retrievalReason"]

    lesson.supersededBy = "mem_newer"
    penalised = hmem.retrieval.retrieve("Which peers belong in the gross margin peer median for Target?",
                                        tags=["retail_trade"], domain="retail_trade", threshold=0.0)
    assert penalised["trace"]["scores"][lesson.id] < out["trace"]["scores"][lesson.id]


def test_feedback_deltas_are_exact(hmem):
    m = add(hmem, "Use quarterly 10-Q inventory facts, not year-end balances, to test inventory build-up.", confidence=0.5)
    hmem.store.feedback(m.id, "helpful")
    assert m.confidence == pytest.approx(0.55) and m.accessCount == 1
    hmem.store.feedback(m.id, "not_helpful")
    assert m.confidence == pytest.approx(0.52)
    hmem.store.feedback(m.id, "wrong")
    assert m.confidence == pytest.approx(0.32) and "review" in m.tags


def test_persistence_and_ledger(tmp_path):
    first = HMem(tmp_path / "h")
    m = add(first, "Retail-margin PPIs are index points, not percentage points of gross margin.")
    first.store.promote(m.id)
    reopened = HMem(tmp_path / "h")
    assert reopened.store.memories[m.id].layer == "long_term"
    actions = [line for line in (tmp_path / "h" / "ledger.jsonl").read_text().splitlines()]
    assert any('"create"' in a for a in actions) and any('"promote"' in a for a in actions)


def test_short_term_is_not_persisted(tmp_path):
    h = HMem(tmp_path / "h")
    add(h, "A direct capture kept only for this session, nothing more.", layer="short_term")
    assert not HMem(tmp_path / "h").store.memories


def test_mining_uses_generator_and_falls_back(tmp_path):
    reply = SimpleNamespace(text='```json\n["Compute changes inside one 10-K so restatements cannot fake a jump.", "x"]\n```')
    h = HMem(tmp_path / "h", generator=lambda *a, **k: reply)
    mined = h.mining.mine("user_correction", "rejection reasons ...", domain="retail_trade", tags=["retail_trade"],
                          mission_id="run1", source="critic", confidence=0.9)
    assert [m.content for m in mined] == ["Compute changes inside one 10-K so restatements cannot fake a jump."]
    assert mined[0].type == "preference" and mined[0].provenance["origin"] == "model_extraction"

    offline = HMem(tmp_path / "h2")  # no generator: direct capture into short_term
    kept = offline.mining.mine("plan_completion", "Target's gross margin fell 4.64 pts vs a peer median of -0.85.",
                               domain="retail_trade", tags=[], mission_id="run2", source="investigation", confidence=0.7)
    assert kept[0].layer == "short_term" and kept[0].provenance["origin"] == "direct_capture"


def test_recency_decays_with_a_14_day_constant(hmem):
    m = add(hmem, "Compare fiscal years ending within seven months of each other across retailers.")
    q = "fiscal years retailers"
    args = (q, embed(q), tokens(q), set(), "retail_trade", set())
    fresh = hmem.retrieval.score(m, *args, m.lastAccessed)
    stale = hmem.retrieval.score(m, *args, m.lastAccessed + 14 * 86400)
    assert fresh - stale == pytest.approx(0.05 * (1 - math.exp(-1)) * 0.9, abs=1e-6)
