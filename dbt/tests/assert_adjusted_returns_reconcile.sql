-- Every adjusted daily return must agree with the change the exchange published for that
-- session (within 0.5 pp), except on days an override replaces the factor.
-- Tolerated: missing change_pct (NewConnect "---").
select isin, trade_date, ticker, adj_ret, change_pct, factor, factor_source
from {{ ref('adjusted_prices') }}
where adj_ret is not null
  and change_pct is not null
  and override_action is null
  and not exchange_confirmed
