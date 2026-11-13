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
  - A title announcing a plan, an intention, negotiations or a letter of intent is not a
    contract: it is `other`, unless an earlier rule (financing, M&A, buyback…) already
    matched, since those are news at the planning stage. Without this, "Planowana sprzedaż
    nieruchomości" became a "large contract" through the body's mention of a preliminary
    agreement. The hand-labelled titles agree (172 → 173 of 184).
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
- **Facts come only from parsed fields, the report title or the report's own text.** When
  insider details are missing, the page says so rather than guessing. The event page quotes
  the opening sentences of the report body and lists the money amounts it names, with the
  full text one click away; nothing is summarised or rewritten.
- **Today is one list, newest first (2026-11-13).** Every report of the digest, in the order
  it was published, each with its type, and for a price-sensitive type the verdict on what
  reports of that type did before, plus "too thin to trade" where it applies. The sentence of
  statistics and the walk-forward stay on the event page, one click away. The first version
  grouped events by verdict and by type, so the statistics appeared once per type; it meant
  scrolling past sections to find what had just happened. The type filter stays as an
  option, not as a grouping. The Telegram digest still leads with the kinds that moved prices.
- **Plain words first, statistics second.** Cards and event pages lead with a sentence
  ("after reports like this the stock did 4.7 pp worse than WIG over 60 sessions; worse in
  64% of 3,590 cases") and a one-line meaning of the verdict. q and the walk-forward stay on
  the event page, with a tooltip and a glossary entry; the Today page does not show them.
- **The stock itself.** Each event page shows the last close, the day-0 reaction against
  WIG, the result since entry, liquidity measured with the study's own rule (a "too thin to
  trade" warning when the statistics do not apply), a 90-session price line and the
  company's other reports. Market value is not shown: the lake has no share counts.
- **Archive track record.** Archived events show their result against WIG since entry, and
  the archive sums it up per verdict. Windows are mostly still open, so the page calls it an
  early read.
- **Pipeline counts belong on the Pipeline page.** Reports ingested, classified and retried
  moved there; the Today tiles count signal events and the types that earned after costs.
- **Telegram digest:** header, counts, up to five items, two buttons, a health line and the
  disclaimer. A failed job or a weekday without GPW session data sends an alert instead.
- **Keepalive:** GitHub disables schedules in repos without recent commits. The nightly run
  commits `digests/YYYY/MM/DD.md` to `main` in addition to data commits on `data`.
- **Exchange holidays are known in advance** (`gpw_calendar.py`). GPW closes on the Polish
  public holidays and also on Good Friday, Christmas Eve and New Year's Eve; for 2026 that is
  ten weekdays (GPW resolution 753/2025). The rule reproduces every closed weekday in the
  lake's WIG calendar; one-off closures (2013-04-16, 2018-01-02, 2018-11-12) are listed by
  hand, and a new one has to be added there. Before this, 11 November 2026 ran as a session
  day: the empty price archive made it "degraded", NewConnect prices "pending", the SLO
  missed, and Telegram got an alert. Now scheduled attempts on a holiday resolve to the last
  session and end in the gate; the watchdog expects no run; a run started by hand for a
  holiday collects reports, fetches no prices, renders the site, writes no digest file,
  sends nothing and records "GPW closed: <holiday>" with status ok. The holiday's reports go
  into the next session's digest, as before. The gate runs the calendar with the runner's
  own `python3` (standard library only), before the project is installed.
- **Freshness SLO:** digest sent by 20:30 Warsaw time on every trading day. The `runs` table
  records each run and the Pipeline page shows whether it met the SLO; `watchdog.yml` sends
  an alert if no successful run exists by 20:15.

## Stock of the week

- **A forward-looking pick, judged like the event studies.** The event pages say what
  happened after a kind of report. The pick page names one liquid WIG stock for the coming
  weeks and shows all of it: the score's parts, the stock's year against WIG, its reports, the
  runners-up, every weight, the out-of-sample curve against two baselines, and the live record.
  Its first line is the verdict on the model's own out-of-sample record. When that is "no
  edge", the stock is called the model's top score, not the best stock.
- **Ridge on ranks, not a black box.** With 20-odd correlated features and a few hundred
  weeks, a linear model on cross-sectional ranks is hard to overfit and explains itself: a
  score is a sum of feature × weight. Ranks make the fit indifferent to units and outliers.
- **Weekly, with a switching rule.** The user wanted a weekly pick; the target is 20 sessions
  because shorter horizons cannot pay a 2 % round trip. The pick is kept unless the new leader
  beats it by more than that cost. Without the rule, the momentum baseline swaps almost every
  week and loses most of its capital to costs.
- **The ranking and the pick are reported separately.** One stock is noisy; the rank IC and
  the top-10 basket show whether the ordering works even when a single pick does not.
- **A live record that is never rewritten.** `state/picks.parquet` gets one row on the first
  session of each week; a rerun of that day leaves it alone. The backtest can be re-run and
  can flatter; the live record cannot.
- **No AI for now.** The model reads the kind of each current report, not its text. Reading
  the text with a language model (in Actions) was considered and set aside by the user. A
  model trained on later data may also remember how older stories ended, so any such
  feature would have to prove itself on the live record.
- **The pick never blocks the digest.** It runs after the study; a failure is logged.

## One source of truth (decided 2026-11-12)

- **One forecast per stock, not one opinion per page.** Until now the site had two answers
  about the same stock: event pages said what a kind of report did on average, and the pick
  said which stock should beat WIG. They could contradict each other. The site moves to one
  layered stack. Every number a page shows comes from one row of it, and a view never
  computes a number of its own.

  ```
  L0  RAW        bronze, immutable
                 stock prices · NC file · 56 GPW indices · ESPI/EBI reports · MAR forms
                   │
  L1  CLEAN      silver, dbt-tested
                 adjusted returns · sessions · events (known_at) · WIG, size, TBSP indices
                   │
  L2  STATE      one row per (isin, date); every column known at that date's close
                 liquidity tier (one rule for everything)
                 σ GARCH ──┬──► cost (spread, σ, turnover)
                           ├──► β Kalman vs WIG · TBSP ──► residual return
                           └──► surprise = residual return / residual σ
                 WIG returns ──► regime probability
                 signal effect = walk-forward event-study mean, shrunk by its evidence
                 trend · risk · liquidity · news features
                   │
  L3  FORECAST   one per (isin, date): THE answer
                 ridge on ranked state ─► score ─► expected excess = score × σ
                 − cost ─► net expected;  conformal band on errors / σ, per regime
                 the contribution of every term
                   │
  L4  DECISION   basket weights = f(net expected, σ, residual correlations)
                 headline pick = largest weight
                   │
  L5  VIEWS      Today · event page · pick page · Telegram: render L3/L4 rows only
                   │
  L6  LEDGER     append-only: forecast and band frozen at the date ─► outcome
                 ─► coverage · rank IC · net result ─► calibration of later dates only
  ```

- **Rules of the wiring.**
  - Data flows down only. The one loop is the ledger, and it informs later dates only,
    after the same purge as the walk-forward.
  - Each input moves each quantity once. σ scales the expected excess, normalises the band
    and drives the cost: the same σ in all three. The regime enters the forecast and the
    band calibration; the basket is not de-risked a second time for it.
  - Modules do not call each other. Each writes its columns into the L2 table and reads only
    what is above it, so each can be tested, replayed point-in-time and dropped on its own.
  - The backtest and the nightly run share the same code path, the cost function included.
  - Diagnostics are evidence, not opinions: signal verdicts, GARCH fits and regime charts
    explain the forecast on the methodology and pipeline pages. They are not a second answer
    on Today.
  - A module joins the forecast only if the walk-forward with it beats the one without it
    out of sample. Every variant tried counts as a trial in the deflated Sharpe ratio.
- **One source of truth is also one point of failure.** A wrong σ would reach every number.
  So every L2 column has tests of its own, and every estimator has a fallback that is flagged
  in the row: σ from EWMA (RiskMetrics, λ = 0.94) when a stock has too little history for a
  GARCH fit; β from the trailing 250-session regression before a Kalman filter can start.
- **Fitted the way the pick is fitted.** GARCH, Kalman and regime parameters for year Y are
  estimated only on sessions before Y began, then frozen; the filters run day by day through
  Y with those parameters. A state value at date t uses returns up to t and parameters from
  before t's year. Filtered regime probabilities are used, never smoothed ones: smoothing
  uses the future.
- **Written in numpy, not with `arch` or `hmmlearn`.** GARCH(1,1) is fitted by Gaussian
  quasi-likelihood on a fixed grid of (α, β) with variance targeting, for all stocks at once.
  A grid has no convergence failures and no optimiser tolerances, so the output is the same
  bytes on every machine, which the reproducibility test needs. The Kalman filter and the
  two-state HMM are a few dozen lines each. No new dependency enters the lock.
- **What the first run on 2015–2018 changed.**
  - The regime model reads WIG alone. With the size spread (sWIG80 minus WIG20) as a
    second input, EM split days rather than periods: each state's chance of lasting another
    session was about 0.5. On WIG alone the states last (0.99 calm, 0.95 stress) and mark
    the right months: about 5 % stress through 2017, 37 % in February 2018, 60–70 % in
    November–December 2018. A fit whose states do not last is now rejected.
  - Stock variances have a floor of (0.5 % a day)². Without it, a stock whose price had not
    moved for weeks got a near-zero variance and its next tick a "surprise" of 20σ (the
    surprise of thin stocks had a standard deviation of 22). Indices keep no practical
    floor: TBSP moves about 0.2 % a day.
  - The Kalman filter carries its state from year to year. Restarting it each year from the
    prior 750 sessions back was not enough warm-up for a slowly drifting beta, and betas
    jumped on 1 January (1.41 to 1.20 on a synthetic test).
  - The likelihood picks the fastest beta drift on the grid (0.02 a day) in most years,
    because a fast beta absorbs fat-tailed noise. The grid stops there on purpose.
  - On the tradable stocks the surprise has a standard deviation of 1.09: the forecasts'
    scale is right, and the tails are a little fatter than normal.
- **Asymmetric GARCH (GJR, EGARCH) was left out.** It adds a parameter for the leverage
  effect; on 100-odd liquid stocks and a 20-session horizon the gain is small, and every
  extra parameter widens the grid. It can be tried later as one more trial.
- **No sector benchmark yet.** The lake has every GPW sector index (WIG-BANKI, WIG-PALIWA, …),
  but the GPW share archive carries no sector per stock, and a hand-made mapping of 400
  issuers would go stale. Rates (TBSP) are a factor instead, and `gold/index_returns` keeps
  WIG20, mWIG40 and sWIG80 for features; a sector source can add a column later without
  touching the rest.
- **Order of work:** L2 (σ, β, residuals, surprise, regime, liquidity, cost) first, then the
  forecast reads it (L3), then bands, basket and the live ledger. Until a layer is wired, the
  site keeps showing what it shows today.
- **The forecast reads the state (L3, 2026-11-12).**
  - Costs are the measure, not a variant. Every model is charged each stock's own round
    trip, in the backtest and the live record alike; comparing variants under different cost
    rules would compare accounting, not forecasts. The hysteresis asks the leader to beat the
    held stock by the leader's own cost.
  - Three nested variants are walked forward: the original 22 features, plus the five state
    features, plus σ scaling of the expected return. The one with the best out-of-sample
    rank IC of its *expected return* is adopted, ties to the simpler one. The IC is taken on
    the expected return, not the bare score, because σ scaling changes which stock comes
    first but not the order of scores. All 12 variant × penalty runs and the momentum
    baseline are trials of the deflated Sharpe ratio.
  - First result (out of sample 2018, 49 weeks): rank IC 0.167 (base), 0.172 (state
    features, adopted), 0.171 (σ scaling, rejected). The gain is small, as expected: the
    GARCH σ is close to the 60-session volatility already in the model. Both of the first
    two variants held PKOBP all year (one purchase); the scaled one held another stock and
    ended +4.2 % against −2.2 %, a difference of one stock's year that the rule rightly ignores.
  - The band is split conformal and the page's "bad case" is its lower edge. Before, the
    page worked out its own bad case from the stock's past year of 20-session windows: a
    second, backward-looking answer next to the forecast. In 2018 the 90 % band held 86.6 % of
    the outcomes; it was calibrated on calmer 2017.
  - **Bands per regime (Mondrian conformal).** Errors are grouped by the regime of their
    week and each regime gets its own percentiles. Two changes made that possible:
    - Calibration uses every training year, each scored by a model fitted on the weeks
      before it, instead of only the last year. Stress is rare (about 5 % of 2017), so a
      last-year-only calibration would almost never contain stress weeks; years like 2020
      and 2022 will, once the backfill reaches them.
    - A regime needs 8 weeks of errors for a band of its own; until then the pooled band is
      used and the backtest says so (`band = all`).

    On a synthetic market whose stocks are 2.5 times as noisy in stress, with a σ that does
    not know it, the pooled band held 75 % of stress-week outcomes and the stress band 89 %.
    On the real lake nothing changes yet: there are no stress weeks to calibrate on and none
    to test on, and calm weeks hold 86.3 %. The remaining gap is in calm weeks, so it is not
    the regime; the live ledger's own coverage is the next correction.
- **The ledger closes the loop (L6, 2026-11-12).**
  - Every scored stock's forecast and band is frozen weekly in `state/forecasts.parquet`.
    One row per stock, not only the pick: one stock a week would take years to say anything
    about coverage.
  - The band's miss rate adapts to realized misses (adaptive conformal inference). It reacts
    to whatever makes the band wrong, the regime or anything else, and needs no stress weeks
    in the calibration set. It moves only on weeks whose outcome is known, 20 sessions
    later, so it is as point-in-time as the rest.
  - The history it learns from is the walk-forward's out-of-sample weeks until the first
    live week, then the ledger's. The backtest is recomputed every night and could change;
    the ledger cannot, so once it exists it is the one that counts.
  - The step (0.05) is a prior. On 2018 a step of 0.1 would have scored a little better
    (88.4 % against 87.8 %), and choosing it from that would be tuning on the test year.
  - On a synthetic market that gets steadily noisier, a fixed band held 78.7 % and the
    adaptive one 88.2 %. On 2018: 86.5 % fixed, 87.8 % adaptive.
  - Calibration and coverage now use outcomes before winsorizing: the ledger can only see
    real outcomes, and the backtest must be measured the same way.
- **The decision layer (L4) and the event pages (L5, 2026-11-12).**
  - The basket is its own module and reads only what the forecast wrote for each week:
    expected excess, cost and σ. It forecasts nothing, so a better forecast improves it
    without touching it.
  - Names must beat their *own* cost to enter, and a held name stays while it is in the
    best 20. Without that buffer, rank noise near the 10th place turns into trades.
  - HRP rather than mean-variance: ten names and 120 sessions give a covariance matrix that
    is too noisy to invert, and HRP never inverts it. It is shrunk halfway to its diagonal
    for the same reason. Residual returns, not raw ones, because every name shares WIG.
  - Unfilled weight stays in WIG instead of being spread over fewer names: a basket of two
    is not a diversified basket, and WIG is what the result is measured against.
  - First result (2018): −2.8 % after costs, with 3.8 names and 38 % invested on average.
    Before costs it was about +0.9 %; weekly turnover of 13 % cost 3.6 %. The single pick
    did −2.2 % and the universe −14.2 %. Neither beats WIG, and the page says so.
  - **The basket is the decision (decided 2026-11-12).** The stock of the week is the
    basket's largest position, the headline verdict is the basket's, and its weekly record
    is the live record. The single pick is still walked forward and shown, as a comparison.
    - Why: the model's evidence is in the ranking (rank IC 0.17), not in any one name. In
      2018 the single pick held one stock all year, so its result says more about that stock
      than about the model. A basket of the best names is where a ranking can pay.
    - Why now: the single pick launched on 2026-11-11 and had no live week yet, so nothing
      recorded was lost. `state/picks` is no longer appended to.
    - The largest position is not the highest forecast: risk parity gives steadier stocks
      more weight, and the page says so. In a week when nothing beats its cost the basket
      stays in WIG and the headline is the top score, flagged as such.
    - How often a top-ranked stock "wins" its week, out of sample in 2018 (49 weeks): the
      model's top stock beat WIG in 45 % of weeks (an average liquid stock: 47.5 %) and over
      its 20 sessions in 41 %; it was never the week's best stock; the top 10 names beat WIG
      52 % of the time. A stock's 20-session σ is about 9 % against a top forecast of about
      2 %: one name in one week is mostly noise, and only a spread of names over many weeks
      can show what the ranking is worth.
  - Event pages now read the shared layers: liquidity tier, the day-0 surprise (the residual
    move in the stock's own σ), the 20-session σ and the round-trip cost from the market
    state, and the stock's forecast, band, rank and the part of it that comes from reports
    from the pick's scores. The page's own liquidity rule is only a fallback when the market
    state is missing. σ, cost and surprise are not shown for a stock too thin to trade: a
    stale price makes them meaningless. A forecast is shown only when it was computed for
    the same as-of date as the page.
  - If the market state fails, the nightly run removes any stale table first, so the pick
    falls back to its base model at flat costs and records `uses_state = false`; it never
    reads yesterday's state.
  - The deflated Sharpe ratio is still empty on the pick page. It was before this change as
    well; a weekly series with one purchase a year gives it little to work with.

## Report effects, the day's range and fundamentals (2026-11-12)

- **Report kinds as a forecast input.** Each kind's 20-session event-study result becomes a
  point-in-time input (`signal_effect.py`, a market-state column): estimated each year
  only from events whose trade had closed before it, standard errors clustered by stock and
  month, and shrunk in two steps.
  - The first version shrank each kind towards 0 and let through what all kinds share: in
    2016 every kind showed +2 to +4 %, in 2026 every kind about −1 %. That is a drift common
    to stocks with news against a large-cap index, not a property of any kind. Kinds are now
    shrunk towards the pooled mean, and the pooled mean towards 0 by its own t-statistic
    (kept share `1 − 1/t²`).
  - Result on 2015–2018: the kinds do not differ beyond noise (τ² = 0), and what is left is
    a common −0.9 % over 20 sessions after any report, kept at about 80 % (t ≈ −2.2). This
    agrees with the study's own verdicts ("noise").
  - Repeated filings of one kind (an insider filing daily) count once, from the latest.
  - In the forecast it is its own variant (`state_signals`). It did not beat the model
    without it out of sample (rank IC 0.168 against 0.170), so it is not used; it stays wired
    and will be tested again as the backfill adds years. Event pages show the shrunk effect
    as evidence.
- **The day's range in the volatility forecast** (GARCH-X). The innovation is
  `(1 − w)·r² + w·rv`, with `rv` the overnight move squared plus the Garman–Klass intraday
  variance, and `w ∈ {0, ½, 1}` fitted with α and β by the same likelihood. The overnight
  move is the adjusted return minus the open-to-close move, so corporate actions cannot
  inflate it. Out of sample on 2017–2018, tradable stocks: the QLIKE loss of the next day's
  variance fell by 0.018 a day (Diebold–Mariano t = −4.5 with Newey–West errors over 483
  days), and the range version was better on 65 % of days. The fit uses the range for 56 % of
  stocks. Downstream it is small so far: band coverage 87.8 % → 88.0 %; the pick's IC and
  the basket moved within noise.
- **Fundamentals: the start** (`esef.py`). The exchange's copies of annual reports are
  behind bot protection and the periodic-report pages hold no figures, so ESEF filings come
  from XBRL International's repository and LEIs are mapped with GLEIF (SOURCES § 11–12).
  - Only consolidated headline facts are kept (revenue, gross and operating profit, net
    profit, assets, liabilities, equity, cash, operating cash flow, EPS), for every period a
    filing reports, so the prior year's comparatives come along.
  - `known_at` is when the repository added the filing. It is later than the issuer's own
    publication, so the data can only look older than it was. Matching each filing to its
    ESPI annual-report timestamp would make it exact; that is a later step.
  - The repository's Polish coverage ends in July 2024 (fiscal 2020–2024). It is enough to
    test value, profitability and investment features once prices for 2021–2024 are in the
    lake, not to run them live; a live source for fiscal 2024–2025 is still to be found.
  - Not wired into the forecast yet: the lake has no prices for the years the filings cover.

## Known limitations

- Insider trade details (side, volume, price) are missing for reports that no portal
  copies; the exchange's own copy of the notification PDF is not retrievable.
- Size of an insider trade as a share of free float is always missing: there is no free-float
  source.
- An ESPI cross-check of detected corporate-action factors (split and dividend reports vs
  factors) is not implemented yet.
