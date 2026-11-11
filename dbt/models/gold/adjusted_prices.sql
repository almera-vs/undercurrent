-- Back-adjusted closes and daily returns.
--
-- Adjustment factors come from the exchange itself: on an ex-date GPW/NewConnect set the
-- session's reference price below (dividend, split, rights) or above (reverse split) the
-- previous close. NewConnect publishes the reference price; for GPW we recover it from the
-- published change: reference = close / (1 + change_pct / 100).
--   factor_t = reference_t / prev_close_t      (applies to every price before t)
--   adj_close_t = close_t * prod(factor_s for s > t)
-- seeds/corporate_action_overrides.csv replaces the factor for (isin, ex_date) when the
-- rules can't resolve a case. Factors within FACTOR_TOL of 1 are treated as 1 (rounding).
--
-- Nothing is derived across a hole in the lake (months not backfilled yet): the previous
-- close on the far side is years old, so there is no factor and no return there.
{% set factor_tol = 0.002 %}
{% set jump_limit = 0.35 %}
with p as (
    select
        d.isin, d.trade_date, d.market, d.ticker, d.short_name, d.close, d.change_pct,
        d.reference, d.volume, d.value_pln, d.known_at,
        s.session_idx,
        lag(d.close) over w as prev_close,
        lag(d.trade_date) over w as prev_date,
        lag(s.session_idx) over w as prev_session_idx
    from {{ ref('prices_daily') }} d
    left join {{ ref('sessions') }} s on s.trade_date = d.trade_date
    window w as (partition by d.isin order by d.trade_date)
),
ref as (
    select
        *,
        -- same stretch of the calendar as the previous row (sessions.sql adds 1000 at a hole)
        coalesce(session_idx - prev_session_idx < 1000, false) as linked,
        case
            when market = 'NC' and reference > 0 then reference
            when change_pct is not null and change_pct > -100 then close / (1 + change_pct / 100)
        end as exch_reference
    from p
),
f as (
    select
        r.*,
        case when r.linked then r.exch_reference / r.prev_close end as exch_factor,
        o.factor as override_factor,
        o.action as override_action,
        o.comment as override_comment
    from ref r
    left join {{ ref('corporate_action_overrides') }} o
        on o.isin = r.isin and cast(o.ex_date as date) = r.trade_date
),
factored as (
    select
        *,
        case
            when override_factor is not null then override_factor
            when prev_close is null or exch_factor is null then 1.0
            when abs(exch_factor - 1) <= {{ factor_tol }} then 1.0
            else exch_factor
        end as factor,
        case
            when override_factor is not null then 'override'
            when prev_close is not null and exch_factor is not null
                and abs(exch_factor - 1) > {{ factor_tol }} then 'exchange_reference'
        end as factor_source
    from f
),
adj as (
    select
        *,
        close * exp(coalesce(sum(ln(factor)) over (
            partition by isin order by trade_date
            rows between 1 following and unbounded following
        ), 0)) as adj_close
    from factored
),
moves as (
    select
        *,
        case
            when linked
            then adj_close / lag(adj_close) over (partition by isin order by trade_date) - 1
        end as adj_ret_since_prev
    from adj
),
ret as (
    select
        *,
        -- A move above the jump limit on the very day the exchange adjusted the reference:
        -- the market's move and the corporate action cannot be told apart from one file, so
        -- the day's return is not used until an override row settles the factor.
        coalesce(
            factor_source = 'exchange_reference' and abs(adj_ret_since_prev) > {{ jump_limit }},
            false
        ) as quarantined
    from moves
)
select
    isin, trade_date, market, ticker, short_name, close, prev_close, prev_date, change_pct,
    exch_reference, exch_factor, factor, factor_source, override_action, override_comment,
    volume, value_pln, known_at, adj_close,
    -- A daily return only between consecutive sessions. After a gap (no trades, or a
    -- missing file) the move spans several sessions and must not be booked as one day.
    case
        when session_idx - prev_session_idx = 1 and not quarantined then adj_ret_since_prev
    end as adj_ret,
    adj_ret_since_prev,
    quarantined,
    -- the model's own arithmetic holds: the adjusted move equals the published change
    change_pct is not null
        and abs(adj_ret_since_prev * 100 - change_pct) < 0.5 as exchange_confirmed
from ret
