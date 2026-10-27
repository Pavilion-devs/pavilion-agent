"""Tools the agents use against Zetaris, plus the schema guide they reason from.

Every query goes through `SqlTool.run`, which enforces read-only SQL, logs the query
(id, SQL, purpose, rows, timing) to the run's queries.jsonl and keeps the full result so the
Critic can re-execute and compare it later.
"""

from __future__ import annotations

import re

from .evidence import Evidence
from .zetaris_client import QueryResult, ZetarisClient

USL = "lightning.metastore.causeway.retail_usl"

SCHEMA_GUIDE = f"""You query a Zetaris Unified Semantic Layer (Spark SQL dialect). Always use the full table names below.

{USL}.company - one row per company in the universe
  ticker, cik, entity_name, sic_code, sic_description, fiscal_year_end_mmdd, comparability_note
{USL}.sector_class - SIC hierarchy
  sic_code, sic_description, industry_group, industry_group_desc, major_group, major_group_desc, sector, sector_desc
{USL}.annual_financials - one row per company fiscal year (10-K, as originally reported), amounts in USD
  row_id, ticker, fiscal_year_end ('YYYY-MM-DD' string), revenue, cost_of_sales, gross_profit,
  gross_margin (0-1 ratio), operating_income, operating_margin (0-1), inventory, revenue_filing (SEC accession)
  Use for LEVELS (inventory, revenue size). Do NOT subtract one row from another to get a change:
  companies restate history (e.g. Dollar Tree after selling Family Dollar), so rows can sit on different bases.
edgar.margin_change - USE THIS FOR YEAR-OVER-YEAR CHANGES. Both years come from the same 10-K (same basis).
  ticker, fiscal_year_end, prior_fiscal_year_end, revenue, prior_revenue, revenue_growth (ratio),
  gross_margin, prior_gross_margin, gross_margin_change (ratio; x100 = percentage points),
  operating_margin, prior_operating_margin, operating_margin_change, basis_filing, filed_date
{USL}.financial_fact - every reported XBRL fact (quarterly and annual), for drill-down
  ticker, metric ('revenue','cost_of_sales','operating_income','inventory'), xbrl_tag, period_start, period_end,
  value_usd, fiscal_year, fiscal_period ('FY','Q1'..), form_type ('10-K','10-Q'), filed_date, accession_number
{USL}.price_index - monthly BLS producer price indexes
  series_id, series_name, description, story_role, observation_date ('YYYY-MM-DD' string), index_value
  series_name values: dept_store_margins, grocery_margins, electronics_margins (retail-margin PPIs: higher = retailers
  earning more margin per unit sold), truckload_freight, warehousing (cost pressures), core_final_demand (broad inflation)

Universe: TGT Target, WMT Walmart, COST Costco, KR Kroger, DG Dollar General, DLTR Dollar Tree, BBY Best Buy.
Fiscal years end on different dates: most retailers end late Jan / early Feb (a year ending 2023-01-28 is "fiscal 2022"),
Costco ends late Aug / early Sep. Compare a company's year with peers' years ending within ~7 months of it.
Kroger's cost_of_sales excludes depreciation and amortization, so its gross margin is not directly comparable.

SQL rules: SELECT or WITH only, one statement, no semicolon. Dates are strings: use to_date(...) for date maths,
or string comparison / substr for filtering. Round ratios with ROUND(x * 100, 2) for percentage points.
Window functions are allowed. Keep result sets small (aggregate, filter, LIMIT 200)."""

_READ_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|create|alter|cache|uncache|register|activate|compile|remove|grant)\b", re.IGNORECASE)


class SqlTool:
    def __init__(self, client: ZetarisClient, evidence: Evidence) -> None:
        self.client = client
        self.evidence = evidence
        self.results: dict[str, QueryResult] = {}

    def run(self, sql: str, purpose: str = "", agent: str = "", max_rows: int = 200) -> QueryResult:
        sql = sql.strip().rstrip(";").strip()
        if not _READ_ONLY.match(sql) or _FORBIDDEN.search(_strip_strings(sql)):
            result = QueryResult("rejected", sql, [], [], 0, error="Only read-only SELECT/WITH queries are allowed.")
        else:
            result = self.client.run_sql(sql, max_rows=max_rows)
        self.results[result.query_id] = result
        self.evidence.log("queries", {
            "query_id": result.query_id,
            "agent": agent,
            "purpose": purpose,
            "sql": sql,
            "columns": result.columns,
            "rows": result.rows,
            "row_count": len(result.rows),
            "elapsed_ms": result.elapsed_ms,
            "error": result.error,
        })
        return result


def _strip_strings(sql: str) -> str:
    """Remove quoted literals so a value like 'drop' inside a string isn't mistaken for a keyword."""
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def format_result(result: QueryResult, limit: int = 40) -> str:
    """Compact text table for an LLM to read."""
    if result.error:
        return f"[{result.query_id}] ERROR: {result.error}"
    lines = [f"[{result.query_id}] {len(result.rows)} rows ({result.elapsed_ms} ms)", " | ".join(result.columns)]
    for row in result.rows[:limit]:
        lines.append(" | ".join(_fmt(v) for v in row))
    if len(result.rows) > limit:
        lines.append(f"... {len(result.rows) - limit} more rows")
    return "\n".join(lines)


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.4f}" if abs(value) < 10 else f"{value:,.0f}"
    return str(value)
