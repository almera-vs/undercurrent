# Decisions

Short, dated entries. Newest at the bottom of each milestone.

## Milestone 1 — skeleton (2026-11-09)

- **Python 3.12 via uv**, all versions pinned in `uv.lock`. CI runs `uv sync --locked`, so an
  out-of-date lock fails the build.
- **dbt is a separate dependency group** (`uv sync --group dbt`). dbt-core pulls ~40 packages;
  keeping it out of the default group keeps unit-test CI fast. The daily job installs it.
- **CLI uses argparse**, not click/typer: one less dependency, nothing we need is missing.
- **One polite HTTP client** (`undercurrent.fetch.PoliteClient`) is the only way the code
  touches the network. It enforces ≥1 s between requests plus 0–0.5 s jitter, sends a
  project-named User-Agent (`undercurrent/0.1 (+repo URL)`, no browser impersonation), does
  ETag / If-Modified-Since revalidation against `.cache/http`, and raises
  `SourceBlockedError` when it sees a WAF page. It never retries around a block.
- **`undercurrent probe`** plus a manual `probe-sources` workflow checks every source from a
  GitHub-hosted runner, because "works from my PC" says nothing about Azure egress IPs.
- Excel readers: `xlrd` for GPW `.xls` and old NewConnect `.xls`; `openpyxl` for NewConnect
  `.xlsx`. Both are small, pure-Python, and the official files come in these formats.
