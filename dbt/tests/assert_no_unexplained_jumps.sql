-- Gate: an adjusted daily move above 35% must be a move somebody traded.
--
-- The adjusted return is built from the exchange's published change, so comparing the two
-- proves nothing (assert_adjusted_returns_reconcile only checks the model's arithmetic).
-- This gate uses what the change does not determine:
--   * volume: a price cannot move by a third on a session without a single trade;
--   * the corporate-action flag: a move that large on an ex-date is quarantined in
--     adjusted_prices (no return) and must not reach the study.
-- An override row in seeds/corporate_action_overrides.csv settles a case by hand.
-- Rows returned = failures.
select
    isin, trade_date, ticker, prev_close, close, change_pct, factor, factor_source, volume,
    adj_ret, adj_ret_since_prev
from {{ ref('adjusted_prices') }}
where abs(adj_ret_since_prev) > 0.35
  and override_action is null
  and (
      coalesce(volume, 0) = 0
      or (factor_source = 'exchange_reference' and adj_ret is not null)
  )
