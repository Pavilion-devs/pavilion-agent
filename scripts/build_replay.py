"""Bundle recorded runs into web/replay_data.js for the replay page (no live services needed to view it).

Usage: .venv/bin/python scripts/build_replay.py runs/<target_run> runs/<second_run> ...

Everything shown comes from the run's own logs: steps, Zetaris queries (rows trimmed to 25), the Critic's
rounds, Nightshift model calls with their receipts, the memo, and what H-MEM recalled and learned.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_ROWS = 25


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def bundle(run_dir: Path) -> dict:
    logs = run_dir / "logs"
    steps = jsonl(logs / "steps.jsonl")
    calls = jsonl(logs / "model_calls.jsonl")
    queries = {}
    for q in jsonl(logs / "queries.jsonl"):
        queries.setdefault(q["query_id"], {
            "id": q["query_id"], "agent": q["agent"], "purpose": q.get("purpose", ""), "sql": q["sql"],
            "columns": q["columns"], "rows": q["rows"][:MAX_ROWS], "row_count": len(q["rows"]),
            "elapsed_ms": q.get("elapsed_ms"), "error": q.get("error")})
    t0 = float(steps[0]["ts"])
    events = []
    for s in steps:
        e = {k: v for k, v in s.items() if k not in ("run_id",)}
        e["t"] = round(float(s["ts"]) - t0, 1)
        events.append(e)
    for c in calls:
        e = {"t": round(float(c["ts"]) - t0, 1), "agent": c.get("agent"), "event": "model_call"}
        if "served_model" in c:
            e.update(model=c["served_model"], latency_ms=c["latency_ms"], receipt=c.get("receipt_id"),
                     tokens_out=c.get("tokens_out"), cached=c.get("from_cache"))
        elif c.get("event") == "fallback":
            e.update(event="fallback", model=c["to"], frm=c["from"])
        else:
            continue  # failed attempts are summarised in stats, not replayed one by one
        events.append(e)
    events.sort(key=lambda e: e["t"])
    ok = [c for c in calls if "served_model" in c]
    stats = {"model_calls": len(ok), "hung_calls": sum(1 for c in calls if "Timeout" in str(c.get("error", ""))),
             "model_seconds": round(sum(c["latency_ms"] for c in ok) / 1000),
             "queries": sum(1 for q in queries.values() if q["agent"] == "investigator"),
             "recomputes": sum(1 for q in queries.values() if q["agent"] == "critic"),
             "elapsed_s": next((float(s["elapsed_s"]) for s in steps if s.get("event") == "done"), None),
             "receipts": sum(1 for c in ok if c.get("receipt_id")),
             "by_model": {}}
    for c in ok:
        stats["by_model"][c["served_model"]] = stats["by_model"].get(c["served_model"], 0) + 1
    world = json.loads((run_dir / "world_summary.json").read_text()) if (run_dir / "world_summary.json").exists() else {}
    return {"id": run_dir.name, "question": steps[0]["question"], "plan": json.loads((run_dir / "plan.json").read_text()),
            "rounds": json.loads((run_dir / "rounds.json").read_text()), "memo": (run_dir / "memo.md").read_text(),
            "memory": json.loads((run_dir / "memory.json").read_text()), "world": world,
            "queries": queries, "events": events, "stats": stats}


def dq_rules() -> list[dict]:
    text = (ROOT / "zetaris_sql/dq/01_dq_rules.sql").read_text()
    rules = []
    for m in re.finditer(r"-- (.+?)\nREGISTER DQ (\w+) TABLE \S+\.(\w+) AS\n(.+?);", text, re.DOTALL):
        rules.append({"why": m.group(1), "name": m.group(2), "table": m.group(3), "rule": " ".join(m.group(4).split()),
                      "invalid": 0})
    return rules


def main(run_dirs: list[str]) -> None:
    data = {"runs": [bundle(Path(r)) for r in run_dirs], "dq": dq_rules()}
    out = ROOT / "web" / "replay_data.js"
    out.parent.mkdir(exist_ok=True)
    out.write_text("window.PAVILION = " + json.dumps(data, default=str) + ";\n")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB): " + ", ".join(r["id"] for r in data["runs"]))


if __name__ == "__main__":
    main(sys.argv[1:])
