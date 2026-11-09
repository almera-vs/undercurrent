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
{% set factor_tol = 0.002 %}
with p as (
    select
        isin, trade_date, market, ticker, short_name, close, change_pct, reference, volume,
        known_at,
        lag(close) over w as prev_close,
        lag(trade_date) over w as prev_date
    from {{ ref('prices_daily') }}
    window w as (partition by isin order by trade_date)
),
ref as (
    select
        *,
        case
            when market = 'NC' and reference > 0 then reference
            when change_pct is not null and change_pct > -100 then close / (1 + change_pct / 100)
        end as exch_reference
    from p
),
f as (
    select
        r.*,
        r.exch_reference / r.prev_close as exch_factor,
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
)
select
    isin, trade_date, market, ticker, short_name, close, prev_close, prev_date, change_pct,
    exch_reference, exch_factor, factor, factor_source, override_action, override_comment,
    volume, known_at, adj_close,
    adj_close / lag(adj_close) over (partition by isin order by trade_date) - 1 as adj_ret,
    -- the move is the market's own: it agrees with the change the exchange published
    change_pct is not null
        and abs(
            (adj_close / lag(adj_close) over (partition by isin order by trade_date) - 1) * 100
            - change_pct
        ) < 0.5 as exchange_confirmed
from adj
