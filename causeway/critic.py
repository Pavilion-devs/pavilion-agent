"""The Critic: a gate, not an opinion. A finding is approved only if both passes approve it.

1. Deterministic pass
   - the finding cites at least one query that actually ran
   - every cited query is RE-EXECUTED against Zetaris and must reproduce the same rows
   - every number in the finding must be grounded in World Model facts from its cited queries:
     the value itself, the value as a percentage, a difference of two facts, or a median of one measure
2. Model pass (Nemotron Ultra): methodology review against a domain checklist
"""

from __future__ import annotations

import hashlib
import json
import statistics
from concurrent.futures import ThreadPoolExecutor

from .agents import ask_json
from .engines.world_model import WorldModel
from .evidence import Evidence
from .generator import NightshiftGenerator
from .tools import SqlTool, format_result

ABS_TOL = 0.051   # numbers quoted to 1-2 decimals
REL_TOL = 0.005

CRITIC_SYSTEM = """detailed thinking off
You are the Critic in Causeway. You review an Investigator's findings against the query results they cite.
Approve a finding only if it is supported by the cited results AND follows this methodology checklist:
1. Year-over-year changes come from edgar.margin_change (both years from one filing); annual_financials rows from
   different years can sit on different bases after restatements.
2. A peer may be excluded only for a documented comparability reason with evidence (different cost definition,
   restatement, different fiscal calendar). Excluding a peer because its result differs is cherry-picking: reject.
3. Retail-margin PPIs (dept_store_margins, grocery_margins, electronics_margins) measure retailers' margin per unit
   sold, in index points. They are not percentage points of gross margin and not input costs. Claims about cost
   pressure need truckload_freight / warehousing / core_final_demand.
4. Year-end balances (annual_financials.inventory) cannot rule out intra-year effects. Claims about inventory
   build-up or clearance need quarterly 10-Q facts (financial_fact, fiscal_period Q1-Q3).
5. Causal words ("driven by", "ruled out", "eliminates") need direct evidence; otherwise the finding must be
   phrased as "consistent with" and carry low or medium confidence.
6. The headline decomposition must use the same peer set throughout: company-specific = company change - peer median.
7. Numbers must be stated at the precision of the query results (no rounding 4.64 to 4).
The automated checks already re-ran every cited query and tested whether each number appears in the results;
treat their failures as decisive.

You review ONE finding per request. Reply with one JSON object:
{"verdict": "approve" | "reject", "reasons": ["..."],
 "fix": "the specific query or rewording that would make it acceptable (empty if approved)"}"""

REVIEW_WORKERS = 2  # Nightshift caps calls in flight per app; 3 plus fallbacks hit the cap


def _fingerprint(rows: list[tuple]) -> str:
    norm = [tuple(round(v, 6) if isinstance(v, float) else v for v in r) for r in rows]
    return hashlib.sha1(json.dumps(sorted(map(repr, norm))).encode()).hexdigest()[:16]


def _candidates(values: list[float]) -> set[float]:
    out = set()
    for v in values:
        out.update({v, v * 100, v / 100})
    return out


def _grounded(x: float, values: list[float], medians: list[float], groups: list[list[float]] | None = None) -> str | None:
    """How a quoted number is supported by the facts, or None if it isn't.

    Differences only count within a group of comparable values (one measure with its medians, or one company in one
    period across measures): across everything
    in a result, some pair lands within tolerance of almost any number, which let 13.83 rounded to 14 pass.
    """
    def close(a: float, b: float) -> bool:
        return abs(a - b) <= max(ABS_TOL, REL_TOL * abs(b))

    base = _candidates(values)
    if any(close(x, c) for c in base):
        return "direct"
    if any(close(x, c) for c in _candidates(medians)):
        return "median"
    for group in (groups if groups is not None else [values + medians]):
        pool = sorted(_candidates(group))
        if len(pool) > 1200:
            continue
        for i, a in enumerate(pool):
            for b in pool[i + 1:]:
                if close(abs(x), abs(a - b)):
                    return "difference"
    return None


