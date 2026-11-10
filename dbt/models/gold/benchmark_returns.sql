-- WIG daily returns. No return on the first session after a hole in the lake: the move
-- since the previous known close spans the whole hole, not one day.
select
    b.trade_date,
    b.wig_close,
    case
        when not s.after_hole
        then b.wig_close / lag(b.wig_close) over (order by b.trade_date) - 1
    end as wig_ret
from {{ ref('benchmark') }} b
join {{ ref('sessions') }} s using (trade_date)
