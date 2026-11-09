# Scaling

Undercurrent today: ~90 reports and ~800 price rows per trading day, ≈ 300k reports and
≈ 2.5M price rows over 2015–2026. Everything fits in one process on one GitHub runner, and the
whole lake is well under 1 GB of Parquet. The stack is deliberately small: Python, DuckDB,
Parquet files in a git branch, dbt, cron.

What would be needed at **1,000× the volume** (all European venues, intraday prices, news
feeds, many consumers), and why none of it is justified now:

| Need at 1,000× | Addition | Why not now |
|---|---|---|
| Continuous ingestion, many producers, replay | **A streaming backbone** (Kafka / Redpanda) with reports and ticks as event streams, consumers for parsing and alerting | Data arrives in one batch per evening from two websites at ≤ 1 req/s. A log of logs adds operations, cost and failure modes, and no latency we need. The nightly job runs in minutes. |
| Concurrent writers, schema evolution, time travel, compaction | **A table format with a catalog** (Iceberg or Delta + REST/Glue catalog) on object storage | One writer at a time, append-only daily partitions, and git history already gives time travel. Object storage and catalogs need accounts and money, and the project runs at zero cost. |
| Many interdependent jobs, backfills, SLAs per asset | **An orchestrator** (Dagster / Airflow) with asset lineage, retries and sensors | Six sequential steps in one Python function. GitHub Actions gives scheduling, retries (re-run), logs and a matrix for backfills. |
| Interactive queries by many users | A warehouse (or MotherDuck / ClickHouse) behind an API | The product is a static site rebuilt nightly. Readers never query. |
| Parsing PDFs at scale | A document pipeline with OCR and layout models | The forms are bot-protected at the source, and parsing here is rule-based by design. |
| Entity resolution across venues | A master-data service with LEI/ISIN/FIGI | ISINs on the listing already resolve 100 % of current reports. |

The parts that *would* carry over unchanged: immutable partitions keyed by publication date,
`known_at` on every row, point-in-time cuts, exchange-derived adjustment factors, seeded
statistics and the SHA-256 reproducibility gate. Those are properties of the data model, not
of the infrastructure.
