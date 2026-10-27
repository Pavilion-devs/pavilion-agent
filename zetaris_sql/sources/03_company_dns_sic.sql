-- SIC industry hierarchy from company_dns (V4, production host).
-- Adapted from zetaris/getting-started open_data/rest_apis/sql/rate_limited/12_company_dns_create.sql
-- (V3 dropped: deprecated upstream). Licence: Apache 2.0, traces to SEC's public SIC list.
-- Rate limited: send an identifying User-Agent ("app_name you@domain.tld"); cached straight after creation.
-- V4 naming: section (letter, e.g. G Retail Trade) > division (2-digit) > group (3-digit) > class (4-digit SIC code).

CREATE LIGHTNING DATABASE COMPANY_DNS DESCRIBE BY "company_dns SIC reference data V4 - section, division, group, class";

CREATE LIGHTNING REST TABLE sic_codes_v4_raw FROM COMPANY_DNS REQUEST(
    endpoint "https://company-dns.mediumroast.io/V4.0/na/sic/code/%25",
    method "get",
    response_type "json",
    http_encoding "URLENCODED"
) HEADER (
    user-agent "YOUR_APP_NAME YOUR_CONTACT_EMAIL"
) BODY ();
CACHE TABLE company_dns.sic_codes_v4_raw;

CREATE SCHEMASTORE CONTAINER company_dns;

CREATE SCHEMASTORE VIEW sic_hierarchy WITH CONTAINER company_dns AS
SELECT
    item.class_id AS sic_code,
    item.class_desc AS sic_description,
    item.group_id AS industry_group,
    item.group_desc AS industry_group_desc,
    item.division_id AS major_group,
    item.division_desc AS major_group_desc,
    item.section_id AS sector,
    item.section_desc AS sector_desc
FROM company_dns.sic_codes_v4_raw
LATERAL VIEW explode(data) AS item;
