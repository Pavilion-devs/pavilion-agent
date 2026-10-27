"""Run evidence: every agent step, SQL query and model call as append-only JSONL under runs/<run_id>/logs/."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"


class Evidence:
    def __init__(self, run_id: str | None = None, root: Path = RUNS_DIR) -> None:
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.dir = root / self.run_id
        self.logs = self.dir / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._listeners = []

    def subscribe(self, fn) -> None:
        """fn(stream, record) is called for every logged record (used for live UI streaming)."""
        self._listeners.append(fn)

    def log(self, stream: str, record: dict) -> dict:
        record = {"ts": round(time.time(), 3), "run_id": self.run_id, **record}
        line = json.dumps(record, default=str)
        with self._lock:
            with (self.logs / f"{stream}.jsonl").open("a") as f:
                f.write(line + "\n")
        for fn in self._listeners:
            fn(stream, record)
        return record

    def write(self, name: str, content: str | dict) -> Path:
        path = self.dir / name
        path.write_text(content if isinstance(content, str) else json.dumps(content, indent=2, default=str))
        return path
