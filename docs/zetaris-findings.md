# Zetaris: what we built and what we learned

Raw material for the Zetaris reflection write-ups (scoring part 2). Zetaris 2.4.3.1, local Docker install on an Apple M4 Pro, 27 Oct 2026.

## What's onboarded

| # | Source | Type | Objects | Zetaris view |
|---|---|---|---|---|
| 1 | SEC EDGAR companyconcept | REST | 26 cached tables (7 retailers × up to 4 metrics) | `edgar.financial_fact` |
| 2 | SEC EDGAR submissions | REST | 7 cached tables | `edgar.company` |
| 3 | company_dns SIC hierarchy (V4) | REST | 1 cached table | `company_dns.sic_hierarchy` |
| 4 | BLS producer price indexes (via FRED CSV) | File | 1 cached CSV table, 6 series | `macro.ppi` |

All 26 EDGAR tables were verified row-for-row against direct SEC downloads: 0 mismatches.

USL `lightning.metastore.causeway.retail_usl`: `sector_class`, `company` (FK → `sector_class`), `financial_fact`, `annual_financials` (derived), `price_index`.

Data quality: PK/FK constraint rules plus 6 custom rules; 6,794 records checked, 0 invalid. Cross-row checks (plain queries) flag the two year-over-year margin anomalies the agent investigates: Target FY2022 (−4.71 pts vs peer median −0.74) and Dollar Tree FY2022 (+8.12 pts vs −0.96).

## Findings (things that would have cost us hours)

1. **Every company names the same figure differently.** Cost of sales is `CostOfRevenue` (Walmart), `CostOfGoodsAndServicesSold` (Target, Costco, Dollar General, Dollar Tree, Best Buy), and `CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization` (Kroger, which isn't comparable because it excludes D&A). Only 3 of 7 report `GrossProfit`. The USL's canonical `metric` column is the reconciliation layer.
2. **A USL table can be activated from a SchemaStore view.** The getting-started repo lists this as untested; it works, and keeps the activation queries short.
3. **ACTIVATE enforces NOT NULL, and its error message writes the fix.** Every NOT NULL column needed `COALESCE(...)`. The error suggests the exact rewritten SELECT, which is excellent.
4. **ACTIVATE does not enforce numeric widths.** `fiscal_year` declared `int` activated fine from a BIGINT source, then reads failed with `java.lang.Long is not a valid external type for schema of int`. Fix: explicit `CAST`. *Feature request:* check type compatibility at activation, as is done for nullability.
5. **DQ rules are row filters.** No window functions, so year-over-year checks (the most valuable ones for financial data) must be plain queries. *Feature request:* a DQ rule type over an ordered partition (`PARTITION BY ticker ORDER BY period`) would let "margin moved > 3 pts vs peers" live inside the USL.
6. **A multi-table COMPILE must be sent as one statement.** Semicolon-splitting runners break it; our runner uses explicit `-- >>>` statement markers.
7. **REST tables re-fetch on every uncached query.** `CACHE TABLE` straight after `CREATE` made re-queries take about a second; SEC's 10 requests/second limit was never hit.
8. **REST tables can't send a JSON POST body**, so the BLS API's date-range request isn't possible (its GET returns only 3 years). We fetch the same BLS series from FRED's CSV and register them as a FILESTORE table from the shared data volume (`file:///home/zetaris/customer-data/shared/...`).
9. **Plain `SHOW DATABASES` / `SHOW TABLES` are disabled**; use the Lightning `SHOW ... IN ...` forms.
10. **Over JDBC, errors arrive as full Java stack traces.** Our client keeps only the `<<<…>>>` message, which is what an agent needs to fix its own SQL.

11. **Restatements fake year-over-year moves.** Dollar Tree's 2025 10-K restated fiscal 2022 to exclude Family Dollar (revenue $28.32B → $15.41B), while fiscal 2021 was never restated. Taking the latest-filed value per year produced a +8.12 pt "margin jump"; taking first-filed values just moved the artefact to fiscal 2024 (+5.32). The fix: `edgar.margin_change` computes each change inside a single 10-K (current vs prior year, same basis). Real Dollar Tree fiscal 2022 change: +2.14 pts. A restatement check (`dq/02_cross_row_checks.sql` #3) lists every period that later filings report differently. *Feature request:* a USL-level "as-of filing" dimension, so basis-consistent comparisons don't need hand-written window SQL.

## Performance notes

- `RUN DQ` on the derived tables takes about 55–60 s, because they recompute from 26 REST tables through views. Materializing `annual_financials` is the next step if agent latency needs it.
- A typical agent query over `annual_financials` joined to `company` and `sector_class` takes about 8–10 s.
