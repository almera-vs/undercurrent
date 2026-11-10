-- Trading calendar: every date with a WIG close is a GPW session.
-- The lake can have holes (months not backfilled yet). Real sessions are never more than a
-- week apart, so a longer step is a hole: session_idx jumps by 1000 there, and nothing that
-- asks for "the next session" (session_idx + 1) can reach across it.
with d as (
    select trade_date, lag(trade_date) over (order by trade_date) as prev_date
    from {{ ref('benchmark') }}
),
h as (
    select
        trade_date,
        coalesce(date_diff('day', prev_date, trade_date) > 7, false) as after_hole
    from d
)
select
    trade_date,
    after_hole,
    row_number() over (order by trade_date)
        + 1000 * sum(after_hole::int) over (order by trade_date) as session_idx
from h
