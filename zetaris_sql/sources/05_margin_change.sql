-- Year-over-year margin changes measured within a single 10-K.
-- Each 10-K reports the current and prior fiscal year on the same basis, so a change computed inside one
-- filing never mixes restated and unrestated years (Dollar Tree's Family Dollar divestiture restated its
-- history; comparing values from different filings manufactured +8 and +5 pt "jumps").
-- One row per company fiscal year, taken from the filing that first reported that year.

CREATE SCHEMASTORE VIEW margin_change WITH CONTAINER edgar AS
WITH by_filing AS (
    SELECT ticker, accession_number, filed_date, period_end,
           MAX(CASE WHEN metric = 'revenue' THEN value_usd END) AS revenue,
           MAX(CASE WHEN metric = 'cost_of_sales' THEN value_usd END) AS cost_of_sales,
           MAX(CASE WHEN metric = 'operating_income' THEN value_usd END) AS operating_income
    FROM edgar.financial_fact
    WHERE form_type = '10-K'
      AND metric IN ('revenue', 'cost_of_sales', 'operating_income')
      AND datediff(to_date(period_end), to_date(period_start)) BETWEEN 350 AND 380
    GROUP BY ticker, accession_number, filed_date, period_end
),
margins AS (
    SELECT ticker, accession_number, filed_date, period_end, revenue,
           (revenue - cost_of_sales) / revenue AS gross_margin,
           operating_income / revenue AS operating_margin,
           LAG(period_end) OVER (PARTITION BY ticker, accession_number ORDER BY period_end) AS prior_period_end,
           LAG(revenue) OVER (PARTITION BY ticker, accession_number ORDER BY period_end) AS prior_revenue,
           LAG((revenue - cost_of_sales) / revenue) OVER (PARTITION BY ticker, accession_number ORDER BY period_end) AS prior_gross_margin,
           LAG(operating_income / revenue) OVER (PARTITION BY ticker, accession_number ORDER BY period_end) AS prior_operating_margin,
           MAX(period_end) OVER (PARTITION BY ticker, accession_number) AS filing_current_period
    FROM by_filing
    WHERE revenue IS NOT NULL AND cost_of_sales IS NOT NULL
),
current_year AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker, period_end ORDER BY filed_date ASC) AS rn
    FROM margins
    WHERE period_end = filing_current_period AND prior_gross_margin IS NOT NULL
)
SELECT ticker,
       period_end AS fiscal_year_end,
       prior_period_end AS prior_fiscal_year_end,
       revenue, prior_revenue, revenue / prior_revenue - 1 AS revenue_growth,
       gross_margin, prior_gross_margin, gross_margin - prior_gross_margin AS gross_margin_change,
       operating_margin, prior_operating_margin, operating_margin - prior_operating_margin AS operating_margin_change,
       accession_number AS basis_filing, filed_date
FROM current_year
WHERE rn = 1;
