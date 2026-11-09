select
    trade_date,
    wig_close,
    wig_close / lag(wig_close) over (order by trade_date) - 1 as wig_ret
from {{ ref('benchmark') }}
