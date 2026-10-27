"""Meterless World Model engine (spec: meterless/engines/world-model/AGENTS.md), headless.

The shared, grounded state every Causeway agent reasons over. Pure-graph aggregate:
  entities       company, metric, period, price series, finding
  relationships  fact_of (fact -> company), measures (fact -> metric), in_period (fact -> period),
                 cites (finding -> query), about (finding -> company)
  facts          one numeric observation from one Zetaris query result, with provenance

Spec contracts implemented here:
  * Hash-based stable IDs: content-derived, never autoincrement (`stable_id`).
  * Append-only canonical store: events.jsonl is the source of truth; every view is a projection rebuilt from it.
  * Six-stage per-observation path: extract -> normalize -> resolve -> validate -> append -> project.
    Validate rejects anything without provenance. Append is the only exactly-once stage (dedupe key).
  * Idempotent ingest: re-ingesting the same query result produces the same world.
  * Read surface with a context builder that returns a structured, budgeted payload, plus provenance on every read.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1

# Columns that identify an observation's subject and period rather than being measures.
SUBJECT_COLUMNS = ("ticker", "series_name", "series_id")
# A query that filters on one subject (WHERE ticker = 'BBY') often doesn't select that column; its rows still belong
# to that subject. Only a single distinct literal counts, so a multi-company IN (...) list is never guessed at.
SUBJECT_FILTER = re.compile(r"\b(?:ticker|series_name|series_id)\s*=\s*'([^']+)'", re.IGNORECASE)
PERIOD_COLUMNS = ("fiscal_year_end", "period_end", "observation_date", "yr", "fiscal_year")
NON_MEASURES = set(SUBJECT_COLUMNS) | set(PERIOD_COLUMNS) | {
    "prior_fiscal_year_end", "basis_filing", "filed_date", "accession_number", "revenue_filing", "row_id", "cik",
    "entity_name", "sic_code", "sic_description", "xbrl_tag", "metric", "form_type", "fiscal_period", "period_start",
    "period_frame", "description", "story_role", "comparability_note", "fiscal_year_end_mmdd",
}


def normalize_key(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower())


def stable_id(kind: str, *parts) -> str:
    """Content-derived ID: the same thing always resolves to the same ID, in any process."""
    digest = hashlib.sha1("|".join([kind, *(normalize_key(p) for p in parts)]).encode()).hexdigest()[:12]
    return f"{kind}_{digest}"


class ValidationError(ValueError):
    pass


@dataclass
class Fact:
    id: str
    subject_id: str
    subject: str
    measure: str
    period: str | None
    value: float
    query_id: str
    sql_hash: str
    observed_at: float

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Projection:
    """Derived views, rebuilt from the event log. Never mutated from outside `WorldStore._project`."""
    entities: dict = field(default_factory=dict)
    relationships: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    findings: dict = field(default_factory=dict)


class WorldStore:
    """Canonical append-only store + projections. Single writer, many readers."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_path = self.root / "events.jsonl"
        self._lock = threading.Lock()
        self._keys: set[str] = set()
        self.view = Projection()
        self.rebuild()

    # -- write side ---------------------------------------------------------------------------
    def append(self, event: dict) -> bool:
        """Exactly-once append keyed on the event's dedupe key. Returns False if already present."""
        key = event["dedupe_key"]
        with self._lock:
            if key in self._keys:
                return False
            event = {"schema_version": SCHEMA_VERSION, "appended_at": round(time.time(), 3), **event}
            with self.log_path.open("a") as f:
                f.write(json.dumps(event, default=str) + "\n")
            self._keys.add(key)
            self._project(event)
            return True

    # -- projections --------------------------------------------------------------------------
    def rebuild(self) -> None:
        """Recompute every derived view from the log (the store is the source of truth)."""
        self.view = Projection()
        self._keys = set()
        if self.log_path.exists():
            for line in self.log_path.read_text().splitlines():
                if line.strip():
                    event = json.loads(line)
                    self._keys.add(event["dedupe_key"])
                    self._project(event)

    def _project(self, event: dict) -> None:
        kind = event["type"]
        if kind == "entity":
            ent = self.view.entities.setdefault(event["id"], {**event["entity"], "id": event["id"], "provenance": []})
            ent["provenance"].append(event["provenance"])
        elif kind == "relationship":
            self.view.relationships[event["id"]] = {k: event[k] for k in ("id", "rel", "src", "dst", "provenance")}
        elif kind == "fact":
            self.view.facts[event["id"]] = Fact(**event["fact"])
        elif kind == "finding_status":
            self.view.findings.setdefault(event["finding_id"], {}).update(event["finding"])


