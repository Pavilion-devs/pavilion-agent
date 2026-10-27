# Pavilion — From an Analyst's Question to a Memo Where Every Number Has a Source

> Pavilion explains why a public company's numbers moved, and whether it was the company or the whole sector. Its agents query a Zetaris semantic layer over live SEC data, a separate Critic re-runs every query behind every claim, and only findings that survive reach the analyst's memo.

Built for the Open Agent Hackathon 2026, Track 2: *The Agent That Can Explain Why*.

* * *
## Live
  * Dashboard: run locally with `python3 -m http.server 8765 --directory web`, then open `http://localhost:8765`. It replays two recorded runs, so no services are needed.
  * 🎥 Demo video: [YouTube link to be added]
  * 📖 Setup guide: [`SETUP.md`](SETUP.md)
  * 🗺️ Architecture: see [Architecture](#architecture) below

* * *
## Table of Contents

  * [Quick Path](#quick-path)
  * [Why It Stands Out](#why-it-stands-out)
  * [What it does](#what-it-does)
  * [Architecture](#architecture)
  * [Requirements](#requirements)
  * [Setup](#setup)
  * [Usage](#usage)
  * [Pavilion at the evidence boundary](#pavilion-at-the-evidence-boundary)
    * [From a question to a plan](#from-a-question-to-a-plan)
    * [One Zetaris semantic layer](#one-zetaris-semantic-layer)
    * [The Critic gate](#the-critic-gate)
    * [Validation and safety boundaries](#validation-and-safety-boundaries)
  * [Project layout](#project-layout)
  * [AI assistance](#ai-assistance)
  * [License](#license)

* * *
## Quick Path

If you want the shortest path through the project:

```bash
python3 -m http.server 8765 --directory web
```

Then open `http://localhost:8765`. The Pavilion dashboard replays the recorded Target and Best Buy investigations from their real logs: press **Replay run**, open a finding to see the Critic's review, or click any query id to see the SQL Zetaris ran and the rows it returned.

To run a live investigation against Zetaris and Nemotron, follow [Setup](#setup).

## Why It Stands Out

Pavilion takes on a question analysts ask every reporting season, and makes the answer auditable:

  * It takes a practical input from a credit or equity analyst: *"Why did Target's gross margin fall in fiscal 2022? Was it Target or the whole sector?"*
  * It answers from live public data: SEC EDGAR filings, sector codes and producer price indexes, unified in one Zetaris semantic layer.
  * No agent may state a number it didn't query. A separate **Critic** re-executes every cited query in Zetaris, requires identical rows, checks every quoted number against those rows, and reviews the method with Nemotron Ultra.
  * It learns from its own Critic: rejections become H-MEM lessons that the next investigation recalls before it plans.

On the recorded Target run, the Critic rejected 4 of 5 findings in round 1, including a peer median that quietly dropped Dollar Tree because it didn't fit. All five were approved by round 3. The memo's answer: Target fell **−4.64** points against a peer median of **−0.74**, so **−3.90** points were Target's own.

## What it does

  * Analyst question: one sentence naming a company, a metric and a period.
  * Planning: Nemotron Lightning picks the peer set, documents any exclusion with a reason, and writes 4–6 hypotheses covering both sector-wide and company-specific drivers.
  * Investigation: Nemotron Ultra writes read-only SQL against the Zetaris semantic layer, one query per turn, and cites the query ids behind every finding.
  * Verification: the Critic re-runs each cited query, grounds each number in the World Model's facts, and reviews the method against a checklist. Rejected findings go back with the specific fix, for up to 3 rounds.
  * Memo: the Reporter writes only approved findings, with a query id on every number. Unverified claims appear only under Open questions.
  * Memory: H-MEM mines lessons from the Critic's rejections and approvals, and recalls them before the next plan.

## Architecture

![Pavilion architecture: inputs, the Zetaris data layer, Nemotron agents via Meterless Nightshift, the World Model and H-MEM engines, and outputs](docs/architecture/pavilion-architecture.png)

The analyst's question goes to the Planner. The Investigator queries the Zetaris semantic layer over JDBC, every result is ingested into the World Model as facts with provenance, and the Critic re-runs and grounds each claim before the Reporter writes the memo. The editable diagram is [`docs/architecture/pavilion.excalidraw`](docs/architecture/pavilion.excalidraw).

```text
Analyst question
  → H-MEM recalls lessons from earlier investigations
  → Planner (Nemotron Lightning) sets peers, exclusions and hypotheses
  → Investigator (Nemotron Ultra) writes read-only SQL on the Zetaris semantic layer
  → World Model ingests every result as facts with provenance
  → Critic re-runs cited queries, grounds every number, reviews the method
  → rejected findings return with the fix (up to 3 rounds)
  → Reporter writes the memo from approved findings only
  → H-MEM learns from the verdicts
```

Every model call goes through Meterless Nightshift and is logged with its compute receipt and the model that served it. Credentials stay in `.env` and in Nightshift's Providers; the agents only ever see query results.

## Requirements

  * Python 3.11+
  * Java 17 (for the Zetaris JDBC driver)
  * Docker, with the Zetaris Lightning bundle running locally
  * The official Zetaris JDBC driver (`ndp-jdbc-driver-2.4.3.1`)
  * Meterless Nightshift, with an NVIDIA API key in its Providers
  * A contact email for the SEC user agent, as SEC EDGAR requires

## Setup

### 1. Install dependencies

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Configure the environment

Create the ignored local environment file:

```bash
cp .env.example .env
```

Set the values locally. Do not put secrets in chat, source files or commits.

```dotenv
METERLESS_BASE_URL=http://127.0.0.1:8088/v1
METERLESS_RUNTIME_TOKEN=your_nightshift_runtime_token
ZETARIS_URL=http://localhost:3000
ZETARIS_USER=your_zetaris_user
ZETARIS_PASSWORD=your_zetaris_password
SEC_USER_AGENT="Pavilion your_contact_email"
ZETARIS_JDBC_JAR=/absolute/path/to/ndp-jdbc-driver-2.4.3.1-driver.jar
ZETARIS_JDBC_URL=jdbc:zetaris:lightning@localhost:10000
JAVA_HOME=/path/to/java17
```

The NVIDIA key goes in Nightshift's Providers, not in this file. [`SETUP.md`](SETUP.md) walks through Zetaris, Nightshift and NVIDIA step by step.

### 3. Build the semantic layer

Generate the SEC source tables, fetch the price indexes, then create the sources, the USL and the data-quality rules in Zetaris:

```bash
.venv/bin/python scripts/gen_edgar_sql.py
.venv/bin/python scripts/fetch_ppi.py
for f in zetaris_sql/sources/*.sql zetaris_sql/usl/*.sql zetaris_sql/dq/01_dq_rules.sql; do .venv/bin/python scripts/run_sql.py "$f" --skip-exists; done
```

### 4. Check and run

```bash
.venv/bin/python -m pytest -q          # 27 tests, no services needed
.venv/bin/python -m causeway status    # Nightshift and Zetaris reachable?
```

## Usage

1. Ask a question:

   ```bash
   .venv/bin/python -m causeway ask "Why did Target's gross margin fall in fiscal 2022? Was it Target or the whole sector?"
   ```

2. Follow the live timeline in the terminal: the plan, each Zetaris query, the Critic's recompute and verdicts, and each model call with the Nemotron model that served it.
3. Read the result in `runs/<run_id>/`: `memo.md`, `plan.json`, `rounds.json` (every Critic verdict), `memory.json` (lessons recalled and learned) and `logs/` (steps, queries with rows, model calls with receipts).
4. Load the run into the dashboard:

   ```bash
   .venv/bin/python scripts/build_replay.py runs/<run_id>
   python3 -m http.server 8765 --directory web
   ```

5. Ask a second question in the same sector, for example Best Buy, to see H-MEM recall the first run's lessons.

A full run takes 50–60 minutes, most of it Nemotron Ultra calls that hang until the timeout; model working time is about 15 minutes. The two recorded runs are in [`runs/example/`](runs/example/).

## Pavilion at the evidence boundary

Pavilion sits between an analyst's question and the numbers that answer it:

```text
Analyst
  → Pavilion planner and investigator
  → Zetaris semantic layer over live SEC and BLS data
  → Critic re-execution and number grounding
  → memo with a query id on every number
```

The agents propose; the Critic decides. No LLM decides whether a number is real: that is checked deterministically against rows Zetaris returned.

### From a question to a plan

The Planner turns the question into a company, a period, a peer set and hypotheses on both sides: sector-wide (did peers move too? did industry price indexes?) and company-specific (did cost of sales outgrow revenue? did operating leverage turn?). A peer can be excluded only with a documented reason. Kroger is excluded because its reported cost of sales leaves out depreciation, so it isn't comparable.

### One Zetaris semantic layer

A Unified Semantic Layer (`causeway.retail_usl`) spans three sources: SEC EDGAR XBRL and submissions (33 live REST tables), the company_dns SIC hierarchy, and BLS producer price indexes. Its tables are `sector_class`, `company`, `financial_fact`, `annual_financials` and `price_index`. The `edgar.margin_change` view computes every year-over-year change from a single 10-K, because Dollar Tree's restated cost of sales would otherwise fake an 8-point jump. Six `REGISTER DQ` rules run with `RUN DQ` and found 0 invalid records. Platform findings are in [`docs/zetaris-findings.md`](docs/zetaris-findings.md).

### The Critic gate

A finding is approved only when both passes approve it:

  * Deterministic: every cited query is re-executed in Zetaris and must return identical rows. Every number must be a World Model fact from those rows, its percentage, a same-measure difference, or a peer median. Rounding 4.64 to 4 fails.
  * Methodology: Nemotron Ultra reviews each finding against a checklist covering peer cherry-picking, restatements, price-index units, year-end versus intra-year claims, and causal wording.

Rejections return to the Investigator with the specific fix. H-MEM records them as lessons (confidence 0.9) and approved findings as patterns (0.75), retrieved with a hybrid score and a 0.35 threshold.

### Validation and safety boundaries

  * SQL is read-only by construction: only `SELECT` and `WITH` are accepted, and DDL and DML are refused before they reach Zetaris.
  * A finding whose methodology review fails or times out is never approved.
  * Unapproved findings never appear as conclusions; the memo lists them under Open questions.
  * Nightshift's exact-match cache is keyed per run, so a stale answer can't be replayed as live agent work.
  * Hung models fall back between Ultra and Lightning, and transient Nightshift states (`cloud_busy`, DNS drops, engine restarts) are waited out instead of failing the run.
  * Keys live in `.env` (gitignored) and in Nightshift. The SEC contact email is injected at run time and never committed.
  * This is historical analysis of public filings, not investment advice.

## Project layout

```text
causeway/
  orchestrator.py          # recall → plan → investigate ⇄ critic → report → learn
  agents.py                # Planner, Investigator, Reporter prompts and JSON contracts
  critic.py                # recompute, number grounding, methodology review
  generator.py             # Meterless Nightshift provider, receipts, fallbacks
  zetaris_client.py        # Zetaris JDBC client
  tools.py                 # read-only SQL tool and schema guide
  engines/
    world_model.py         # stable IDs, append-only log, ingest, read surface
    hmem.py                # tiers, hybrid retrieval, trust ledger, mining
zetaris_sql/
  sources/                 # EDGAR, SIC and PPI source tables, margin_change view
  usl/                     # Unified Semantic Layer DDL
  dq/                      # DQ rules and cross-row checks
scripts/                   # SQL runner, EDGAR generator, PPI fetch, replay tools
tests/                     # 27 tests: engines, Critic grounding, generator resilience
web/                       # Pavilion dashboard and the recorded runs it replays
runs/example/              # full logs of the Target and Best Buy runs
docs/
  architecture/            # Pavilion architecture (PNG, SVG, .excalidraw)
  zetaris-findings.md      # what we learned about the platform
SETUP.md                   # Zetaris, Nightshift and NVIDIA setup
```

## AI assistance

NVIDIA Nemotron is called at runtime through Meterless Nightshift: Nemotron 3.5 Lightning 30B for planning, and Nemotron 3 Ultra 550B for investigation, critique, reporting and memory mining. Claude Code assisted with implementation, testing, the dashboard, the architecture diagram and this documentation.

## License

No license has been selected and this repository has no `LICENSE` file. Permission to reuse the code has not been specified.
