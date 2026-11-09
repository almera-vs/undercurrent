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

## Milestone 2 — ingest (2026-11-09)

- **Green on a GitHub runner.** Run
  [42629599476](https://github.com/almera-vs/undercurrent/actions/runs/42629599476) landed 295
  reports (2026-11-03 … 06, 0 failed, 0 blocked) and 10 sessions of GPW shares, WIG and NC
  prices. Its commit step failed (missing exec bit on `scripts/commit_data.sh`, fixed). The
  re-run [42630917651](https://github.com/almera-vs/undercurrent/actions/runs/42630917651)
  pushed the partitions to `data` in 36 s thanks to the HTTP cache.
- **Partition key = publication date (Warsaw) for reports, session date for prices.** A
  report published after the nightly run lands in the next `part-N` of its own date's
  partition on the following run. Existing files are never rewritten. Re-runs that find
  nothing new write nothing (tested: byte-identical bronze after a second backfill).
- **Report pages are fetched only for current reports (`Bieżący`).** Periodic reports are
  classified from the listing, saving ≈ 20 % of requests.
- **Three page templates** are handled: legacy ESPI (labelled table rows), legacy EBI (free
  text with a trailing `Podstawa prawna:`), and the 2026 "gpwdata" template, where the
  legal basis is in the header and the body exists only as a PDF (→ `body_text` null with a
  note).
- **Source data quality:** some ESPI pages lack Polish letters at the byte level
  (`Rozporzdzenia`, `penice`). The source does this, not our decoder. Rules match on
  diacritic-free tokens and on `MAR`, so these still classify.
- **Silver/gold are rebuilt every run and never committed.** Only bronze (immutable),
  `state/` (small) and later small daily gold snapshots go on the `data` branch. Rebuilt
  tables live in `.work/`. Committing a nightly rewrite of a growing table would bloat git
  history.
- **Backfill is sequential** (`max-parallel: 1`) and pauses 17:30–19:30 UTC on weekdays, so
  total traffic to the GPW sites stays ≤ 1 req/s even while the nightly job runs.
  Failed-report state is per month, so the two jobs never write the same file.

## Milestone 3 — parse and classify (2026-11-09) — partially met

- **Classification** (`classify.py`) is a pure function: legal basis first, then
  title/body fallbacks, then keyword sub-types for inside information. Every label carries
  the rule that produced it (`class_rule`). Corrections found on real data:
  - Art. 70 pkt 3 (list of ≥5 % holders at a general meeting) is **not** a threshold
    crossing; only Art. 69/69a and Art. 70 pkt 1 are.
  - An Art. 19 basis without any insider wording is reclassified `other`
    (`basis:mar19:no_text_support`). Seen: Artifex Mundi filed a monthly revenue estimate
    under Art. 19.
- **Entity resolution:** 295/295 reports resolved by exact ISIN from the listing. The
  rapidfuzz fallback and review list exist but had nothing to do on this sample.
- **Insider fields** come from report text (PDFs are blocked). On the 15 real Art. 19
  reports of 2026-11-02 … 06: field-level accuracy **99.2 %** (all fields, nulls included),
  **97.1 %** on stated fields. **Caveat: in-sample.** These 15 were used to fix the
  extractor, so this is a development-set number, not a held-out one.
- **Coverage is the real limitation:** person/role are stated in 14/15 bodies, but side in
  2/15, volume 2/15, price 1/15, value 0/15. Trade details live in the blocked PDFs.
- **Not met:** "≥ 98 % on 300+ hand-labelled PDFs". Blocked on (a) the PDF decision and
  (b) enough labelled examples. Once the backfill lands, the plan is to label a fresh,
  held-out batch of Art. 19 report texts (not used for tuning) and report that number.
  CI already gates on the current labels at ≥ 98 %.
- **Number/date parsers** are Hypothesis-tested: round-trips over Polish (`1 234,56`),
  dotted (`1.234.567,89`) and English (`1,234.56`) formats, and ISO / `DD.MM.YYYY` /
  `5 listopada 2026` dates.
