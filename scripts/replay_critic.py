"""Re-run only the Critic against a saved run's plan, findings and query results.

Usage: .venv/bin/python scripts/replay_critic.py runs/<run_id>

The cited queries are still re-executed live against Zetaris (that's the point of the recompute pass);
the World Model is rebuilt in a temporary folder from the run's logged query results.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from causeway import critic  # noqa: E402
from causeway.engines.world_model import WorldModel  # noqa: E402
from causeway.evidence import Evidence  # noqa: E402
from causeway.generator import NightshiftGenerator  # noqa: E402
from causeway.tools import SqlTool  # noqa: E402
from causeway.zetaris_client import QueryResult, ZetarisClient  # noqa: E402


def main(run_dir: Path) -> None:
    logs = run_dir / "logs"
    plan = json.loads((run_dir / "plan.json").read_text())
    steps = [json.loads(l) for l in (logs / "steps.jsonl").read_text().splitlines()]
    question = next(s["question"] for s in steps if s.get("event") == "start")
    last = [s for s in steps if s.get("event") == "findings"][-1]
    investigation = {"findings": last["findings"], "decomposition": last.get("decomposition")}

    evidence = Evidence(run_id=f"replay-{run_dir.name}-{time.strftime('%H%M%S')}")
    with tempfile.TemporaryDirectory() as tmp:
        world = WorldModel(Path(tmp))
        sql = SqlTool(ZetarisClient(), evidence)
        for line in (logs / "queries.jsonl").read_text().splitlines():
            q = json.loads(line)
            if q["agent"] == "investigator":
                sql.results[q["query_id"]] = QueryResult(q["query_id"], q["sql"], q["columns"],
                                                         [tuple(r) for r in q["rows"]], q["elapsed_ms"], q["error"])
                if not q["error"]:
                    world.ingest.ingest_query_result(q["query_id"], q["sql"], q["columns"], q["rows"])
        started = time.monotonic()
        verdict = critic.review(NightshiftGenerator(evidence=evidence), sql, world, evidence, question, plan, investigation, 1)
        sql.client.close()
    print(f"Critic took {time.monotonic() - started:.0f} s; recompute: {verdict['recomputed']}")
    for f in verdict["approved"]:
        print(f"  APPROVED {f.get('id')}: {f.get('statement')}")
    for f in verdict["rejected"]:
        print(f"  REJECTED {f.get('id')}: {f.get('statement')}\n      reasons: {f['critic']['reasons']}\n      fix: {f['critic'].get('fix')}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
