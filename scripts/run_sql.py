"""Run a Zetaris SQL file one statement at a time over JDBC.

Usage: .venv/bin/python scripts/run_sql.py FILE.sql [--skip-exists] [--pause SECONDS]

`YOUR_APP_NAME YOUR_CONTACT_EMAIL` in the file is replaced with SEC_USER_AGENT
from .env, so no contact details live in committed SQL.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from causeway.zetaris_client import ZetarisClient  # noqa: E402

UA_PLACEHOLDER = "YOUR_APP_NAME YOUR_CONTACT_EMAIL"


STATEMENT_MARKER = "-- >>>"


def split_statements(text: str) -> list[str]:
    """Split on semicolons at line ends, dropping comment-only chunks.

    Files containing `-- >>>` marker lines are split on those markers instead, so a
    multi-table COMPILE USL (whose DDL has inner semicolons) reaches Zetaris intact.
    """
    if any(line.strip() == STATEMENT_MARKER for line in text.splitlines()):
        chunks = [[]]
        for line in text.splitlines():
            if line.strip() == STATEMENT_MARKER:
                chunks.append([])
            elif not line.strip().startswith("--"):
                chunks[-1].append(line)
        return [s for s in ("\n".join(c).strip().rstrip(";") for c in chunks) if s]
    statements, current = [], []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).rstrip().rstrip(";"))
            current = []
    if current:
        statements.append("\n".join(current))
    return statements


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--skip-exists", action="store_true", help="treat 'already exists' errors as success")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds to wait between statements")
    args = parser.parse_args()

    ua = os.environ.get("SEC_USER_AGENT", "")
    text = Path(args.file).read_text()
    if UA_PLACEHOLDER in text:
        if "@" not in ua:
            sys.exit("SEC_USER_AGENT in .env must be 'app_name you@domain.tld'")
        text = text.replace(UA_PLACEHOLDER, ua)

    client = ZetarisClient()
    failures = 0
    for i, stmt in enumerate(split_statements(text), 1):
        head = " ".join(stmt.split())[:90]
        result = client.run_sql(stmt)
        if result.error and args.skip_exists and "already exist" in result.error.lower():
            print(f"[{i}] SKIP (exists) {head}")
        elif result.error:
            failures += 1
            print(f"[{i}] FAIL {result.elapsed_ms}ms {head}\n     {result.error}")
        else:
            preview = f" -> {result.rows[:3]}" if result.rows else ""
            print(f"[{i}] OK   {result.elapsed_ms}ms {head}{preview}")
        if args.pause:
            time.sleep(args.pause)
    client.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
