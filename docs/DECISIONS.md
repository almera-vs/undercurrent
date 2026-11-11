# Design decisions

Why Undercurrent is built the way it is. Each entry states the choice, the reason, and what
it costs. Method details (event study, walk-forward, verdicts) are in
[METHODOLOGY.md](METHODOLOGY.md); every endpoint is described in [SOURCES.md](SOURCES.md).

## Tooling

- **Python 3.12 via uv**, every version pinned in `uv.lock`. CI runs `uv sync --locked`, so a
  stale lock fails the build.
- **dbt is a separate dependency group** (`uv sync --group dbt`). dbt-core pulls in about 40
  packages; keeping it out of the default group keeps unit-test CI fast. The nightly job
  installs it.
- **argparse for the CLI**, not click/typer: one dependency fewer and nothing missing.
- **Excel readers:** `xlrd` for GPW `.xls` and older NewConnect `.xls`, `openpyxl` for
  NewConnect `.xlsx`. The official files come in exactly these formats.

## Fetching

- **One HTTP client** (`undercurrent.fetch.PoliteClient`) is the only code that touches the
  network. It keeps at least 1 s between requests plus 0–0.5 s jitter, sends a User-Agent
  that names the project (`undercurrent/0.1 (+repo URL)`), revalidates with ETag /
  If-Modified-Since against `.cache/http`, and raises `SourceBlockedError` when it receives a
  bot-protection page. It never retries around a block.
- **Sources are checked from a GitHub runner**, not only from a home connection, because
  datacenter IPs are often treated differently. `undercurrent probe` and the manual
  `probe-sources` workflow test every endpoint.
- **Bot-protected endpoints are not used.** PDF attachments of ESPI reports on gpw.pl and
  newconnect.pl, and PAP's espiebi.pap.pl, answer automated clients with a JavaScript
  challenge. Getting past it would mean a headless browser or challenge solving, so these
  endpoints are left alone.
- **Insider forms come from portal copies.** Parkiet, Bankier and BiznesRadar republish ESPI
  reports with their own copies of the attachments, served without bot protection and
  allowed by their robots.txt. Three sources rather than one: each has gaps (Parkiet's copies
  stop on 2026-10-26; some older Bankier links are dead), and one portal being down must not
  stop the others. Report pages and PDFs are fetched once and not cached on disk; only the
  extracted facts are stored.

## Report identity and storage

- **Two sites, one key.** gpw.pl lists main-market issuers only; NewConnect reports come from
  newconnect.pl through the same handler. `geru_id` sequences differ per site, so the primary
  key is `report_id = "{site}:{geru_id}"` (for example `gpw:500131`, `nc:244788`).
- **Partitions by publication date** (Warsaw) for reports and by session date for prices,
  under `bronze/{table}/date=YYYY-MM-DD/part-N.parquet` on the `data` branch. A file is never
  rewritten: a report published after the nightly run lands in the next `part-N` of its own
  date. A run that finds nothing new writes nothing, so re-runs are idempotent.
- **Only bronze and small state tables are committed.** Silver and gold are rebuilt every
  run in `.work/`. Committing a nightly rewrite of growing tables would bloat git history.
- **Monthly compaction** (`compact.yml`) merges daily partitions older than two months into
  `month=YYYY-MM/part-0.parquet` with a `_partition_date` column. Ingest checks both layouts
  before fetching, so compaction never triggers re-downloads, and late rows for a compacted
  month land as a daily part next to the month file.

## Ingest

- **Report pages are fetched only for current reports (`Bieżący`).** Periodic reports are
  classified from the listing, which saves about 20 % of requests.
- **Three page templates** are parsed: legacy ESPI (labelled table rows), legacy EBI (free
  text ending in `Podstawa prawna:`), and the 2026 NewConnect EBI template, whose body lives
  only in PDF attachments. Those EBI PDFs are downloadable but not parsed, because no EBI
  report feeds a signal.
- **Failed pages are retried, not dropped.** They stay out of bronze and are tracked per
  month in `state/failed_reports`; the next run retries them.
- **Source text quality:** some ESPI pages lack Polish letters at the byte level
  (`Rozporzdzenia`). Classification matches diacritic-free tokens, so these still classify.
- **NewConnect price file timing.** `NC_YYYYMMDD.xlsx` appears between 16:47 and 18:50 UTC,
  often after the digest is due. The scheduled run does not wait for it: it publishes with
  NewConnect prices marked pending (not a degraded run), and the next run back-fills any
  missing price day from the last 10 sessions. Nothing in tonight's digest depends on
  tonight's NewConnect closes. A run started by hand can still poll the file every 5 minutes
  until 20:05 Warsaw time (`wait_nc`).
