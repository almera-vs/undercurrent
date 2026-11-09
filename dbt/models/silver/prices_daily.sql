-- Unadjusted daily OHLCV for all shares, both markets, one row per (isin, trade_date).
with gpw as (
    select
        isin, trade_date, 'GPW' as market, name as short_name, name as ticker, currency,
        open, high, low, close, cast(null as double) as reference,
        volume, trades, turnover_kpln * 1000 as value_pln, known_at, source_url
    from {{ ref('bronze_prices_gpw') }}
),
nc as (
    select
        isin, trade_date, 'NC' as market, name as short_name, ticker, currency,
        open, high, low, close, reference,
        volume, trades, value_pln, known_at, source_url
    from {{ ref('bronze_prices_nc') }}
),
unioned as (
    select * from gpw
    union all
    select * from nc
)
select *
from unioned
where isin is not null and close is not null and close > 0
qualify row_number() over (partition by isin, trade_date order by market) = 1