class IngestPipeline:
    """extract -> normalize -> resolve -> validate -> append -> project, per observation."""

    def __init__(self, store: WorldStore) -> None:
        self.store = store

    def ingest_query_result(self, query_id: str, sql: str, columns: list[str], rows: list[tuple], agent: str = "") -> dict:
        """Turn one Zetaris query result into entities, facts and relationships. Idempotent."""
        provenance = {"origin": "zetaris_query", "query_id": query_id, "agent": agent,
                      "sql_hash": hashlib.sha1(sql.encode()).hexdigest()[:12]}
        stats = {"facts": 0, "already_known": 0, "rejected": 0}
        filtered = {m.upper() for m in SUBJECT_FILTER.findall(sql)}
        default_subject = filtered.pop() if len(filtered) == 1 else None
        for record in self._extract(columns, rows):
            for event in self._resolve(self._normalize(record, default_subject), provenance):
                try:
                    self._validate(event)
                except ValidationError:
                    stats["rejected"] += 1
                    continue
                appended = self.store.append(event)
                if event["type"] == "fact":
                    stats["facts" if appended else "already_known"] += 1
        return stats

    def record_finding(self, finding: dict, verdict: str, reasons: list[str], round_no: int, provenance: dict) -> None:
        """Findings are entities too: their status (approved / rejected) and the queries they cite."""
        fid = stable_id("finding", finding.get("statement", ""))
        event = {"type": "finding_status", "dedupe_key": f"finding|{fid}|{round_no}|{verdict}", "finding_id": fid,
                 "finding": {"statement": finding.get("statement"), "verdict": verdict, "reasons": reasons,
                             "round": round_no, "evidence": finding.get("evidence", []), "scope": finding.get("scope")},
                 "provenance": provenance}
        self._validate(event)
        self.store.append(event)
        for qid in finding.get("evidence", []):
            rel = {"type": "relationship", "rel": "cites", "src": fid, "dst": f"query:{qid}",
                   "id": stable_id("rel", "cites", fid, qid), "provenance": provenance}
            rel["dedupe_key"] = rel["id"]
            self.store.append(rel)

    # -- stages -------------------------------------------------------------------------------
    @staticmethod
    def _extract(columns: list[str], rows: list[tuple]):
        for row in rows:
            yield dict(zip([c.lower() for c in columns], row))

    @staticmethod
    def _normalize(record: dict, default_subject: str | None = None) -> dict:
        subject = next((str(record[c]).upper() for c in SUBJECT_COLUMNS if record.get(c) not in (None, "")), default_subject)
        period = next((str(record[c])[:10] for c in PERIOD_COLUMNS if record.get(c) not in (None, "")), None)
        measures = {k: float(v) for k, v in record.items()
                    if k not in NON_MEASURES and isinstance(v, (int, float)) and not isinstance(v, bool)}
        return {"subject": subject, "period": period, "measures": measures}

    @staticmethod
    def _resolve(rec: dict, provenance: dict):
        if not rec["subject"]:
            return
        kind = "series" if "_" in rec["subject"] else "company"  # tickers have no underscores; PPI series names do
        subject_id = stable_id(kind, rec["subject"])
        yield {"type": "entity", "id": subject_id, "dedupe_key": f"entity|{subject_id}|{provenance['query_id']}",
               "entity": {"kind": kind, "name": rec["subject"]}, "provenance": provenance}
        for measure, value in rec["measures"].items():
            fact_id = stable_id("fact", rec["subject"], measure, rec["period"] or "", provenance["query_id"])
            yield {"type": "fact", "id": fact_id, "dedupe_key": fact_id, "provenance": provenance,
                   "fact": {"id": fact_id, "subject_id": subject_id, "subject": rec["subject"], "measure": measure,
                            "period": rec["period"], "value": value, "query_id": provenance["query_id"],
                            "sql_hash": provenance["sql_hash"], "observed_at": round(time.time(), 3)}}

    @staticmethod
    def _validate(event: dict) -> None:
        prov = event.get("provenance") or {}
        if not prov.get("origin"):
            raise ValidationError("no provenance: rejected")
        if event["type"] == "fact" and not prov.get("query_id"):
            raise ValidationError("fact without a source query: rejected")


class ReadSurface:
    """Lookups, projections and context builders. Consumers never read the store directly."""

    def __init__(self, store: WorldStore) -> None:
        self.store = store

    def facts_for_query(self, query_id: str) -> list[Fact]:
        return [f for f in self.store.view.facts.values() if f.query_id == query_id]

    def values_for_queries(self, query_ids: list[str]) -> list[float]:
        return [f.value for q in query_ids for f in self.facts_for_query(q)]

    def conflicts(self, tolerance: float = 0.001) -> list[dict]:
        """Same subject, measure and period reported with different values by different queries.

        Relative tolerance only: ratios like gross margin are < 1, and 0.2457 vs 0.2464 (a basis difference
        between filings) must still surface.
        """
        groups: dict[tuple, list[Fact]] = {}
        for f in self.store.view.facts.values():
            groups.setdefault((f.subject, f.measure, f.period), []).append(f)
        out = []
        for (subject, measure, period), facts in groups.items():
            values = {round(f.value, 6) for f in facts}
            if len(values) > 1 and max(values) - min(values) > tolerance * max(abs(v) for v in values):
                out.append({"subject": subject, "measure": measure, "period": period,
                            "values": [{"value": f.value, "query_id": f.query_id} for f in facts]})
        return out

    def build_context(self, subjects: list[str], measures: list[str] | None = None, max_facts: int = 60) -> dict:
        """Structured, budgeted payload of grounded facts for the given subjects, newest periods first."""
        wanted = {s.upper() for s in subjects}
        facts = [f for f in self.store.view.facts.values()
                 if f.subject in wanted and (not measures or f.measure in measures)]
        facts.sort(key=lambda f: (f.subject, f.measure, f.period or ""), reverse=True)
        seen, picked = set(), []
        for f in facts:  # one value per (subject, measure, period): the canonical merged view
            key = (f.subject, f.measure, f.period)
            if key not in seen:
                seen.add(key)
                picked.append({"subject": f.subject, "measure": f.measure, "period": f.period,
                               "value": f.value, "provenance": f.query_id})
        findings = [v for v in self.store.view.findings.values() if v.get("verdict") == "approved"]
        return {"facts": picked[:max_facts], "truncated": max(0, len(picked) - max_facts),
                "approved_findings": findings[-10:], "conflicts": self.conflicts()[:10]}


class WorldModel:
    """Facade wiring the services together for one investigation run."""

    def __init__(self, root: Path) -> None:
        self.store = WorldStore(root)
        self.ingest = IngestPipeline(self.store)
        self.read = ReadSurface(self.store)
