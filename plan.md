# Causeway — Build Plan

> Working name. An agent that explains **why a public company's numbers moved**, and whether the cause is the **company** or its **whole sector**, with every claim traced back to a query.

**Hackathon:** Open Agent Hackathon 2026 (GenAI.Works / HackOS)
**Primary track:** 2 — *The Agent That Can Explain Why*
**Also targeting:** NVIDIA "Best use of Nemotron" (DGX Spark), and the Zetaris Demo Challenge (opens 29 Oct)
**Team:** Pavilion

---

## 1. The problem and the user

**User:** a credit or equity analyst (also useful for financial journalists and students).

**Their question:** *"Why did Company X's gross margin fall in FY2025? Is it the company, or is the whole sector down?"*

**Today:** they pull 10-K figures by hand, build a peer set in a spreadsheet, check input-cost indices, and write the memo themselves. It takes hours, and the numbers are easy to get wrong.

**With Causeway:** they type the question and get, within a few minutes, an evidence-backed memo:
- the movement, quantified (what changed, by how much, over which periods)
- the cause split into **company-specific** and **sector-wide**, with peer comparison
- the likely drivers (revenue mix, cost of revenue, input costs, and so on), each with a confidence level
- a link from every number to the SQL that produced it, and the critic's verdict on it
- the data-quality issues found in the source filings along the way

**Not financial advice.** The memo explains historical, public data; it doesn't recommend trades. This disclaimer appears in the UI and the README.

---

## 2. What makes it strong (mapped to scoring)

| Criterion (pts) | How we earn it |
|---|---|
| **Impact (30)** | A real user, a real recurring question, real public filings rather than synthetic data. "Company vs. sector" is the question analysts really ask. |
| **Technical (20)** | Multi-agent loop with a real critic that **re-executes SQL** to check every figure; JSONL traces; idempotent ingest; tests; sample mode. |
| **Innovation (15)** | Critic as a **recompute gate**, not an opinion. H-MEM makes the *second* investigation in a sector smarter than the first. Zetaris data-quality rules act as tripwires the agent investigates. |
| **Demo (15)** | Live run: the critic rejects a claim, sends the investigator back, and the memo improves. Then a second company in the same sector, showing memory carry-over. |
| **Product & UX (10)** | Simple web page: one question box, a live agent timeline, a final memo with clickable evidence. |
| **Sponsor Tech (10)** | Zetaris is the only data layer (USL + DQ rules). Meterless World Model + H-MEM run in working features, with tests. All model calls go through Nightshift to Nemotron, logged per call. |

**Things the rules say will fail us, and how we avoid each:**
- "Fixed chain with no critique" → the critic can reject and loop (capped rounds).
- "One agent with several labels" → each agent has its own role, prompt, inputs, and model; the critic also runs deterministic checks.
- "Hardcoded outputs / fake activity" → every claim needs a query ID that ran; sample mode replays *recorded* real runs and is labelled as a replay.

---

## 3. Data layer — Zetaris

Zetaris holds every source; the agents query **only** through Zetaris SQL.

### Sources (need 3+; Zetaris asks for 2–3 exploratory queries each)

**Status, 27 Oct: built and verified.** Details and gotchas in `docs/zetaris-findings.md`.

| # | Source | What it gives us | Zetaris view |
|---|---|---|---|
| 1 | **SEC EDGAR companyconcept** (26 REST tables) | Revenue, cost of sales, operating income, inventory for 7 retailers, each mapped from the XBRL tag that company actually uses | `edgar.financial_fact` |
| 2 | **SEC EDGAR submissions** (7 REST tables) | Company profile: SIC code, fiscal year end, comparability notes | `edgar.company` |
| 3 | **company_dns SIC hierarchy** (V4, REST) | SIC code → industry group → major group → sector | `company_dns.sic_hierarchy` |
| 4 | **BLS Producer Price Indexes** (CSV via FRED, file table) | Retail margin PPIs (department stores, grocery, electronics), truckload freight, warehousing, core PPI | `macro.ppi` |

