"""Zetaris access over the official Lightning JDBC driver.

Every query the agents run goes through `run_sql`, which records a query id,
the SQL, row count and timing so claims can cite the exact query behind them.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DRIVER_CLASS = "com.zetaris.lightning.jdbc.LightningDriver"


@dataclass
class QueryResult:
    query_id: str
    sql: str
    columns: list[str]
    rows: list[tuple]
    elapsed_ms: int
    error: str | None = None
    meta: dict = field(default_factory=dict)

    def as_dicts(self) -> list[dict]:
        return [dict(zip(self.columns, r)) for r in self.rows]


class ZetarisClient:
    """One JDBC connection, serialised: Zetaris expects one statement per call."""

    def __init__(self) -> None:
        jar = Path(os.environ["ZETARIS_JDBC_JAR"])
        if not jar.is_absolute():
            jar = (PROJECT_ROOT / jar).resolve()
        self._jar = str(jar)
        self._url = os.environ.get("ZETARIS_JDBC_URL", "jdbc:zetaris:lightning@localhost:10000")
        self._user = os.environ["ZETARIS_USER"]
        self._password = os.environ["ZETARIS_PASSWORD"]
        self._conn = None
        self._lock = threading.Lock()

    def _connect(self):
        if self._conn is None:
            import jaydebeapi

            self._conn = jaydebeapi.connect(DRIVER_CLASS, self._url, [self._user, self._password], self._jar)
        return self._conn

    def run_sql(self, sql: str, max_rows: int = 5000) -> QueryResult:
        sql = sql.strip().rstrip(";")
        query_id = "q_" + uuid.uuid4().hex[:10]
        started = time.monotonic()
        with self._lock:
            try:
                cur = self._connect().cursor()
                try:
                    cur.execute(sql)
                    if cur.description is None:
                        columns, rows = [], []
                    else:
                        columns = [d[0] for d in cur.description]
                        rows = [tuple(_to_python(v) for v in r) for r in cur.fetchmany(max_rows)]
                finally:
                    cur.close()
                error = None
            except Exception as exc:  # JDBC surfaces everything as generic Java exceptions
                columns, rows, error = [], [], _short_error(str(exc))
                self.close()
        elapsed = int((time.monotonic() - started) * 1000)
        return QueryResult(query_id, sql, columns, rows, elapsed, error)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


def _short_error(text: str) -> str:
    """Reduce a Java stack trace to the message an agent can act on."""
    if "<<<" in text and ">>>" in text:
        return text[text.index("<<<") + 3 : text.index(">>>")].strip()
    first = text.strip().splitlines()[0] if text.strip() else text
    for prefix in ("java.sql.SQLException:", "org.apache.hive.service.cli.HiveSQLException:"):
        first = first.replace(prefix, "")
    return first.strip()[:600]


def _to_python(value):
    """Convert Java boxed values (BigDecimal etc.) that JayDeBeApi leaves as-is."""
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    text = str(value)
    try:
        return float(text) if any(c in text for c in ".eE") else int(text)
    except ValueError:
        return text
