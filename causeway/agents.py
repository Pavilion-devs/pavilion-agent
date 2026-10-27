"""The Causeway agents. Each has one job, its own prompt and model, and a JSON contract.

Planner (fast)      question -> plan: company, period, peer set, hypotheses to test
Investigator (deep) plan -> loop of {query | finish}; every finding cites the query ids behind it
Reporter (deep)     plan + approved findings -> analyst memo with a citation on every number
"""

from __future__ import annotations

import json
import re

from .evidence import Evidence
from .generator import NightshiftGenerator
from .tools import SCHEMA_GUIDE, SqlTool, format_result

NO_THINK = "detailed thinking off"


class AgentError(RuntimeError):
    pass


def parse_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (tolerates ```json fences and chatter)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AgentError(f"reply was not valid JSON: {exc}: {text[:300]}") from exc


def ask_json(gen: NightshiftGenerator, prompt: str, system: str, role: str, agent: str, retries: int = 2) -> dict:
    """Call the model and parse JSON, re-asking with the parse error if the reply is malformed."""
    last = ""
    for _ in range(retries + 1):
        reply = gen(prompt if not last else f"{prompt}\n\nYour previous reply could not be parsed ({last}). Reply with one valid JSON object only.",
                    system=system, role=role, agent=agent)
        try:
            return parse_json(reply.text)
        except AgentError as exc:
            last = str(exc)[:200]
    raise AgentError(f"{agent}: no valid JSON after {retries + 1} attempts: {last}")


# --------------------------------------------------------------------------- Planner

PLANNER_SYSTEM = f"""{NO_THINK}
You are the Planner in Causeway, a financial investigation agent for credit and equity analysts.
Turn the analyst's question into an investigation plan. You do not query data yourself.

{SCHEMA_GUIDE}

Reply with one JSON object:
{{"ticker": "...", "company": "...", "metric": "gross_margin | operating_margin | revenue | ...",
  "focus_period": "fiscal year label as the analyst means it, e.g. fiscal 2022",
  "focus_fiscal_year_end_window": ["YYYY-MM-DD", "YYYY-MM-DD"],
  "comparison": "what the change is measured against, usually the prior fiscal year",
  "peers": ["TICKER", ...],
  "excluded_peers": [{{"ticker": "...", "reason": "..."}}],
  "hypotheses": [{{"id": "H1", "driver": "...", "how_to_test": "which tables and comparisons"}}]}}
Hypotheses must cover both sides: sector-wide drivers (peers moved too, industry margin and cost PPIs) and
company-specific drivers (the company diverged from peers: cost of sales growing faster than revenue,
inventory build-up, operating leverage). 4-6 hypotheses."""


def plan(gen: NightshiftGenerator, evidence: Evidence, question: str, memories: str | None = None) -> dict:
    prompt = f"Analyst question: {question}"
    if memories:
        prompt += "\n\nLessons from earlier investigations in this sector (use them to order and shape the hypotheses):\n" + memories
    result = ask_json(gen, prompt, PLANNER_SYSTEM, role="fast", agent="planner")
    for key in ("ticker", "peers", "hypotheses"):
        if not result.get(key):
            raise AgentError(f"planner returned no {key}")
    evidence.log("steps", {"agent": "planner", "event": "plan", "plan": result})
    return result


# --------------------------------------------------------------------------- Investigator

INVESTIGATOR_SYSTEM = f"""{NO_THINK}
You are the Investigator in Causeway. You test an investigation plan against real data by writing SQL,
one query per turn, and reading the results. Never state a number you did not get from a query result.

{SCHEMA_GUIDE}

Each turn reply with exactly one JSON object, either:
{{"action": "query", "hypothesis": "H1", "purpose": "what this query establishes", "sql": "SELECT ..."}}
or, when every hypothesis has been tested (or you are out of turns):
{{"action": "finish",
  "findings": [{{"id": "F1", "hypothesis": "H1", "statement": "one precise sentence with the numbers",
                 "numbers": {{"label": value}}, "evidence": ["q_..."], "confidence": "high|medium|low",
                 "scope": "company-specific | sector-wide | mixed"}}],
  "decomposition": {{"company_change_pts": number, "peer_median_change_pts": number,
                     "company_specific_pts": number, "evidence": ["q_..."]}},
  "open_questions": ["..."]}}
Every finding needs at least one evidence query id that produced its numbers. Prefer few, decisive queries:
peer comparison of the metric change first, then the drivers."""


def investigate(gen: NightshiftGenerator, sql: SqlTool, evidence: Evidence, question: str, the_plan: dict,
                max_queries: int = 8, memories: str | None = None, revision: dict | None = None) -> dict:
    """revision = {"feedback": str, "approved": [...], "rejected": [...]} from the Critic's previous round."""
    transcript = [f"Analyst question: {question}", "Plan:\n" + json.dumps(the_plan, indent=1)]
    if memories:
        transcript.append("Lessons from earlier investigations in this sector (follow them):\n" + memories)
    if revision:
        seen_sql, earlier = set(), []
        for r in sql.results.values():  # skip the Critic's re-executions of the same SQL
            if r.query_id != "rejected" and r.sql not in seen_sql:
                seen_sql.add(r.sql)
                earlier.append(r)
        transcript.append("Results of queries you already ran (cite them by id; don't re-run them):\n"
                          + "\n\n".join(format_result(r, limit=25) for r in earlier if not r.error))
        transcript.append("Findings the Critic APPROVED (keep them, unchanged, in your final list):\n"
                          + json.dumps([{k: v for k, v in f.items() if k != "critic"} for f in revision["approved"]], indent=1))
        transcript.append("Findings the Critic REJECTED, with reasons and the required fix:\n"
                          + json.dumps([{"id": f.get("id"), "statement": f.get("statement"), **f["critic"]}
                                        for f in revision["rejected"]], indent=1))
        transcript.append("Critic feedback: " + revision["feedback"] +
                          "\nFix or drop each rejected finding. Run new queries only where a fix needs new data.")
    queries_run = 0
    while True:
        turns_left = max_queries - queries_run
        prompt = "\n\n".join(transcript) + f"\n\nQueries left: {turns_left}. " + (
            "Reply with your next JSON action." if turns_left > 0 else "No queries left: reply with action finish now.")
        action = ask_json(gen, prompt, INVESTIGATOR_SYSTEM, role="deep", agent="investigator")

        if action.get("action") == "query" and turns_left > 0:
            result = sql.run(action.get("sql", ""), purpose=action.get("purpose", ""), agent="investigator")
            queries_run += 1
            evidence.log("steps", {"agent": "investigator", "event": "query", "hypothesis": action.get("hypothesis"),
                                   "purpose": action.get("purpose"), "query_id": result.query_id,
                                   "rows": len(result.rows), "error": result.error})
            transcript.append(f"Query for {action.get('hypothesis')}: {action.get('purpose')}\n{format_result(result, limit=25)}")
            continue

        if action.get("action") == "finish":
            findings = action.get("findings") or []
            known = set(sql.results)
            for f in findings:  # drop citations to queries that never ran, so the Critic sees the gap
                f["evidence"] = [q for q in f.get("evidence", []) if q in known]
            evidence.log("steps", {"agent": "investigator", "event": "findings", "findings": findings,
                                   "decomposition": action.get("decomposition")})
            return action

        transcript.append("That reply was neither a valid query nor finish. Follow the JSON contract.")


# --------------------------------------------------------------------------- Reporter

REPORTER_SYSTEM = """You are the Reporter in Causeway. Write the analyst memo from the approved findings only.
Rules:
- Every number in the memo must carry the query id it came from, like (q_ab12cd34ef). Never introduce a number
  that is not in the findings.
- Structure, in Markdown: a one-paragraph answer; "Company vs sector" with the decomposition; "Drivers" as a
  short list with confidence; "Data notes" (comparability caveats, fiscal-year alignment, data-quality flags);
  "Open questions". Keep it under 450 words.
- End with: "Historical analysis of public filings; not investment advice."
Reply with the Markdown memo only."""


def report(gen: NightshiftGenerator, evidence: Evidence, question: str, the_plan: dict, investigation: dict,
           approved: list[dict], unresolved: list[dict]) -> str:
    strip = lambda fs: [{k: v for k, v in f.items() if k != "critic"} for f in fs]  # noqa: E731
    prompt = (f"Analyst question: {question}\n\nPlan:\n{json.dumps(the_plan, indent=1)}\n\n"
              f"Approved findings (the only ones you may state as conclusions):\n{json.dumps(strip(approved), indent=1)}\n\n"
              f"Decomposition:\n{json.dumps(investigation.get('decomposition'), indent=1)}\n\n"
              "Findings the Critic did NOT approve (mention them only under Open questions, as unverified, "
              f"with the Critic's reason):\n{json.dumps([{'statement': f.get('statement'), 'reasons': f['critic']['reasons']} for f in unresolved], indent=1)}\n\n"
              f"Open questions: {json.dumps(investigation.get('open_questions', []))}")
    memo = gen(prompt, system=REPORTER_SYSTEM, role="deep", agent="reporter").text
    evidence.log("steps", {"agent": "reporter", "event": "memo", "chars": len(memo)})
    return memo