- **Schedule.** GitHub delivers this repository's scheduled events 5 to 7 hours late
  (measured on 2026-11-09 and 2026-11-10, for both workflows), so a single cron at the right
  time produced a digest near midnight. `daily.yml` therefore asks for an attempt every 30
  minutes from 09:17 to 21:47 UTC, and `scripts/nightly_gate.sh` lets one through: the first
  that starts after the 18:30 Warsaw cut-off, or any that finds the previous trading day's
  digest missing. A digest file on `main` is the marker that a day is done, so a failed run
  is retried by the next attempt. If the lag disappears the same setup still runs once, at
  18:47.

## Classification and insider fields

- **Classification is a pure function** (`classify.py`): legal basis first, then title/body
  fallbacks, then keyword sub-types for inside information. Every label records the rule
  that produced it (`class_rule`).
  - Art. 70 pkt 3 (list of ≥ 5 % holders at a general meeting) is not a threshold crossing;
    only Art. 69/69a and Art. 70 pkt 1 are.
  - An Art. 19 basis with no insider wording in the text is classified `other`
    (`basis:mar19:no_text_support`); issuers occasionally file unrelated reports under it.
- **Insider fields: the notification form first, the report text second.** For every
  insider report the nightly run looks for a portal copy (`portals.py`): Parkiet through its
  monthly sitemaps, Bankier and BiznesRadar through their report listings. A page matches
  only if it was published within 10 minutes of the exchange's timestamp, names the same
  issuer (official or short name) and has a similar title; BiznesRadar's report number must
  also agree. The PDFs on the matched page are parsed as the fixed EU form (`forms.py`):
  person, position, amendment flag, issuer, LEI, and per transaction the instrument, ISIN,
  nature, price/volume rows, aggregates, date and venue. Every transaction is stored in
  `bronze/insider_forms`. The report-level fields use only transactions in the issuer's
  shares; Polish forms win over English translations, and corrected forms ("zmiana")
  replace the originals. Where the form is silent, the field parsed from the report text
  stays. Nothing is inferred.
- **Retries and history.** Every nightly run checks insider reports of the last 21 days (as
  far back as the listing portals reach); reports that already have forms cost no
  requests. Older reports without a copy stay in `state/insider_forms_pending` for 14 days.
  After publishing, the nightly run spends up to 20 minutes on older months, newest first,
  recording finished months in
  `state/insider_forms_months`; the same is available as `undercurrent forms`.
- **Portal quirk:** Parkiet's sitemap stores the Warsaw publication time as if it were UTC
  and then renders it in local time (14:27 Warsaw appears as `16:27+02:00`). Its UTC reading
  is the real Warsaw time.
- **Sides:** `buy`, `sell`, `mixed`, `pledge`, plus `gift` and `incentive` (incentive-plan
  grants). Only `buy` and `sell` get their own signal key; everything else is
  `insider_tx:unknown`, because gifts and grants are not market trades.
- **Report-text accuracy harness:** `tests/fixtures/labelled_pdfs/` holds hand-labelled report texts and
  `scripts/score_insider.py` scores the extractor. CI fails below 98 % field-level accuracy.
  The current labels were also used to develop the rules, so the number is optimistic; a
  held-out set is needed for an unbiased figure. Person names in fixtures are fictional.
- **Entity resolution:** the listing carries the ISIN, which resolves nearly every report
  exactly. A rapidfuzz name match and a review list exist for the rest, with manual
  overrides in `seeds/issuer_overrides.csv`.
- **Number and date parsers** are property-tested with Hypothesis over Polish
  (`1 234,56`), dotted (`1.234.567,89`) and English (`1,234.56`) formats and ISO,
  `DD.MM.YYYY` and `5 listopada 2026` dates.

## Prices and corporate actions

- **Adjustment factors come from the exchange.** On an ex-date the exchange sets the
  session's reference price away from the previous close (dividend, split, rights issue,
  reverse split). NewConnect publishes the reference price; for GPW it is recovered from the
  published change, `reference = close / (1 + change/100)`. `factor = reference / prev_close`
  applies to all earlier prices. GPW adjusts the reference for dividends, so adjusted returns
  are total returns.
- **Factors within 0.2 % of 1 are rounding** and treated as 1. The published change has two
  decimals, so rounding noise is far smaller. The cost: dividends yielding under 0.2 % are
  ignored (at most 0.2 % on one day).
- **Overrides:** `seeds/corporate_action_overrides.csv` replaces the factor for an
  `(isin, ex_date)`; every row needs a comment.
- **Jump gate.** The first version passed any move the exchange's published change
  "confirmed". The adjusted return is built from that change, so the gate could not fail: on
  2015–2016 all 1,586 moves above 35 % were confirmed. It now asks for evidence the change
  does not contain. `assert_no_unexplained_jumps`: a move above 35 % needs traded volume.
  `assert_no_reversed_factors`: a large factor undone within a month is a garbled price.
  `assert_nc_reference_matches_change`: NewConnect's two published columns must agree.
  NewConnect has genuine moves above 35 % almost daily, so there is no cap on size and no
  hand-kept list. `assert_adjusted_returns_reconcile` stays as a check of the model's own
  arithmetic. All run in the nightly `dbt build` and in CI against the real `data` branch.
