"""Critic deterministic pass (recompute + number grounding) and generator helpers, without live services."""

from causeway.critic import _grounded, deterministic_check
from causeway.engines.world_model import WorldModel
from causeway.generator import normalise_base_url, strip_reasoning
from causeway.tools import SqlTool
from causeway.zetaris_client import QueryResult

COLUMNS = ["ticker", "fiscal_year_end", "gross_margin_change"]
ROWS = [("TGT", "2023-01-28", -0.0464), ("WMT", "2023-01-31", -0.0096), ("COST", "2022-08-28", -0.0074),
        ("DG", "2023-02-03", -0.0037), ("BBY", "2023-01-28", -0.0108)]


class FakeClient:
    """Stands in for Zetaris: returns fixed rows, or different rows to simulate a non-reproducible query."""

    def __init__(self, rows):
        self.rows = rows

    def run_sql(self, sql, max_rows=200):
        return QueryResult("q_rerun", sql, COLUMNS, self.rows, 5)


class NullEvidence:
    run_id = "test"

    def log(self, *a, **k):
        return {}


def setup(tmp_path, rerun_rows=ROWS):
    world = WorldModel(tmp_path / "w")
    sql = SqlTool(FakeClient(rerun_rows), NullEvidence())
    sql.results["q_1"] = QueryResult("q_1", "SELECT ...", COLUMNS, ROWS, 10)
    world.ingest.ingest_query_result("q_1", "SELECT ...", COLUMNS, ROWS)
    return world, sql


def test_grounding_direct_percentage_difference_and_median():
    values = [-0.0464, -0.0096, -0.0074, -0.0037, -0.0108]
    medians = [-0.0085]
    assert _grounded(-4.64, values, medians) == "direct"       # ratio quoted in percentage points
    assert _grounded(-0.85, values, medians) == "median"       # peer median, in points
    assert _grounded(3.68, values, medians) == "difference"    # -4.64 - (-0.96)
    assert _grounded(-4.0, values, medians) is None            # coarse rounding of 4.64 is not grounded


def test_differences_only_count_within_one_measure():
    revenue_pct, inventory_pct, levels = [-10.55], [-13.83], [5.14, 5.965, 46.298, 51.761]
    groups = [revenue_pct, inventory_pct, levels]
    assert _grounded(-14, revenue_pct + inventory_pct + levels, [], groups) is None   # rounding, not a difference
    assert _grounded(0.825, revenue_pct + inventory_pct + levels, [], groups) == "difference"  # 5.965 - 5.14


def test_finding_with_grounded_numbers_passes(tmp_path):
    world, sql = setup(tmp_path)
    finding = {"id": "F1", "evidence": ["q_1"], "numbers": {"target_change_pts": -4.64, "wmt_change_pts": -0.96}}
    assert deterministic_check(finding, sql, world, {}) == []


def test_ungrounded_number_and_missing_evidence_are_rejected(tmp_path):
    world, sql = setup(tmp_path)
    rounded = {"id": "F1", "evidence": ["q_1"], "numbers": {"target_change_pts": -4}}
    assert any("not in the cited query results" in p for p in deterministic_check(rounded, sql, world, {}))
    assert deterministic_check({"id": "F2", "evidence": ["q_missing"]}, sql, world, {}) == ["cites no query that actually ran"]


def test_non_reproducible_query_is_rejected(tmp_path):
    world, sql = setup(tmp_path, rerun_rows=ROWS[:-1])
    finding = {"id": "F1", "evidence": ["q_1"], "numbers": {"target_change_pts": -4.64}}
    assert any("did not reproduce" in p for p in deterministic_check(finding, sql, world, {}))


def test_sql_tool_is_read_only(tmp_path):
    _, sql = setup(tmp_path)
    assert sql.run("DROP TABLE x").error
    assert sql.run("SELECT 'drop' AS word").error is None   # keywords inside strings are fine


def test_generator_waits_out_transient_states(monkeypatch):
    import httpx

    from causeway import generator as g

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] <= 3:  # transient failures in a row: more than the 4 model attempts would allow if counted
            code = ["cloud_unreachable", "cloud_busy", "engine_not_running"][calls["n"] - 1]
            return httpx.Response(502 if calls["n"] < 3 else 503, text=f'{{"error":{{"code":"{code}"}}}}')
        assert request.headers["meterless-ttfb-ms"] and request.headers["meterless-scope"] == "user"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "meterless": {"routed_model": "nvidia/nemotron-3-ultra-550b-a55b"}})

    monkeypatch.setattr(g.time, "sleep", lambda s: None)
    gen = g.NightshiftGenerator(base_url="127.0.0.1:8088", token="t")
    gen._client = httpx.Client(transport=httpx.MockTransport(handler))
    out = gen("hi", role="deep")
    assert out.text == "ok" and out.model == "nvidia/nemotron-3-ultra-550b-a55b" and calls["n"] == 4


def test_generator_switches_model_after_one_hang(monkeypatch):
    import httpx

    from causeway import generator as g

    served = []

    def handler(request):
        model = __import__("json").loads(request.content)["model"]
        served.append(model)
        if model == g.MODELS["deep"]:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "meterless": {"routed_model": model}})

    monkeypatch.setattr(g.time, "sleep", lambda s: None)
    gen = g.NightshiftGenerator(base_url="127.0.0.1:8088", token="t")
    gen._client = httpx.Client(transport=httpx.MockTransport(handler))
    out = gen("hi", role="deep")
    assert out.model == g.MODELS["fast"] and served == [g.MODELS["deep"], g.MODELS["fast"]]


def test_generator_helpers():
    assert normalise_base_url("127.0.0.1:8088/v1/chat/completions") == "http://127.0.0.1:8088/v1"
    assert normalise_base_url("http://localhost:8088/healthz") == "http://localhost:8088/v1"
    assert strip_reasoning("The user asks ... 391.</think>391") == "391"
    assert strip_reasoning("<think>hmm</think>Answer") == "Answer"


def test_cross_measure_differences_only_for_named_companies(tmp_path):
    cols = ["ticker", "fiscal_year_end", "gross_margin_change_pts", "operating_margin_change_pts", "revenue_growth_pct"]
    rows = [("BBY", "2023-01-28", -1.08, -1.99, -10.55), ("DG", "2023-02-03", -0.37, -0.5, 10.59)]
    world = WorldModel(tmp_path / "w")
    sql = SqlTool(FakeClient(rows), NullEvidence())
    sql.results["q_2"] = QueryResult("q_2", "SELECT ...", cols, rows, 10)
    world.ingest.ingest_query_result("q_2", "SELECT ...", cols, rows)
    gap = {"id": "F1", "statement": "Best Buy's operating margin fell more", "evidence": ["q_2"], "numbers": {"gap_pts": 0.91}}
    assert deterministic_check(gap, sql, world, {}, focus="BBY") == []
    rounded = {"id": "F2", "statement": "Best Buy revenue fell", "evidence": ["q_2"], "numbers": {"revenue_change_pct": -11}}
    assert deterministic_check(rounded, sql, world, {}, focus="BBY")  # DG's -0.37 vs 10.59 must not ground it
