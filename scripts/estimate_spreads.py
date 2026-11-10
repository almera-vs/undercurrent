"""Estimate effective bid-ask spreads per market from daily high, low and close.

Abdi and Ranaldo (2017): with ``c`` the log close and ``eta`` the mean of the log high and
log low, the squared effective spread is ``4 * E[(c_t - eta_t) * (c_t - eta_{t+1})]``. Run on
the DuckDB file a transform built, it prints the distribution across stocks that trade on at
least half of the sessions (the stocks the study keeps), and the same for the stocks behind
actual events. The round-trip cost in ``study.py`` is one spread plus commissions both ways.

    uv run python scripts/estimate_spreads.py .work/undercurrent.duckdb
"""

from __future__ import annotations

import sys

import duckdb

QUERY = """
with d as (
    select isin, market, trade_date, volume,
           ln(close) as c, (ln(high) + ln(low)) / 2 as eta,
           lead((ln(high) + ln(low)) / 2) over (partition by isin order by trade_date) as eta_next,
           lead(volume) over (partition by isin order by trade_date) as vol_next
    from prices_daily
    where high > 0 and low > 0 and close > 0
),
s as (
    select isin, any_value(market) as market,
           avg((volume > 0)::int) as traded_share,
           count(*) filter (where volume > 0 and vol_next > 0) as n,
           4 * avg((c - eta) * (c - eta_next)) filter (where volume > 0 and vol_next > 0) as s2
    from d group by isin
),
e as (select isin, count(*) as events from events where isin is not null group by isin)
select market,
       count(*) as stocks,
       round(100 * median(sqrt(greatest(s2, 0))), 2) as median_spread_pct,
       round(100 * avg(sqrt(greatest(s2, 0))), 2) as mean_spread_pct,
       round(100 * quantile_cont(sqrt(greatest(s2, 0)), 0.75), 2) as p75_spread_pct,
       round(100 * sum(sqrt(greatest(s2, 0)) * coalesce(events, 0)) / sum(coalesce(events, 0)), 2)
           as event_weighted_spread_pct
from s left join e using (isin)
where n >= 100 and traded_share >= 0.5
group by market order by market
"""


def main(path: str) -> None:
    con = duckdb.connect(path, read_only=True)
    try:
        cols = [d[0] for d in con.execute(QUERY).description]
        print(" | ".join(cols))
        for row in con.execute(QUERY).fetchall():
            print(" | ".join(str(v) for v in row))
    finally:
        con.close()


if __name__ == "__main__":
    main(sys.argv[1])
