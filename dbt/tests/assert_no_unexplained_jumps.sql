-- Milestone 4 gate: no daily move above 35% in the adjusted series unless it is
-- explained by a corporate action (exchange reference adjustment or an override row)
-- or confirmed as a genuine market move by the exchange's own published change.
-- Rows returned = failures.
select isin, trade_date, ticker, prev_close, close, change_pct, factor, adj_ret
from {{ ref('adjusted_prices') }}
where abs(adj_ret) > 0.35
  and override_action is null
  and not coalesce(exchange_confirmed, false)
