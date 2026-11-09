-- Trading calendar: every date with a WIG close is a GPW session.
select trade_date, row_number() over (order by trade_date) as session_idx
from {{ ref('benchmark') }}
