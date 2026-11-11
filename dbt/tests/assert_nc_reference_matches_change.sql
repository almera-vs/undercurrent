-- Gate: NewConnect publishes the reference price and the change as two separate columns.
-- They must tell the same story: close / reference - 1 = change (within 0.5 pp). A mismatch
-- means a garbled row or a shifted column, and the factor taken from that reference is wrong.
-- Rows returned = failures.
select isin, trade_date, ticker, close, reference, change_pct
from {{ ref('prices_daily') }}
where market = 'NC'
  and reference > 0
  and change_pct is not null
  and abs((close / reference - 1) * 100 - change_pct) > 0.5