Demo universe: Target, Walmart, Costco, Kroger, Dollar General, Dollar Tree, Best Buy (`zetaris_sql/universe.json`). Headline story: Target's FY2022 gross margin fell 4.71 pts vs a peer median of −0.74.

Dropped from the original plan: the SEC Frames API (peers come from the pre-registered universe instead) and NOAA (not needed for retail).

### Unified Semantic Layer (USL)
One business view joining the sources:
- `company` (CIK, name, SIC) → `industry` (SIC hierarchy) → `sector`
- `financial_fact` (CIK, concept, period, value, unit, filing accession)
- `peer_fact` (concept, period, CIK, value) from Frames
- `cost_index` (industry/series, period, value) from BLS

Deliver the USL **DDL**, 2–3 queries showing it's active, and the derived metrics (gross margin, YoY change, peer median).

### Data-quality rules (the "tripwires")
- **FK rule:** every company's SIC exists in the SIC hierarchy (Zetaris's own recipe already found a real bad record, IBM).
- **Accounting identity:** `GrossProfit ≈ Revenue − CostOfRevenue` within tolerance; a violation flags a filing for the agent to explain.
- *(stretch)* **Period continuity:** no missing quarters in the window under analysis.

### Licence note
EDGAR data is free to query, but bulk redistribution isn't cleared. We **query live** and commit only small sample snippets for sample mode.

---

## 4. Agents

All agents call models through **one injected generator** pointed at Nightshift (as the Meterless engine docs require).

```
question
   │
   ▼
Planner ──► Investigator ⇄ Critic ──► Reporter ──► memo + evidence
  (scope,     (writes SQL,    (re-runs SQL,
  peers,       drafts claims   checks numbers,
  metrics)     with query IDs) rejects/approves)
            ▲                      │
            └── reject + reason ───┘   (max 3 rounds)
```

| Agent | Job | Model (via Nightshift) |
|---|---|---|
| **Planner** | Resolve the company (name → CIK), the metric, the periods; choose the peer set from SIC; pull relevant memories from H-MEM | Nemotron 3.5 Lightning |
| **Investigator** | Write Zetaris SQL, run it, draft claims. Every claim cites the query IDs behind it. | Nemotron 3.5 Lightning |
| **Critic** | **Deterministic pass:** re-run each cited query and compare numbers. **Model pass:** check that the claim follows from the evidence, and flag company-vs-sector confusion. Approve, or reject with reasons. | Nemotron 3 Ultra |
| **Reporter** | Write the final memo from approved claims only, with confidence levels and DQ findings | Nemotron 3 Ultra |

Tools the agents can call: `run_sql`, `list_tables`, `describe_table`, `get_dq_results`, `world_model.query`, `hmem.retrieve`.

---

## 5. Meterless engines (required: at least one, working, with a test)

We implement two of them, following the Meterless agent-engine specs:

**World Model** (core): the shared, grounded state every agent reasons over.
- `WorldStoreService`: entities (company, sector, metric, period, claim), relationships, provenance (the query ID behind each fact)
- `IngestPipelineService`: idempotent; re-running a crashed investigation doesn't duplicate facts
- `ContextBuilderService`: builds each agent's context block from the world state
- **Test:** ingest the same query result twice → one fact; context for agent X contains the expected entities

**H-MEM** (the demo moment): memory across investigations.
- `MemoryStoreService` + `MemoryRetrievalService`, with tiers and decay
- After each run, store "in sector S, driver D explained the move" with an outcome score
- At the next run in the same sector, the Planner retrieves it and investigates that driver first
- **Test:** run A stores a memory; run B in the same sector retrieves it and reorders its plan

*(Markovian is optional; we only add it if long investigations hit context limits.)*

All engines write JSONL (audit, traces, provenance) to `runs/<run_id>/logs/`.

---

## 6. Models — Meterless Nightshift + NVIDIA Nemotron

- **Nightshift** is the app's only AI provider: a local OpenAI-compatible gateway (`/v1/chat/completions`) that we configure with an NVIDIA key.
- **Nemotron models**, chosen from what the Discord confirms is working:
  - `nvidia/nemotron-3.5-lightning-30b-a3b` (fast: Planner, Investigator)
  - `nvidia/nemotron-3-ultra-550b-a55b` (strong: Critic, Reporter)
