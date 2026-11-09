# Undercurrent data branch

Parquet lake written by GitHub Actions. Do not edit by hand.

- `bronze/{table}/date=YYYY-MM-DD/part-N.parquet` — immutable daily partitions
- `state/` — small mutable state (runs, failed reports)

See `docs/DECISIONS.md` on `main`.
