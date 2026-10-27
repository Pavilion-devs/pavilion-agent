"""Meterless H-MEM engine (spec: meterless/engines/hmem/AGENTS.md), headless.

Memory that carries lessons from one Causeway investigation to the next. Implemented services:
  MemoryStoreService      tiered persistence (short_term in memory, working + long_term on disk), CRUD,
                          enrichment (domain, namespace, entities, relatedTo, supersedes), promotion
  MemoryMiningService     plan_completion and user_correction events -> memories, via the injected
                          generator with tolerant JSON parsing and a no-model fallback
  MemoryRetrievalService  the canonical hybrid score, threshold 0.35, top 5, domain-grouped reinjection
                          block plus trace metadata
  TrustLedgerService      append-only audit log using the canonical action names
Feedback deltas are exact (helpful +0.05 and accessCount+1, not_helpful -0.03, wrong -0.20 plus a review tag).
Dreaming and the sleep cycle are out of scope for this build.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

LAYER_WEIGHT = {"long_term": 1.0, "working": 0.8, "short_term": 0.6}
THRESHOLD = 0.35
TOP_N = 5
RECENCY_TAU_DAYS = 14.0
ENTITY_CAP = 10
RELATED_CAP = 5
EMBED_DIM = 256
FEEDBACK = {"helpful": 0.05, "not_helpful": -0.03, "wrong": -0.20}
LEDGER_ACTIONS = {
    "create", "read", "update", "delete", "feedback", "promote", "demote", "merge", "conflict_detected",
    "conflict_resolved", "sleep_consolidate", "sleep_archive", "sleep_synthesize", "dream_proposed",
    "dream_approved", "dream_rejected", "restore",
}
STOPWORDS = set("the a an and or of to in on for is was were be by with as at from that this it its vs not no "
                "did does do why how what which than into over under per their they them".split())
TICKERS = {"TGT", "WMT", "COST", "KR", "DG", "DLTR", "BBY"}
CORRECTION_WORDS = re.compile(r"\b(instead|actually|correction|not\b|never|wrong|should)\b", re.IGNORECASE)


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9_]+", text.lower()) if t not in STOPWORDS and len(t) > 1]


def embed(text: str) -> list[float]:
    """Lightweight deterministic embedding: signed hashed bag of words, L2-normalised."""
    vec = [0.0] * EMBED_DIM
    for tok in tokens(text):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % EMBED_DIM] += 1.0 if (h >> 8) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return max(0.0, sum(x * y for x, y in zip(a, b)))


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def extract_entities(text: str) -> list[str]:
    found = [t for t in re.findall(r"\b[A-Z]{2,5}\b", text) if t in TICKERS]
    found += [m for m in ("gross_margin", "operating_margin", "inventory", "revenue", "cost_of_sales", "freight",
                          "warehousing", "markdown", "restatement", "peer_median") if m.replace("_", " ") in text.lower()
              or m in text.lower()]
    return list(dict.fromkeys(found))[:ENTITY_CAP]


@dataclass
class Memory:
    id: str
    content: str
    type: str                      # personal | factual | preference | general
    layer: str                     # short_term | working | long_term
    source: str                    # mandatory
    confidence: float              # mandatory
    provenance: dict               # mandatory for new writes: origin, learnedAt, label, refs
    timestamp: float
    lastAccessed: float
    accessCount: int = 0
    tags: list[str] = field(default_factory=list)
    embedding: list[float] = field(default_factory=list)
    domain: str = "general"
    namespace: str = "general"
    entities: list[str] = field(default_factory=list)
    relatedTo: list[str] = field(default_factory=list)
    supersedes: str | None = None
    supersededBy: str | None = None
    derivedFrom: list[str] = field(default_factory=list)
    missionId: str | None = None   # the investigation run that produced it


class TrustLedgerService:
    """Append-only audit log. Never destructively pruned."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def log(self, action: str, memory_id: str, actor: str, details: dict | None = None,
            previous_state: dict | None = None, new_state: dict | None = None) -> None:
        if action not in LEDGER_ACTIONS:
            raise ValueError(f"unknown ledger action {action}")
        entry = {"timestamp": round(time.time(), 3), "action": action, "memoryId": memory_id, "actor": actor,
                 "details": details or {}, "previousState": previous_state, "newState": new_state}
        with self.path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")


