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

- **Green on a GitHub runner.** The first daily run (2026-11-09) landed 295
  reports (2026-11-03 … 06, 0 failed, 0 blocked) and 10 sessions of GPW shares, WIG and NC
  prices. Its commit step failed (missing exec bit on `scripts/commit_data.sh`, fixed). The
  re-run pushed the partitions to `data` in 36 s thanks to the HTTP cache.
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
- **Audit of the 30 insider reports in the lake (2026-11-09).** In 5 the text named the person
  but the parser missed it: a name right after the role with no "Pan/Pani", a "Pan/Pani"
  list not introduced by "od/przez", a foreign fund without a Polish legal form (`SCA`), and a
  long Portuguese name. One report was a share gift and one a warrant grant from an incentive
  plan, both previously counted as buys or left blank. Fixed: new person patterns, sides
  `gift` and `incentive` (both map to `insider_tx:unknown`, since neither is a market trade),
  and a generic `pdmr` role when the text only says "osoba pełniąca obowiązki zarządcze".
  Regression tests use fictional names. All remaining blanks in those 30 reports are
  genuinely absent from the text.
- **Attachments, re-checked:** ESPI attachments are bot-protected on newconnect.pl as well as
  gpw.pl, so the insider forms stay out of reach. NewConnect EBI files under `/ebi/files/` are
  downloadable but not parsed (no EBI report feeds a signal). See SOURCES.md §4.

## Milestone 4 — prices (2026-11-09)

- **Adjustment factors come from the exchange.** On an ex-date GPW and NewConnect set the
  session's reference price away from the previous close (dividend, split, rights issue,
  reverse split). NewConnect publishes the reference price. GPW's archive doesn't, but it
  publishes the change against the reference, so `reference = close / (1 + change/100)`.
  `factor = reference / prev_close` applies to all earlier prices. Verified on real data:
  in 10 sessions the rule found 4 GPW factors (Ambra 0.946, BoomBit 0.958, Ailleron 0.961,
  Banco Santander 0.988), all in dividend season. **GPW adjusts the reference for
  dividends**, so adjusted returns are total returns.
- Factors within 0.2 % of 1 are treated as 1. The published change has 2 decimals, which
  bounds rounding noise far below that. Dividends under 0.2 % yield are therefore ignored
  (documented bias, at most 0.2 % on one day).
- `seeds/corporate_action_overrides.csv` replaces the factor for an `(isin, ex_date)`. Every
  row needs a `comment` saying why.
- **Jump gate interpretation.** `assert_no_unexplained_jumps` fails on any adjusted daily
  move > 35 % that is neither covered by an override nor *confirmed by the exchange*, i.e.
  our adjusted return agrees (±0.5 pp) with the change the exchange published for that
  session. Why: NewConnect has genuine > 35 % moves almost daily (6 in the first 10 sessions:
  FPO, FOX ×2, UFC, BTC ×2), and hand-listing each would make the override file meaningless.
  What the gate catches is what it is meant to catch: missing or garbled prices, a stale
  previous close, a missed or wrong factor. `assert_adjusted_returns_reconcile` checks the
  same agreement on every row, not just big moves.
- Both gates run in CI against the real `data` branch (`ci.yml` → `data-checks`), and in the
  nightly job via `dbt build`.
- **Cross-check with ESPI** (split/reverse split/dividend reports vs detected factors) is
  deferred until the backfill provides enough history to make it meaningful.

## Milestone 5 — backfill (2026-11-09) — workflow verified, full run waiting on repo visibility

- `backfill.yml` was verified on a runner for 2026-10-19 … 25
  on 2026-11-09: 7 days, 0 failures, partitions pushed to `data`, 9 min. Re-running is idempotent.
- At ≈ 9 min per week, 2015-01-01 → today is ≈ 80 runner-hours in ~143 sequential monthly jobs
  (~4 days wall-clock).
- **Not started**, because the repo is still private. 80 hours would exhaust the free private
  quota (2,000 min/month, shared with the owner's other private repos) within the first day.
  On a public repo standard runners are free and unmetered. Once public:
  `gh workflow run backfill -f start=2015-01-01 -f end=<yesterday>`.
- **Monthly compaction** (`compact.yml`) merges daily partitions older than two months into
  `month=YYYY-MM/part-0.parquet` with a `_partition_date` column, so a single day can still be
  found. Ingest checks both layouts before fetching, so compaction never causes re-downloads
  (tested). Late rows that arrive for an already compacted month land as a new daily part
  next to the month file. Both are read.

## Milestone 6 — event studies (2026-11-09)

- Implemented as specified (see METHODOLOGY.md). Gates: `tests/reproducibility` (identical
  SHA-256 over two runs) and `tests/replay` (as-of recomputation never sees later rows; an
  unfiltered input raises `LookAheadError`). Both run in CI.
- Day 0 = first session whose 17:05 close is after `known_at`.
- Tested on a seeded synthetic market with a planted +3 % day-1 effect: detected (q < 0.05,
  CI excludes 0), the null signal is `noise`.

## Milestone 7 — walk-forward (2026-11-09) — implemented, result pending backfill

- **Rule choice by t-statistic, not mean.** On the synthetic market, choosing the horizon with
  the best past *mean* picked 60-day holds (pure noise) and turned a real +3 % effect into
  −0.6 % out of sample. Ranking by mean / standard error fixed it. Years where no candidate
  was net-positive in the past are not traded.
- Short rules are allowed, and METHODOLOGY flags that borrow is unrealistic for small caps.
- The README "Honest results" section says there are no results until the backfill runs.

## Milestone 8 — site and Telegram (2026-11-09)

- Static site: Today, Signals (one page per horizon: 1/5/20/60d, default 20d), event detail
  pages with an inline-SVG CAR chart and bootstrap band, and a Pipeline page (the `runs` table
  with an SLO column). Dark theme, blue up / orange down, Inter + JetBrains Mono, AA contrast,
  no horizontal scroll at 375 px (checked).
- Facts on cards come only from parsed fields or the report title. When insider details are
  missing the card says so ("trade details are only in the attached PDF") instead of
  guessing.
- **Telegram**: the daily run sent the first digest for 2026-11-06
  (run on 2026-11-09).
  Header, counts, up to 5 items, two URL buttons, health line, disclaimer. On job failure an
  alert is sent instead. On a weekday with no GPW session data the digest is replaced by an
  alert. `watchdog.yml` alerts if there's no successful run by 20:15 Warsaw.
- **Keepalive:** the run commits `digests/YYYY/MM/DD.md` to `main` every night. It isn't documented
  whether data-branch commits alone keep schedules alive, so we do both.
- **GitHub Pages is not live**: it isn't available for private repos on the free plan. The
  deploy job is `continue-on-error`, so the digest is unaffected. To go live: make the repo
  public, then Settings → Pages → Source: GitHub Actions.

## Milestone 9 — unattended run (started 2026-11-09)

- The `runs` state table records every run (status, timings, counts, NewConnect pending), and
  the Pipeline page shows it with a "within SLO" column (finished on the run date by 20:30
  Warsaw). Counting 30 consecutive trading days starts with the first scheduled run.
