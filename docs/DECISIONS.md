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

## Source research review (2026-11-09) — before building ingest

Full details in `SOURCES.md`. What changes the plan:

### Blocker: PDF attachments are bot-protected (affects milestone 3)

- **What:** every `https://www.gpw.pl/attachment/…` URL answers with an F5 BIG-IP ASM
  "Request Rejected" page and a JavaScript challenge instead of the PDF. Same result from a
  residential IP and from a GitHub-hosted runner, with or without the report page's cookies
  and Referer, on four different attachments. PAP's mirror (espiebi.pap.pl) is behind an
  Imperva/Incapsula challenge.
- **Why we stop here:** C6 says a blocked source is written up, not worked
  around. Getting past the challenge would need a headless browser or challenge solving,
  which is exactly the thing the WAF is there to stop.
- **Impact:** the insider notification forms (MAR Art. 19(1) "notification of transactions")
  are attachments. Without them we cannot meet the milestone 3 acceptance criterion as written
  ("98% field-level accuracy on 300+ hand-labelled insider notification **PDFs**").
- **What we do instead (needs the owner's sign-off):**
  1. Insider fields are extracted from the **HTML report page**: legal basis, title and
     `Treść raportu`. These reliably give the event (Art. 19 MAR), the issuer, the
     published time and usually the person and role. Many issuers also restate the trade
     (buy/sell, volume, price) in the body; when they do, we parse it. When they don't, the
     field is **null with a logged reason**. We never guess.
  2. The accuracy harness (`tests/fixtures/labelled_pdfs/` + scoring script) is kept, but its
     fixtures are labelled report-page extracts (HTML/text), not PDFs. The 98% target applies
     to fields that are present in the source text. Coverage (the share of reports where each
     field is present at all) is reported separately and honestly.
  3. `pdfplumber` stays in the stack with a PDF-form extractor that works on local files, so
     PDFs can be added if a permitted route ever appears (for example GPW granting access).
     It is not wired into the nightly fetch.
- **Signal impact:** `insider_tx` events still exist, with direction where stated. Studies
  that need trade value or the share of free float run only on the subset where those fields
  are known, and report that `n`.

### NewConnect is a separate site

gpw.pl's listing has main-market issuers only. NewConnect reports come from
`newconnect.pl/ajaxindex.php` (same handler, `page=spolki-komunikaty-spolek`), and report
pages from `newconnect.pl/komunikat?geru_id=`. `geru_id` sequences differ per site, so the
primary key is `report_id = "{site}:{geru_id}"` (for example `gpw:500131`, `nc:244788`).

### NewConnect price file timing

`NC_YYYYMMDD.xlsx` is published between 16:47 and 18:50 UTC, often after the 17:47 UTC cron.
The daily job ingests ESPI and main-market prices first, then polls the NewConnect file with a
conditional GET every 5 minutes, at most until 19:05 UTC (20:05 CET / 21:05 CEST). If it
still isn't there, the run publishes with NewConnect prices marked pending in the health
line. The next run picks the file up, because ingest always back-fills missing price days in
the last 10 sessions.

DST: in summer (CEST) the deadline is 18:30 UTC, so the summer polling cutoff is 18:15 UTC.
The job computes the cutoff from Europe/Warsaw local time rather than hard-coding UTC.

### Backfill volume

About 90 reports per trading day × ~2,950 trading days since 2015 ≈ 265k report pages. At the
polite rate (~1.25 s/request) that is ≈ 92 runner-hours, split into ~143 monthly jobs of
≈ 40 min each. That is fine on a public repo. On a private repo it would blow far past the
2,000 free minutes per month, which is one more reason the repo must be public (C4).

### Repo visibility

The GitHub repo is currently **private**. C4 requires public (free Actions minutes,
free Pages). Flipping visibility is the owner's action; until then CI runs on the private
free quota and Pages deploys will fail.