class MemoryStoreService:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger = TrustLedgerService(self.root / "ledger.jsonl")
        self.memories: dict[str, Memory] = {}
        self.load()

    # -- persistence ----------------------------------------------------------------------------
    def _path(self, layer: str) -> Path:
        return self.root / f"{layer}.json"

    def load(self) -> None:
        """Hydrate long_term + working, merge with short_term, dedupe by id, backfill embeddings."""
        short_term = {k: m for k, m in self.memories.items() if m.layer == "short_term"}
        merged: dict[str, Memory] = {}
        for layer in ("long_term", "working"):
            path = self._path(layer)
            if path.exists():
                for raw in json.loads(path.read_text()):
                    merged.setdefault(raw["id"], Memory(**raw))
        merged.update({k: v for k, v in short_term.items() if k not in merged})
        for m in merged.values():
            if not m.embedding:
                m.embedding = embed(m.content)
        self.memories = merged

    def save(self) -> None:
        for layer in ("long_term", "working"):
            rows = [asdict(m) for m in self.memories.values() if m.layer == layer]
            self._path(layer).write_text(json.dumps(rows, indent=1))

    # -- CRUD + enrichment ------------------------------------------------------------------------
    def add(self, content: str, *, source: str, confidence: float, provenance: dict, type: str = "factual",
            layer: str = "working", tags: list[str] | None = None, domain: str = "general",
            namespace: str | None = None, mission_id: str | None = None, actor: str = "system") -> Memory:
        if not source:
            raise ValueError("a memory without a source is rejected by construction")
        if not provenance or not provenance.get("origin"):
            raise ValueError("new memories need provenance with an origin")
        now = time.time()
        mem = Memory(id="mem_" + uuid.uuid4().hex[:10], content=content.strip(), type=type, layer=layer, source=source,
                     confidence=clamp01(confidence), provenance={"learnedAt": now, **provenance}, timestamp=now,
                     lastAccessed=now, tags=list(dict.fromkeys(tags or [])), embedding=embed(content),
                     domain=domain, namespace=namespace or domain, entities=extract_entities(content),
                     missionId=mission_id)
        self._link(mem)
        self.memories[mem.id] = mem
        self.ledger.log("create", mem.id, actor, {"source": source, "layer": layer}, new_state={"content": mem.content})
        self.save()
        return mem

    def _link(self, mem: Memory) -> None:
        """relatedTo by shared entities in the same domain; supersedes on near-duplicate correction language."""
        scored = []
        for other in self.memories.values():
            if other.supersededBy:
                continue
            sim = cosine(mem.embedding, other.embedding)
            if sim >= 0.82 and CORRECTION_WORDS.search(mem.content) and other.domain == mem.domain:
                mem.supersedes = other.id
                other.supersededBy = mem.id
                self.ledger.log("conflict_resolved", other.id, "system", {"supersededBy": mem.id, "similarity": round(sim, 3)})
            shared = set(mem.entities) & set(other.entities)
            if shared and other.domain == mem.domain:
                scored.append((len(shared) + sim, other))
        for _, other in sorted(scored, key=lambda x: -x[0])[:RELATED_CAP]:
            mem.relatedTo.append(other.id)
            if mem.id not in other.relatedTo and len(other.relatedTo) < RELATED_CAP:
                other.relatedTo.append(mem.id)

    def feedback(self, memory_id: str, verdict: str, actor: str = "critic") -> None:
        mem = self.memories[memory_id]
        before = {"confidence": mem.confidence, "accessCount": mem.accessCount}
        if verdict == "helpful":
            mem.accessCount += 1
        mem.confidence = clamp01(mem.confidence + FEEDBACK[verdict])
        if verdict == "wrong" and "review" not in mem.tags:
            mem.tags.append("review")
        self.ledger.log("feedback", memory_id, actor, {"verdict": verdict}, before,
                        {"confidence": mem.confidence, "accessCount": mem.accessCount})
        self.save()

    def promote(self, memory_id: str, actor: str = "system") -> None:
        mem = self.memories[memory_id]
        if mem.layer != "long_term":
            before = mem.layer
            mem.layer = "long_term"
            self.ledger.log("promote", memory_id, actor, previous_state={"layer": before}, new_state={"layer": "long_term"})
            self.save()