- Log the serving model for **every call** (Nightshift returns a receipt) → `logs/model_calls.jsonl`. The NVIDIA prize requires this.

**Verified on our machine (27 Oct):**
- Nightshift Runtime at `127.0.0.1:8088/v1` answers with our token; `GET /models` lists both Nemotron models.
- Pinning `model` to a Nemotron ID routes exactly there (single `final_synthesis` phase, no race): Lightning ~5s, Ultra <1s on a tiny prompt. `meterless/auto` picked Bolt E4B instead, so **always pin**.
- Every response carries `meterless.routed_model` plus a `meterless.compute_receipt.v1` receipt (id, latency, tokens): log it per call as NVIDIA award evidence.
- Ultra can leak a reasoning tail ending in `</think>`; the generator strips everything up to the last `</think>`.
- Both models reason before answering: realistic calls take 7–60 s, growing with the Investigator's transcript. `detailed thinking off` in the system prompt helps. Ultra writes better SQL than Lightning at similar speed, so roles are Planner = Lightning; Investigator, Critic, Reporter = Ultra.
- **Nightshift's exact-match cache ignores the system prompt** (verified: an edited system prompt still got the old cached plan in 19 ms). No cache-control header or body flag disables it. The generator tags every user message with the run id and a system-prompt fingerprint, and logs `from_cache` / `cache_status` per call, so no replayed answer can pass as live agent work.
- Ultra intermittently returns HTTP 504 "no first token within the cloud first-token timeout"; retried with backoff (3 in a row seen, all recovered).
- First end-to-end runs (27 Oct): about 5–13.5 min, 5 queries, ~10 model calls. Latency is the main Step 4 concern (shorter Investigator transcripts, fewer turns).

**Known issues from the Discord, with our mitigations:**
- Nightshift calls take 30–75s because of planning and racing several models → keep the number of calls low, cap rounds, try pinning the lane/model, show progress live in the UI.
- Free-tier HTTP 429 → retry with backoff.
- NVIDIA key verification can fail → get the key **first** (step 0).
- Super 120B / Super VL are unreliable → don't use them.

---

## 7. Product surface

A small web app:
- **Ask:** company + metric + period (and a free-text question)
- **Live timeline:** each agent step, SQL run, critic verdict, and model used, streamed as it happens
- **Memo:** the final explanation; clicking any number shows its SQL, result rows, and critic verdict
- **Data quality panel:** DQ rule results the run tripped over

Plus a CLI (`causeway ask "..."`) for judges and tests.

**Sample mode:** `causeway --sample` replays recorded real runs (clearly labelled as replays), so judges can see it work without Zetaris, Nightshift, or keys.

---

## 8. Tech stack

- **Python 3.12**, FastAPI backend, server-sent events for the live timeline
- Frontend: one plain HTML/JS page (no build step)
- Zetaris via its SQL connection (JDBC/HTTP, whichever the getting-started repo uses)
- OpenAI-compatible client → Nightshift
- pytest for tests
- **Built in Cursor**, so the submission can say how Cursor was used

---

## 9. Repository layout (planned)

```
zetaris/
├── plan.md
├── README.md                  # setup, usage, sponsor tech, pre-existing components
├── .env.example               # no real keys, ever
├── zetaris_sql/
│   ├── sources/               # CREATE statements per source
│   ├── usl/                   # USL DDL + activation queries
│   ├── dq/                    # data-quality rules
│   └── exploratory/           # 2–3 queries per source
├── causeway/
│   ├── generator.py           # the single injected model generator → Nightshift
│   ├── zetaris_client.py
│   ├── agents/                # planner, investigator, critic, reporter
│   ├── engines/
│   │   ├── world_model/
│   │   └── hmem/
│   ├── orchestrator.py        # the loop, round caps, run IDs
│   ├── evidence.py            # JSONL logging
│   └── web/                   # FastAPI app + static page
├── samples/                   # recorded runs for sample mode
├── tests/
├── runs/                      # gitignored run outputs (one committed example)
└── docs/
    ├── architecture.md        # + diagram
    ├── sponsor-tech.md        # Zetaris / Meterless / NVIDIA / Cursor explanation
    └── zetaris-reflection.md  # the USL reflection docs Zetaris scores
```

