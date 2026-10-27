-- Causeway Unified Semantic Layer: one business model over four onboarded sources.
--   sector_class        <- company_dns SIC hierarchy (REST)
--   company             <- SEC EDGAR submissions (REST, 7 tables)        FK sic_code -> sector_class
--   financial_fact      <- SEC EDGAR companyconcept (REST, 26 tables)    FK ticker   -> company
--   annual_financials   <- derived from financial_fact: one row per company fiscal year, as originally reported
--   price_index         <- BLS producer price indexes (CSV file)
-- Statements are separated by `-- >>>` so the multi-table COMPILE reaches Zetaris as one statement
-- (run with scripts/run_sql.py).

CREATE NAMESPACE IF NOT EXISTS lightning.metastore
-- >>>
CREATE NAMESPACE IF NOT EXISTS lightning.metastore.causeway
-- >>>
COMPILE USL IF NOT EXISTS retail_usl DEPLOY NAMESPACE lightning.metastore.causeway DDL
CREATE TABLE sector_class (
    sic_code varchar(4) NOT NULL PRIMARY KEY,
    sic_description varchar(200),
    industry_group varchar(3),
    industry_group_desc varchar(200),
    major_group varchar(2),
    major_group_desc varchar(200),
    sector varchar(1),
    sector_desc varchar(200)
);
CREATE TABLE company (
    ticker varchar(8) NOT NULL PRIMARY KEY,
    cik varchar(10) NOT NULL,
    entity_name varchar(200) NOT NULL,
    sic_code varchar(4) NOT NULL FOREIGN KEY REFERENCES sector_class(sic_code),
    sic_description varchar(200),
    fiscal_year_end_mmdd varchar(4),
    comparability_note varchar(400)
);
CREATE TABLE financial_fact (
    fact_id varchar(300) NOT NULL PRIMARY KEY,
    ticker varchar(8) NOT NULL FOREIGN KEY REFERENCES company(ticker),
    metric varchar(40) NOT NULL,
    xbrl_tag varchar(200),
    period_start varchar(10),
    period_end varchar(10) NOT NULL,
    value_usd double,
    fiscal_year int,
    fiscal_period varchar(4),
    form_type varchar(12),
    filed_date varchar(10),
    accession_number varchar(30),
    period_frame varchar(12)
);
CREATE TABLE annual_financials (
    row_id varchar(30) NOT NULL PRIMARY KEY,
    ticker varchar(8) NOT NULL FOREIGN KEY REFERENCES company(ticker),
    fiscal_year_end varchar(10) NOT NULL,
    revenue double,
    cost_of_sales double,
    gross_profit double,
    gross_margin double,
    operating_income double,
    operating_margin double,
    inventory double,
    revenue_filing varchar(30)
);
CREATE TABLE price_index (
    row_id varchar(40) NOT NULL PRIMARY KEY,
    series_id varchar(20) NOT NULL,
    series_name varchar(40) NOT NULL,
    description varchar(200),
    story_role varchar(100),
    observation_date varchar(10) NOT NULL,
    index_value double
);
-- >>>
UPDATE USL lightning.metastore.causeway.retail_usl SET DESCRIPTION 'Causeway retail model: SEC EDGAR financials for 7 US retailers, their SIC industry hierarchy, and BLS producer price indexes, reconciled under one metric vocabulary.'
-- >>>
ACTIVATE USL TABLE lightning.metastore.causeway.retail_usl.sector_class AS
SELECT COALESCE(sic_code, '') AS sic_code, sic_description, industry_group, industry_group_desc,
       major_group, major_group_desc, sector, sector_desc
FROM company_dns.sic_hierarchy
-- >>>
ACTIVATE USL TABLE lightning.metastore.causeway.retail_usl.company AS
SELECT ticker, COALESCE(cik, '') AS cik, COALESCE(entity_name, '') AS entity_name,
       COALESCE(sic_code, '') AS sic_code, sic_description, fiscal_year_end_mmdd, comparability_note
FROM edgar.company
-- >>>
ACTIVATE USL TABLE lightning.metastore.causeway.retail_usl.financial_fact AS
SELECT DISTINCT
    concat_ws('|', ticker, metric, coalesce(period_start, ''), period_end, accession_number, coalesce(fiscal_period, ''), coalesce(period_frame, '')) AS fact_id,
    -- fiscal_year arrives as BIGINT; ACTIVATE accepts it against the declared int, but reads then fail
    -- ("java.lang.Long is not a valid external type for schema of int"), so cast explicitly.
    ticker, metric, xbrl_tag, period_start, COALESCE(period_end, '') AS period_end, value_usd,
    CAST(fiscal_year AS INT) AS fiscal_year, fiscal_period,
    form_type, filed_date, accession_number, period_frame
FROM edgar.financial_fact
-- >>>
ACTIVATE USL TABLE lightning.metastore.causeway.retail_usl.annual_financials AS
SELECT
    COALESCE(concat(ticker, '|', period_end), '') AS row_id,
    ticker,
    COALESCE(period_end, '') AS fiscal_year_end,
    revenue,
    cost_of_sales,
    revenue - cost_of_sales AS gross_profit,
    (revenue - cost_of_sales) / revenue AS gross_margin,
    operating_income,
    operating_income / revenue AS operating_margin,
    inventory,
    revenue_filing
FROM (
    SELECT
        ticker,
        period_end,
        MAX(CASE WHEN metric = 'revenue' THEN value_usd END) AS revenue,
        MAX(CASE WHEN metric = 'cost_of_sales' THEN value_usd END) AS cost_of_sales,
        MAX(CASE WHEN metric = 'operating_income' THEN value_usd END) AS operating_income,
        MAX(CASE WHEN metric = 'inventory' THEN value_usd END) AS inventory,
        MAX(CASE WHEN metric = 'revenue' THEN accession_number END) AS revenue_filing
    FROM (
        -- As originally reported (first 10-K that carried the period). Latest-filed values mix bases:
        -- Dollar Tree's 2025 10-K restated fiscal 2022 to exclude Family Dollar ($28.32B -> $15.41B revenue)
        -- while fiscal 2021 kept the old basis, which manufactured a fake +8 pt margin jump.
        SELECT ticker, metric, period_end, value_usd, accession_number,
               ROW_NUMBER() OVER (PARTITION BY ticker, metric, period_end ORDER BY filed_date ASC) AS rn
        FROM edgar.financial_fact
        WHERE form_type = '10-K'
          AND (metric = 'inventory'
               OR datediff(to_date(period_end), to_date(period_start)) BETWEEN 350 AND 380)
    ) latest
    WHERE rn = 1
    GROUP BY ticker, period_end
) pivoted
WHERE revenue IS NOT NULL
-- >>>
ACTIVATE USL TABLE lightning.metastore.causeway.retail_usl.price_index AS
SELECT
    COALESCE(concat(series_id, '|', CAST(observation_date AS STRING)), '') AS row_id,
    COALESCE(series_id, '') AS series_id, COALESCE(series_name, '') AS series_name, description, story_role,
    COALESCE(CAST(observation_date AS STRING), '') AS observation_date,
    index_value
FROM macro.ppi
