-- BLS Producer Price Indexes (retail margins, freight, warehousing, core inflation).
-- Fetched by scripts/fetch_ppi.py (FRED's public CSV of the BLS series) into the Zetaris
-- shared file area, then registered as a FILESTORE table. Data: U.S. BLS, public domain.

CREATE LIGHTNING DATABASE MACRO_DATA DESCRIBE BY "BLS producer price indexes for retail margins and cost pressures";

CREATE LIGHTNING FILESTORE TABLE ppi_series_raw
FROM MACRO_DATA
FORMAT CSV
OPTIONS (
  PATH "file:///home/zetaris/customer-data/shared/causeway/ppi/ppi_series.csv",
  inferSchema "true",
  header "true"
);
CACHE TABLE macro_data.ppi_series_raw;

CREATE SCHEMASTORE CONTAINER macro;

CREATE SCHEMASTORE VIEW ppi WITH CONTAINER macro AS
SELECT
    series_id,
    series_name,
    description,
    story_role,
    CAST(observation_date AS DATE) AS observation_date,
    CAST(value AS DOUBLE) AS index_value
FROM macro_data.ppi_series_raw;