- **Quarantine instead of a guess.** A move above 35 % on an ex-date gets no return until an
  override row settles the factor. Failing the build would need a hand review of every such
  day; trusting it would put the least certain returns into the study.
- **No factor across a hole in the lake.** The first session after missing months compared
  its reference with a close from years earlier and rescaled all older prices by the result.
  Returns were unaffected, but adjusted levels were wrong. Such a row now has factor 1.
- **Tradability filter.** Events count only for stocks with a median close of at least 0.50 zł
  and a median daily turnover of at least 10,000 zł over the 60 sessions before day 0
  (`MIN_PRICE`, `MIN_TURNOVER` in `study.py`). Without it the statistics were averages over
  tick bounce in grosz-priced stocks. The thresholds are a judgment: low enough to keep small
  caps a private investor does trade, high enough that one tick is not the whole move.
- **"Bad case" tile is a percentile.** The event page showed the single worst outcome in the
  sample, which is one stock's story and grows with the sample. It now shows the outcome only
  1 past event in 20 did worse than (for a short rule, the rise), one event per stock and day.

## Backfill

- About 90 reports per trading day since 2015 is roughly 265k report pages, about 80
  runner-hours at the polite request rate. `backfill.yml` runs one month per job,
  sequentially (`max-parallel: 1`), and pauses 17:30–19:30 UTC on weekdays so that combined
  traffic with the nightly job stays at one request per second. Failed-report state is per
  month, so the two jobs never write the same file.

## Event studies and walk-forward

- **Day 0** is the first session whose 17:05 close is after `known_at`.
- **Returns count from the entry close, not day 0.** A reader learns about a report from the
  evening digest, so the first close they can trade is the session after that digest. An
  intraday report's day-0 move (and, for most reports, the day +1 move) happens before then.
  Measuring from day 0 would credit the strategy with moves nobody using the digest could
  catch; on a synthetic market with a planted day +1 effect, the day-0 method reported it as
  tradable and the entry method correctly does not. Statistics, the walk-forward and the
  per-event table all use (entry, entry+h]; the chart keeps the day-0 view.
- **Reproducible and point-in-time:** `tests/reproducibility` requires an identical SHA-256
  of `signal_stats` over two runs; `tests/replay` recomputes as of past dates and fails on
  any input row known after the cut-off.
- **Walk-forward rules are ranked by t-statistic, not by mean.** On a synthetic market with a
  planted +3 % day-1 effect, picking the horizon with the best past mean chose 60-day holds
  (pure noise) and turned the effect into −0.6 % out of sample. Ranking by mean over standard
  error recovers it. Years in which no candidate was net-positive in the past are not traded.
- Short rules are allowed; METHODOLOGY notes that borrow is rarely available for small caps.
- **Review fixes (2026-11-09).** A line-by-line review of the statistics found that the
  formulas themselves are right (Benjamini–Hochberg matches statsmodels on 200 random cases;
  OLS, t-test and p-values match statsmodels/scipy; bootstrap coverage 95.3 % on iid data)
  but four things fed them wrongly:
  1. repeated filings on one stock were treated as independent, which made the t-test call
     an absent effect significant about half the time; standard errors and the bootstrap are
     now clustered by stock, and duplicate events are collapsed;
  2. the walk-forward could pick a rule using trades that had not exited yet; training now
     uses only trades closed before the year starts;
  3. a return across a trading gap was booked as a one-day return; it is now left out;
  4. "eaten by costs" and "noise" were shown when there was simply not enough data; there
     are now explicit `untested` and `too_few` verdicts.
  On the site, cards showed the 20-session number next to a verdict that came from another
  horizon; they now show the verdict's own horizon.

## Site and digest

- **Static site** on GitHub Pages: Today, Signals (one page per horizon, 1/5/20/60 days),
  event pages with an inline-SVG excess-return chart and bootstrap band, and a Pipeline page.
  Polish at the root, English under `/en/`; report titles are always shown as published.
- **Facts come only from parsed fields or the report title.** When insider details are
  missing, the page says so rather than guessing.
- **Telegram digest:** header, counts, up to five items, two buttons, a health line and the
  disclaimer. A failed job or a weekday without GPW session data sends an alert instead.
- **Keepalive:** GitHub disables schedules in repos without recent commits. The nightly run
  commits `digests/YYYY/MM/DD.md` to `main` in addition to data commits on `data`.
- **Freshness SLO:** digest sent by 20:30 Warsaw time on every trading day. The `runs` table
  records each run and the Pipeline page shows whether it met the SLO; `watchdog.yml` sends
  an alert if no successful run exists by 20:15.

## Known limitations

- Insider trade details (side, volume, price) are missing for reports that no portal
  copies; the exchange's own copy of the notification PDF is not retrievable.
- Size of an insider trade as a share of free float is always missing: there is no free-float
  source.
- An ESPI cross-check of detected corporate-action factors (split and dividend reports vs
  factors) is not implemented yet.