---

## 10. Build order

**Step 0 — Accounts and setup (blocking, do first)**
- [ ] NVIDIA API key from build.nvidia.com
- [ ] Install Meterless Nightshift (Mac M-series edition), add the NVIDIA key, confirm Lightning and Ultra answer
- [ ] Start Docker; install Zetaris locally (or sign up for Zetaris Cloud Gateway); run the SQL connectivity check
- [ ] Create the public GitHub repo; add `.gitignore` and `.env.example`

**Step 1 — Data layer**
- [ ] Register EDGAR company facts + profiles + SIC (existing recipes)
- [ ] Register Frames and BLS PPI as custom REST sources; fall back to Eurostat if BLS fights us
- [ ] Exploratory queries (2–3 per source)
- [ ] Build the USL, add the DQ rules, confirm they execute

**Step 2 — Skeleton end to end**
- [ ] Generator → Nightshift, logging the model per call
- [ ] Zetaris client + `run_sql` tool
- [ ] Planner → Investigator → Reporter on one real company (no critic yet)

**Step 3 — The parts that make it good**
- [ ] Critic with deterministic recompute + model check, plus the reject loop
- [ ] World Model engine + tests
- [ ] H-MEM engine + tests; demo the second-company memory effect
- [ ] JSONL evidence for every step

**Step 4 — Product**
- [ ] Web page: ask box, live timeline, memo with evidence drill-down, DQ panel
- [ ] Sample mode from recorded runs

**Step 5 — Package and submit**
- [ ] README (setup, usage, dependencies, which engines and where, which Nemotron models and where)
- [ ] At least 3 sample inputs/outputs
- [ ] Architecture diagram
- [ ] Zetaris reflection docs (time saved vs. hand-coding; would the USL help; "PM feature request")
- [ ] Slide deck
- [ ] Demo video, 1–4 minutes
- [ ] Test from a clean clone; confirm no secrets committed
- [ ] Submit on HackOS; declare pre-existing components (none planned)

---

## 11. Demo script (draft, about 3 minutes)

1. **The problem** (20s): an analyst's real question, and why it takes hours today.
2. **Ask** (20s): "Why did <Company>'s gross margin fall in FY2025?"
3. **Watch it work** (60s): the Planner picks peers from SIC; the Investigator queries Zetaris; **the Critic rejects a claim** because a recomputed number doesn't match, and the Investigator fixes it.
4. **The memo** (30s): company vs. sector split; click a number to see its SQL and result rows; a DQ rule flagged a filing.
5. **Memory** (30s): ask about a second company in the same sector; H-MEM brings the driver that mattered last time, and the plan changes.
6. **Under the hood** (20s): Zetaris USL, Meterless engines, Nemotron Lightning and Ultra via Nightshift, with the per-call model log.

---

## 12. Risks and fallbacks

| Risk | Fallback |
|---|---|
| NVIDIA key won't verify | Nightshift with another provider that serves Nemotron (others in the Discord used Nebius); ask Matt Cool (NVIDIA) in Help NVIDIA Tools |
| Nightshift too slow for a live demo | Pin lanes, fewer calls, record the demo from a real run, offer sample mode for judges |
| Zetaris local install too heavy (24 GB RAM) | Zetaris Cloud Gateway |
| Frames or BLS won't register as REST sources | Peer set from per-company Company Facts calls; macro from Eurostat/StatCan (verified recipes) |
| A company's XBRL tags are inconsistent | Pick demo companies with clean, standard tags; the DQ rules surface the rest |

---

## 13. Open questions

- Which demo companies and sector? We want a sector with a clear margin story and clean XBRL tags (for example consumer goods or airlines in a cost-shock year). To pick once the data is live.
- Zetaris locally (Docker) or Cloud Gateway? Decide at Step 0, after the connectivity check.
- Final product name.
