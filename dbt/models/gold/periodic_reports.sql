-- Periodic reports (quarterly, half-year, annual) aligned to the trading calendar, for the
-- earnings-drift feature. They are not signal events: the study does not test them.
-- event_date = the first session whose close (17:05 Warsaw) is after known_at, as in events.
select
    r.report_id, r.isin, r.market, r.ticker, r.title, r.published_at, r.known_at,
    (
        select min(s.trade_date) from {{ ref('sessions') }} s
        where timezone('Europe/Warsaw', r.known_at) < (s.trade_date + interval 17 hour + interval 5 minute)
    ) as event_date
from {{ ref('reports') }} r
where r.signal_type = 'periodic' and r.isin is not null
