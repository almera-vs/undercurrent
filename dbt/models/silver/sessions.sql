-- Trading calendar: every date with a WIG close is a GPW session.
-- The lake can have holes: months not backfilled yet, or a single day whose files were not
-- read. A hole is a step that skips a session of the exchange's own calendar
-- (silver/gpw_sessions.parquet, written from gpw_calendar.py), or one of more than a week.
-- session_idx jumps by 1000 there, so nothing that asks for "the next session"
-- (session_idx + 1) can reach across it.
with d as (
    select trade_date, lag(trade_date) over (order by trade_date) as prev_date
    from {{ ref('benchmark') }}
),
cal as (
    select trade_date from {{ work_file('silver/gpw_sessions.parquet') }}
),
h as (
    select
        d.trade_date,
        coalesce(
            date_diff('day', d.prev_date, d.trade_date) > 7 or count(c.trade_date) > 0,
            false
        ) as after_hole
    from d
    left join cal c on c.trade_date > d.prev_date and c.trade_date < d.trade_date
    group by d.trade_date, d.prev_date
)
select
    trade_date,
    after_hole,
    row_number() over (order by trade_date)
        + 1000 * sum(after_hole::int) over (order by trade_date) as session_idx
from h
