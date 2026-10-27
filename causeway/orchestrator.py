"""Runs one investigation end to end and writes its artifacts to runs/<run_id>/.

  H-MEM retrieve -> Planner -> [Investigator -> Critic] x up to max_rounds -> Reporter -> H-MEM mining
  Every Zetaris result is ingested into the World Model as it arrives; the Critic grounds numbers against it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import agents, critic
from .engines.hmem import HMem
from .engines.world_model import WorldModel
from .evidence import Evidence
from .generator import NightshiftGenerator
from .tools import SqlTool
from .zetaris_client import ZetarisClient

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
DOMAIN = "retail_trade"  # every company in the universe is SIC sector G (Retail Trade)


def investigate(question: str, evidence: Evidence | None = None, max_queries: int = 8, max_rounds: int = 3,
                state_dir: Path = STATE_DIR) -> dict:
    evidence = evidence or Evidence()
    started = time.monotonic()
    evidence.log("steps", {"agent": "orchestrator", "event": "start", "question": question})

    gen = NightshiftGenerator(evidence=evidence)
    world = WorldModel(state_dir / "world")
    hmem = HMem(state_dir / "hmem", generator=gen)

    def ingest(stream: str, rec: dict) -> None:
        if stream == "queries" and not rec.get("error") and rec.get("rows"):
            stats = world.ingest.ingest_query_result(rec["query_id"], rec["sql"], rec["columns"], rec["rows"], rec.get("agent", ""))
            evidence.log("world", {"event": "ingest", "query_id": rec["query_id"], **stats})

    evidence.subscribe(ingest)

    recalled = hmem.retrieval.retrieve(question, tags=[DOMAIN], domain=DOMAIN, actor="planner")
    evidence.log("steps", {"agent": "hmem", "event": "retrieve", "memories": recalled["memories"], "trace": recalled["trace"]})
    memory_block = recalled["context"] or None

    client = ZetarisClient()
    sql = SqlTool(client, evidence)
    rounds = []
    try:
        the_plan = agents.plan(gen, evidence, question, memories=memory_block)
        evidence.write("plan.json", the_plan)

        revision, investigation, verdict = None, None, None
        for round_no in range(1, max_rounds + 1):
            budget = max_queries if round_no == 1 else max(3, max_queries // 2)
            investigation = agents.investigate(gen, sql, evidence, question, the_plan, max_queries=budget,
                                               memories=memory_block, revision=revision)
            verdict = critic.review(gen, sql, world, evidence, question, the_plan, investigation, round_no)
            rounds.append({"round": round_no, "findings": investigation.get("findings"), "critic": {
                k: verdict[k] for k in ("approved", "rejected", "feedback", "recomputed", "all_approved")}})
            if verdict["all_approved"]:
                break
            revision = verdict
        evidence.write("rounds.json", rounds)

        memo = agents.report(gen, evidence, question, the_plan, investigation, verdict["approved"], verdict["rejected"])
        evidence.write("memo.md", memo)
    finally:
        client.close()

    learned = _learn(hmem, evidence, the_plan, rounds, recalled)
    evidence.write("memory.json", {"recalled": recalled, "learned": learned})
    evidence.write("world_summary.json", {"entities": len(world.store.view.entities), "facts": len(world.store.view.facts),
                                          "findings": len(world.store.view.findings), "conflicts": world.read.conflicts()})

    elapsed = round(time.monotonic() - started, 1)
    evidence.log("steps", {"agent": "orchestrator", "event": "done", "elapsed_s": elapsed, "rounds": len(rounds)})
    return {"run_id": evidence.run_id, "dir": str(evidence.dir), "plan": the_plan, "rounds": rounds,
            "memo": memo, "recalled": recalled, "learned": learned, "elapsed_s": elapsed}


def _learn(hmem: HMem, evidence: Evidence, the_plan: dict, rounds: list[dict], recalled: dict) -> list[dict]:
    """Critic rejections become method lessons; the final approved findings become domain lessons."""
    tags = [DOMAIN, f"ticker:{the_plan.get('ticker', '').upper()}"]
    learned = []
    rejections = [{"statement": f.get("statement"), "reasons": f["critic"]["reasons"], "fix": f["critic"].get("fix")}
                  for r in rounds for f in r["critic"]["rejected"]]
    if rejections:
        mems = hmem.mining.mine("user_correction", json.dumps(rejections, indent=1), domain=DOMAIN, tags=tags,
                                mission_id=evidence.run_id, source="critic", confidence=0.9)
        learned += [{"id": m.id, "content": m.content, "event": "user_correction"} for m in mems]
    final = rounds[-1]["critic"]
    if final["approved"]:
        payload = json.dumps([{"statement": f.get("statement"), "scope": f.get("scope")} for f in final["approved"]], indent=1)
        mems = hmem.mining.mine("plan_completion", payload, domain=DOMAIN, tags=tags,
                                mission_id=evidence.run_id, source="investigation", confidence=0.75)
        learned += [{"id": m.id, "content": m.content, "event": "plan_completion"} for m in mems]
    # Feedback on what was recalled: approved on the first round means the lessons helped.
    verdict = "helpful" if rounds and rounds[0]["critic"]["all_approved"] else "not_helpful"
    for m in recalled["memories"]:
        hmem.store.feedback(m["id"], verdict, actor="critic")
    evidence.log("steps", {"agent": "hmem", "event": "learn", "learned": learned,
                           "feedback": {m["id"]: verdict for m in recalled["memories"]}})
    return learned
