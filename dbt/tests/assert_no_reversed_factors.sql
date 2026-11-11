-- Gate: a large adjustment factor that is undone within a month is a bad print, not a
-- corporate action. A garbled close (a shifted decimal point, say) looks like a split on the
-- day it appears and like the opposite split when the price comes back.
-- An override row on either date settles a case by hand. Rows returned = failures.
with big as (
    select
        isin, ticker, trade_date, factor, override_action,
        lag(factor) over w as prev_factor,
        lag(trade_date) over w as prev_factor_date,
        lag(override_action) over w as prev_override_action
    from {{ ref('adjusted_prices') }}
    where factor_source is not null and abs(ln(factor)) > ln(1.35)
    window w as (partition by isin order by trade_date)
)
select isin, ticker, prev_factor_date, prev_factor, trade_date, factor
from big
where prev_factor is not null
  and abs(ln(factor) + ln(prev_factor)) < 0.1
  and date_diff('day', prev_factor_date, trade_date) <= 31
  and override_action is null
  and prev_override_action is null
