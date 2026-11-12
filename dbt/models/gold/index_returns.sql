-- Daily returns of the GPW indices the market state uses: WIG (the benchmark), the size
-- indices (WIG20, mWIG40, sWIG80) and the treasury bond index TBSP. One row per (name,
-- session). A return exists only between consecutive sessions: not after a hole in the
-- lake (session_idx jumps by 1000 there) and not across a session the index file missed.
with i as (
    select name, trade_date, close, known_at
    from {{ ref('bronze_indices_gpw') }}
    where name in ('WIG', 'WIG20', 'MWIG40', 'SWIG80', 'TBSP.INDEX') and close > 0
    qualify row_number() over (partition by name, trade_date order by ingested_at desc) = 1
)
select
    i.name,
    i.trade_date,
    i.close,
    case
        when lag(s.session_idx) over w = s.session_idx - 1
        then i.close / lag(i.close) over w - 1
    end as ret,
    i.known_at
from i
join {{ ref('sessions') }} s using (trade_date)
window w as (partition by i.name order by i.trade_date)