class MemoryRetrievalService:
    def __init__(self, store: MemoryStoreService) -> None:
        self.store = store

    def score(self, mem: Memory, query: str, q_embed: list[float], q_tokens: list[str], q_tags: set[str],
              q_domain: str, q_entities: set[str], now: float) -> float:
        top8 = set(q_tokens[:8])
        m_tokens = set(tokens(mem.content))
        keyword = len(top8 & m_tokens) / len(top8 | m_tokens) if top8 | m_tokens else 0.0
        tag = len(q_tags & set(mem.tags)) / len(q_tags) if q_tags else 0.0
        domain = 1.0 if mem.domain == q_domain else 0.0
        entity = len(q_entities & set(mem.entities)) / len(q_entities) if q_entities else 0.0
        recency = math.exp(-((now - mem.lastAccessed) / 86400.0) / RECENCY_TAU_DAYS)
        superseded = 1.0 if mem.supersededBy else 0.0
        raw = (0.35 * cosine(q_embed, mem.embedding) + 0.20 * keyword + 0.10 * tag + 0.10 * domain
               + 0.15 * entity + 0.05 * LAYER_WEIGHT[mem.layer] + 0.05 * recency - 0.20 * superseded)
        return clamp01(raw) * mem.confidence

    def retrieve(self, query: str, tags: list[str] | None = None, domain: str = "general",
                 entities: list[str] | None = None, top_n: int = TOP_N, threshold: float = THRESHOLD,
                 actor: str = "planner") -> dict:
        now = time.time()
        q_entities = set(entities or extract_entities(query))
        args = (query, embed(query), tokens(query), set(tags or []), domain, q_entities, now)
        ranked = sorted(((self.score(m, *args), m) for m in self.store.memories.values()), key=lambda x: -x[0])
        selected = [(s, m) for s, m in ranked if s >= threshold][:top_n]
        for s, m in selected:
            m.lastAccessed, m.accessCount = now, m.accessCount + 1
            self.store.ledger.log("read", m.id, actor, {"score": round(s, 3), "query": query[:120]})
        if selected:
            self.store.save()
        return {
            "memories": [{"id": m.id, "content": m.content, "layer": m.layer, "domain": m.domain,
                          "entities": m.entities, "score": round(s, 3)} for s, m in selected],
            "context": self.format(selected),
            "trace": {"retrievalReason": f"domain={domain}; entities={sorted(q_entities)}",
                      "strategy": "minimal" if len(selected) <= 2 else "comprehensive",
                      "considered": len(ranked), "threshold": threshold,
                      "scores": {m.id: round(s, 3) for s, m in ranked[:10]}},
        }

    @staticmethod
    def format(selected: list[tuple[float, Memory]]) -> str:
        """Domain-grouped reinjection block with layer markers."""
        groups: dict[str, list[str]] = {}
        for s, m in selected:
            ents = f" [{', '.join(m.entities)}]" if m.entities else ""
            groups.setdefault(m.domain, []).append(f"- ({m.layer}) {m.content}{ents}")
        return "\n".join(f"## {d}\n" + "\n".join(lines) for d, lines in groups.items())


MINING_PROMPTS = {
    "user_correction": (
        "A reviewer rejected an analyst agent's findings for the reasons below. Extract reusable METHOD lessons that "
        "would stop the same mistake in any future investigation in this sector. Each lesson is one imperative sentence, "
        "specific (name the table, metric or comparison), and not about one particular number."),
    "plan_completion": (
        "An investigation finished with the approved findings below. Extract reusable DOMAIN lessons for future "
        "investigations in this sector: which drivers or comparisons turned out to matter, and which did not. "
        "Each lesson is one sentence, generalisable beyond this exact year."),
}


class MemoryMiningService:
    def __init__(self, store: MemoryStoreService, generator=None) -> None:
        self.store = store
        self.generator = generator

    def mine(self, event_type: str, payload: str, *, domain: str, tags: list[str], mission_id: str,
             source: str, confidence: float, max_items: int = 4) -> list[Memory]:
        items = self._extract(event_type, payload, max_items)
        origin = "model_extraction" if items is not None else "direct_capture"
        if items is None:  # no-model fallback: keep a concise direct summary rather than dropping it
            items, layer = [payload.strip()[:280]], "short_term"
        else:
            layer = "working"
        memory_type = "preference" if event_type == "user_correction" else "factual"
        out = []
        for text in items:
            if isinstance(text, str) and len(text.strip()) >= 20:
                out.append(self.store.add(
                    text, source=source, confidence=confidence, type=memory_type, layer=layer, tags=tags,
                    domain=domain, mission_id=mission_id, actor="miner",
                    provenance={"origin": origin, "label": f"{event_type} in run {mission_id}", "event": event_type}))
        return out

    def _extract(self, event_type: str, payload: str, max_items: int) -> list[str] | None:
        if not self.generator:
            return None
        prompt = (f"{MINING_PROMPTS[event_type]}\nReturn a strict JSON array of at most {max_items} strings, nothing else."
                  f"\n\n{payload[:6000]}")
        try:
            text = self.generator(prompt, role="deep", agent="hmem_miner").text
        except Exception:
            return None
        text = re.sub(r"```(?:json)?|```", "", text).strip()
        start, end = text.find("["), text.rfind("]")
        try:
            items = json.loads(text[start:end + 1]) if start >= 0 else []
        except json.JSONDecodeError:
            items = [line.strip("-• \t\"") for line in text.splitlines() if len(line.strip()) > 20]
        return [i for i in items if isinstance(i, str)][:max_items]


class HMem:
    """Facade: one store shared across runs (that's the point), with retrieval and mining on top."""

    def __init__(self, root: Path, generator=None) -> None:
        self.store = MemoryStoreService(root)
        self.retrieval = MemoryRetrievalService(self.store)
        self.mining = MemoryMiningService(self.store, generator)
