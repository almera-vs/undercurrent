-- Signal events aligned to the trading calendar.
-- event_session = the first session whose close (17:05 Warsaw) is after known_at.
select
    r.report_id, r.signal_type, r.subtype, r.isin, r.market, r.ticker, r.issuer_name,
    r.title, r.published_at, r.known_at, r.insider_side, r.insider_role,
    r.insider_value, r.source_url,
    (
        select min(s.trade_date) from {{ ref('sessions') }} s
        where timezone('Europe/Warsaw', r.known_at) < (s.trade_date + interval 17 hour + interval 5 minute)
    ) as event_date
from {{ ref('reports') }} r
where r.signal_type in ('insider_tx', 'holder_threshold', 'inside_info')
