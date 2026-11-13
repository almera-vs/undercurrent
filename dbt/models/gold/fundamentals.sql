-- Headline facts of ESEF annual reports (esef.py), one row per fact and ISIN of the issuer.
-- known_at is when filings.xbrl.org added the filing: later than the issuer's own
-- publication, so the data can only look older than it was. Empty until filings are read.
select
    isin, lei, filing_id, fiscal_period_end, concept, period_start, period_end, value, unit,
    decimals, known_at
from {{ work_file('silver/fundamentals.parquet') }}
where {{ known_by() }}
