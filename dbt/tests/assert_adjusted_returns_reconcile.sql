-- Every adjusted daily return must agree with the change the exchange published for that
-- session (within 0.5 pp), except on days an override replaces the factor.
-- Tolerated: missing change_pct (NewConnect "---").
-- This checks the model's arithmetic (factors and the back-adjustment), not the data: the
-- return is built from that change. The data gates are assert_no_unexplained_jumps,
-- assert_no_reversed_factors and assert_nc_reference_matches_change.
select isin, trade_date, ticker, adj_ret_since_prev, change_pct, factor, factor_source
from {{ ref('adjusted_prices') }}
where adj_ret_since_prev is not null
  and change_pct is not null
  and override_action is null
  and not exchange_confirmed
