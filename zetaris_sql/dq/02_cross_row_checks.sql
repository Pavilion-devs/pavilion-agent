-- Cross-row data-quality checks. Zetaris DQ rules are row filters (no window functions),
-- so year-over-year checks run as plain queries over the USL. The agents call these as tools.

-- 1. Gross-margin jumps larger than 3 points year over year, with the peer median move in the
--    same window. A company jump far from its peers is either a real story or a definition change;
--    either way it needs an explanation before it's trusted.
--    Changes come from edgar.margin_change (both years from one filing, so restatements can't fake a jump).
WITH yoy AS (
    SELECT ticker, fiscal_year_end, gross_margin_change AS gm_change,
           YEAR(to_date(fiscal_year_end)) - CASE WHEN MONTH(to_date(fiscal_year_end)) <= 3 THEN 1 ELSE 0 END AS fiscal_label
    FROM edgar.margin_change
)
SELECT y.ticker, y.fiscal_year_end, ROUND(y.gm_change * 100, 2) AS gm_change_pts,
       ROUND(percentile_approx(p.gm_change, 0.5) * 100, 2) AS peer_median_change_pts
FROM yoy y
JOIN yoy p ON p.fiscal_label = y.fiscal_label AND p.ticker <> y.ticker
WHERE ABS(y.gm_change) > 0.03
GROUP BY y.ticker, y.fiscal_year_end, y.gm_change
ORDER BY y.fiscal_year_end;

-- 3. Restatements: the same fiscal-year figure reported differently by a later 10-K.
--    annual_financials uses as-originally-reported values; this shows where later filings disagree.
SELECT ticker, metric, period_end,
       ROUND(MIN(value_usd) / 1e9, 2) AS low_bn, ROUND(MAX(value_usd) / 1e9, 2) AS high_bn,
       COUNT(DISTINCT accession_number) AS filings
FROM lightning.metastore.causeway.retail_usl.financial_fact
WHERE form_type = '10-K' AND metric IN ('revenue', 'cost_of_sales')
  AND datediff(to_date(period_end), to_date(period_start)) BETWEEN 350 AND 380
GROUP BY ticker, metric, period_end
HAVING MAX(value_usd) > MIN(value_usd) * 1.01
ORDER BY ticker, period_end, metric;

-- 2. Tag continuity: a company's chosen XBRL tag should cover every 10-K year in the window.
--    Gaps mean the company switched tags and the canonical metric needs a second mapping.
SELECT ticker, metric, MIN(fiscal_year) AS first_fy, MAX(fiscal_year) AS last_fy, COUNT(DISTINCT fiscal_year) AS years
FROM lightning.metastore.causeway.retail_usl.financial_fact
WHERE form_type = '10-K' AND fiscal_period = 'FY'
GROUP BY ticker, metric
ORDER BY ticker, metric;