def deterministic_check(finding: dict, sql: SqlTool, world: WorldModel, recomputed: dict[str, str],
                        focus: str | None = None) -> list[str]:
    """focus = the plan's company, which findings often call by name ("Best Buy") rather than ticker."""
    problems = []
    evidence_ids = [q for q in finding.get("evidence", []) if q in sql.results]
    if not evidence_ids:
        return ["cites no query that actually ran"]
    for qid in evidence_ids:
        original = sql.results[qid]
        if original.error:
            problems.append(f"{qid} returned an error, so it can't support a claim")
            continue
        if qid not in recomputed:  # re-run each cited query once per review round
            rerun = sql.run(original.sql, purpose=f"recompute {qid}", agent="critic")
            recomputed[qid] = "ok" if (not rerun.error and _fingerprint(rerun.rows) == _fingerprint(original.rows)) else (
                f"error: {rerun.error}" if rerun.error else "different rows")
        if recomputed[qid] != "ok":
            problems.append(f"{qid} did not reproduce on re-execution ({recomputed[qid]})")

    facts = [f for q in evidence_ids for f in world.read.facts_for_query(q)]
    values = [f.value for f in facts]
    by_measure: dict[tuple, list[float]] = {}
    for f in facts:
        by_measure.setdefault((f.query_id, f.measure, f.period), []).append(f.value)
    medians = [statistics.median(v) for v in by_measure.values() if len(v) >= 3]
    by_name: dict[str, list[float]] = {}  # one measure across companies and periods, plus its medians
    for (_, measure, _), vals in by_measure.items():
        by_name.setdefault(measure, []).extend(vals + ([statistics.median(vals)] if len(vals) >= 3 else []))
    # One company in one period, across measures (operating vs gross change), for the companies the finding names:
    # a peer's unrelated measures would otherwise ground numbers by coincidence (DG -0.37 vs 10.59 "grounded" 11).
    text = json.dumps(finding).upper()
    named = {f.subject for f in facts if f.subject and f.subject in text} | ({focus.upper()} if focus else set())
    by_row: dict[tuple, list[float]] = {}
    for f in facts:
        if f.subject in named:
            by_row.setdefault((f.subject, f.period), []).append(f.value)
    groups = list(by_name.values()) + list(by_row.values())
    for label, value in (finding.get("numbers") or {}).items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if _grounded(float(value), values, medians, groups) is None:
            problems.append(f"number '{label}' = {value} is not in the cited query results "
                            f"(nor a percentage, difference or median of them)")
    return problems


def review(gen: NightshiftGenerator, sql: SqlTool, world: WorldModel, evidence: Evidence, question: str,
           the_plan: dict, investigation: dict, round_no: int) -> dict:
    findings = investigation.get("findings") or []
    recomputed: dict[str, str] = {}
    auto = {f.get("id", f"F{i}"): deterministic_check(f, sql, world, recomputed, the_plan.get("ticker"))
            for i, f in enumerate(findings, 1)}
    evidence.log("steps", {"agent": "critic", "event": "recompute", "round": round_no, "requeried": len(recomputed),
                           "reproduced": sum(1 for v in recomputed.values() if v == "ok"),
                           "automated_problems": {k: v for k, v in auto.items() if v}})

    context = (f"Analyst question: {question}\n\nPlan (peers, exclusions):\n"
               f"{json.dumps({k: the_plan.get(k) for k in ('ticker', 'peers', 'excluded_peers', 'focus_period')}, indent=1)}\n\n"
               f"Investigation decomposition:\n{json.dumps(investigation.get('decomposition'), indent=1)}")

    def review_one(fid: str, finding: dict) -> dict:
        """Small prompt: one finding and only the results it cites. Runs in parallel with the others."""
        cited = [q for q in finding.get("evidence", []) if q in sql.results]
        subjects = {f.subject for q in cited for f in world.read.facts_for_query(q)}
        conflicts = [c for c in world.read.conflicts() if c["subject"] in subjects][:4]
        prompt = (f"{context}\n\nFinding to review:\n{json.dumps(finding, indent=1)}\n\n"
                  f"Automated check problems for this finding: {json.dumps(auto[fid])}\n\n"
                  f"Conflicting facts about the same companies: {json.dumps(conflicts)}\n\n"
                  "Results of the queries it cites:\n" + "\n\n".join(format_result(sql.results[q], limit=15) for q in cited))
        try:
            return ask_json(gen, prompt, CRITIC_SYSTEM, role="deep", agent="critic")
        except Exception as exc:  # a stuck review blocks only its own finding, and never approves it
            return {"verdict": "reject", "reasons": [f"methodology review unavailable ({type(exc).__name__}); not approved"],
                    "fix": "", "unreviewed": True}

    ids = [(f.get("id", f"F{i}"), f) for i, f in enumerate(findings, 1)]
    with ThreadPoolExecutor(max_workers=REVIEW_WORKERS) as pool:
        model_verdicts = dict(zip([fid for fid, _ in ids], pool.map(lambda pair: review_one(*pair), ids)))

    approved, rejected = [], []
    for fid, f in ids:
        mv = model_verdicts[fid]
        reasons = auto[fid] + (mv.get("reasons", []) if mv.get("verdict") != "approve" else [])
        ok = not auto[fid] and mv.get("verdict") == "approve"
        entry = {**f, "critic": {"verdict": "approved" if ok else "rejected", "reasons": reasons, "fix": mv.get("fix")}}
        (approved if ok else rejected).append(entry)
        world.ingest.record_finding(f, "approved" if ok else "rejected", reasons, round_no,
                                    {"origin": "critic", "run_id": evidence.run_id})

    feedback = " ".join(f"{f.get('id')}: {f['critic'].get('fix') or '; '.join(f['critic']['reasons'])}" for f in rejected)
    result = {"round": round_no, "approved": approved, "rejected": rejected, "all_approved": not rejected,
              "feedback": feedback, "recomputed": recomputed}
    evidence.log("steps", {"agent": "critic", "event": "verdict", "round": round_no,
                           "approved": [f.get("id") for f in approved],
                           "rejected": [{"id": f.get("id"), "reasons": f["critic"]["reasons"]} for f in rejected]})
    return result
