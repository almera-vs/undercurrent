-- WIG closes: the market-model benchmark.
select trade_date, close as wig_close, known_at
from {{ ref('bronze_indices_gpw') }}
where name = 'WIG' and close > 0
qualify row_number() over (partition by trade_date order by ingested_at desc) = 1
