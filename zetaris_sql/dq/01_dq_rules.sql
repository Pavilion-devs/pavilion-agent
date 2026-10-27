-- Data-quality rules on the Causeway USL. Row-level only: Zetaris DQ rules run as filters,
-- so window functions and cross-row checks (e.g. year-over-year margin jumps) live in
-- zetaris_sql/dq/02_cross_row_checks.sql as plain queries instead.
-- PK/FK constraints in the DDL also generate rules automatically (named after their column).

-- Accounting identity: cost of sales can't exceed revenue.
REGISTER DQ cost_below_revenue TABLE lightning.metastore.causeway.retail_usl.annual_financials AS
cost_of_sales IS NULL OR cost_of_sales < revenue;

-- A US retailer's gross margin outside 5%..60% is almost certainly a tagging or scaling error.
REGISTER DQ gross_margin_plausible TABLE lightning.metastore.causeway.retail_usl.annual_financials AS
gross_margin IS NULL OR (gross_margin >= 0.05 AND gross_margin <= 0.60);

-- Operating income can't exceed gross profit.
REGISTER DQ operating_within_gross TABLE lightning.metastore.causeway.retail_usl.annual_financials AS
operating_income IS NULL OR gross_profit IS NULL OR operating_income <= gross_profit;

-- Flows and balances that can't be negative.
REGISTER DQ non_negative_amounts TABLE lightning.metastore.causeway.retail_usl.financial_fact AS
metric NOT IN ('revenue', 'cost_of_sales', 'inventory') OR value_usd >= 0;

-- A duration fact must start before it ends.
REGISTER DQ period_ordered TABLE lightning.metastore.causeway.retail_usl.financial_fact AS
period_start IS NULL OR period_start <= period_end;

-- Price indexes are strictly positive.
REGISTER DQ index_positive TABLE lightning.metastore.causeway.retail_usl.price_index AS
index_value > 0;

RUN DQ TABLE lightning.metastore.causeway.retail_usl.sector_class;
RUN DQ TABLE lightning.metastore.causeway.retail_usl.company;
RUN DQ TABLE lightning.metastore.causeway.retail_usl.financial_fact;
RUN DQ TABLE lightning.metastore.causeway.retail_usl.annual_financials;
RUN DQ TABLE lightning.metastore.causeway.retail_usl.price_index;
LIST DQ USL lightning.metastore.causeway.retail_usl;
