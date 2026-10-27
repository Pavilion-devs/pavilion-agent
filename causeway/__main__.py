"""CLI: python -m causeway ask "Why did Target's gross margin fall in fiscal 2022?"
       python -m causeway status
"""

from __future__ import annotations

import argparse
import sys

from .evidence import Evidence
from .generator import MODELS, NightshiftGenerator


def main() -> int:
    parser = argparse.ArgumentParser(prog="causeway")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ask = sub.add_parser("ask", help="run an investigation")
    ask.add_argument("question")
    ask.add_argument("--max-queries", type=int, default=8)
    sub.add_parser("status", help="check the Nightshift connection")
    args = parser.parse_args()

    if args.cmd == "status":
        gen = NightshiftGenerator()
        print(f"Nightshift {gen.base_url}: {gen.status()}")
        print("Role models:", MODELS)
        return 0

    from .orchestrator import investigate

    evidence = Evidence()

    def show(stream: str, rec: dict) -> None:
        if stream == "model_calls":
            if rec.get("served_model"):
                print(f"      · {rec['agent']} on {rec['served_model']} ({rec['latency_ms']} ms)", flush=True)
        elif stream != "steps":
            pass
        elif rec["agent"] == "critic" and rec["event"] == "recompute":
            print(f"  [critic] re-ran {rec['requeried']} cited queries, {rec['reproduced']} reproduced; "
                  f"automated problems: {rec['automated_problems'] or 'none'}", flush=True)
        elif rec["agent"] == "critic" and rec["event"] == "verdict":
            print(f"  [critic] round {rec['round']}: approved {rec['approved']}", flush=True)
            for r in rec["rejected"]:
                print(f"      x {r['id']} rejected: {' | '.join(r['reasons'])[:300]}", flush=True)
        elif rec["agent"] == "hmem":
            items = rec.get("memories") or rec.get("learned") or []
            print(f"  [hmem] {rec['event']}: {len(items)} memories", flush=True)
            for m in items:
                print(f"      · {m['content'][:150]}{' (score ' + str(m['score']) + ')' if 'score' in m else ''}", flush=True)
        else:
            detail = rec.get("purpose") or rec.get("question") or ""
            extra = f" -> {rec['query_id']} ({rec.get('rows')} rows{', ERROR' if rec.get('error') else ''})" if rec.get("query_id") else ""
            print(f"  [{rec['agent']}] {rec['event']}: {detail}{extra}", flush=True)

    evidence.subscribe(show)
    print(f"Run {evidence.run_id}")
    result = investigate(args.question, evidence, max_queries=args.max_queries)
    print("\n" + result["memo"])
    print(f"\nArtifacts: {result['dir']}  ({result['elapsed_s']} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
