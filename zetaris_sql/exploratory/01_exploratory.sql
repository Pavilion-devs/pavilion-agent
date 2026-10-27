-- Exploratory queries, 2-3 per source (Zetaris scoring 1b). Run with scripts/run_sql.py.

-- Source 1: SEC EDGAR companyconcept (edgar.financial_fact)
-- 1a. How many facts, filings and fiscal years per company?
SELECT ticker, COUNT(*) AS facts, COUNT(DISTINCT accession_number) AS filings,
       MIN(fiscal_year) AS first_fy, MAX(fiscal_year) AS last_fy
FROM edgar.financial_fact GROUP BY ticker ORDER BY ticker;
-- 1b. Which XBRL tag does each company use for each canonical metric?
SELECT DISTINCT ticker, metric, xbrl_tag FROM edgar.financial_fact ORDER BY metric, ticker;
-- 1c. Target's annual revenue and cost of sales from its 10-K filings, FY2021-FY2023.
SELECT metric, period_end, value_usd, accession_number, filed_date
FROM edgar.financial_fact
WHERE ticker = 'TGT' AND form_type = '10-K' AND metric IN ('revenue', 'cost_of_sales')
  AND period_end BETWEEN '2022-01-01' AND '2024-03-01'
  AND datediff(to_date(period_end), to_date(period_start)) BETWEEN 350 AND 380
ORDER BY metric, period_end, filed_date;

-- Source 2: SEC EDGAR submissions (edgar.company)
-- 2a. Every company with its industry code and fiscal year end.
SELECT ticker, entity_name, sic_code, sic_description, fiscal_year_end_mmdd FROM edgar.company ORDER BY ticker;
-- 2b. How many of our companies share each SIC code (the natural peer groups)?
SELECT sic_code, sic_description, COUNT(*) AS companies FROM edgar.company GROUP BY sic_code, sic_description ORDER BY companies DESC;

-- Source 3: company_dns SIC hierarchy (company_dns.sic_hierarchy)
-- 3a. How large is the reference, and how many classes per sector?
SELECT sector, sector_desc, COUNT(*) AS sic_classes FROM company_dns.sic_hierarchy GROUP BY sector, sector_desc ORDER BY sector;
-- 3b. The retail-trade branch our universe lives in.
SELECT major_group, major_group_desc, COUNT(*) AS sic_classes
FROM company_dns.sic_hierarchy WHERE sector = 'G' GROUP BY major_group, major_group_desc ORDER BY major_group;

-- Source 4: BLS producer price indexes (macro.ppi)
-- 4a. Coverage per series.
SELECT series_name, description, COUNT(*) AS months, MIN(observation_date) AS first_month, MAX(observation_date) AS last_month
FROM macro.ppi GROUP BY series_name, description ORDER BY series_name;
-- 4b. Average index by calendar year, 2020-2023: when did freight and margins move?
SELECT series_name, YEAR(observation_date) AS yr, ROUND(AVG(index_value), 1) AS avg_index
FROM macro.ppi WHERE YEAR(observation_date) BETWEEN 2020 AND 2023
GROUP BY series_name, YEAR(observation_date) ORDER BY series_name, yr;
