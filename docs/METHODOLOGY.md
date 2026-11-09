# Methodology

How Undercurrent turns ESPI/EBI reports and daily prices into the statistics on the site.
Code: `src/undercurrent/study.py` (event studies), `dbt/models/gold/adjusted_prices.sql`
(prices), `src/undercurrent/classify.py` and `parse.py` (signals).

**This is a measurement tool, not a trading system. Not investment advice.**

## 1. Events

- Every current report gets a `signal_type` from its legal basis (then title/body rules):
  `insider_tx` (Art. 19 MAR; pre-MAR Art. 160 of the Trading Act), `holder_threshold`
  (Art. 69/69a, Art. 70 pkt 1 of the Public Offering Act), `inside_info` (Art. 17 MAR; pre-MAR
  Art. 56 ust. 1 pkt 1) with a keyword sub-type, `periodic`, `other`.
- Statistics are grouped by **signal key**: `inside_info:<subtype>`, `holder_threshold`,
  `insider_tx:buy|sell|unknown`. The insider side is only known when the report text states it
  (the PDF form is an ESPI attachment, bot-protected on both gpw.pl and newconnect.pl), so most
  insider events fall in `insider_tx:unknown`. Gifts (`gift`) and incentive-plan grants
  (`incentive`) are recognised but count as `unknown`: they are not market trades.
- **`known_at`** is the publication timestamp on the exchange page. It is the earliest moment
  Undercurrent could have known the report.
- **Day 0** is the first GPW session whose close (17:05 Warsaw) is after `known_at`. A report
  published at 16:30 has day 0 = that day; one at 17:30 has day 0 = the next session.

## 2. Prices and returns

- Raw daily closes from the official GPW quote archive (main market) and NewConnect daily
  statistics files.
- **Adjustment factors come from the exchange.** On an ex-date the exchange sets the session's
  reference price below or above the previous close (dividend, split, rights issue, reverse
  split). NewConnect publishes the reference price. For GPW it is recovered from the published
  change: `reference = close / (1 + change% / 100)`. `factor = reference / prev_close`;
  every earlier price is multiplied by the factor. Factors within 0.2 % of 1 are rounding.
  Manual overrides live in `seeds/corporate_action_overrides.csv`.
- Adjusted daily returns therefore equal the exchange's own published change, which makes them
  **total returns** (GPW adjusts the reference for dividends; verified on real dividends).
- Gates (fail CI and the nightly job): no adjusted daily move > 35 % unless an override exists
  or the exchange's published change confirms it, and every adjusted return reconciles with the
  published change to ±0.5 pp.
- Look-ahead note: back-adjusted *levels* use future factors, so the study uses only daily
  *returns*. A return on day t depends only on information known on day t.

## 3. Event study

- **Benchmark:** WIG.
- **Market model:** `r_i,t = a + b · r_WIG,t + e`, OLS on sessions [−250, −30] relative to
  day 0, requiring ≥ 120 valid returns (otherwise the event is skipped, not imputed).
- **Abnormal return:** `AR_t = r_i,t − (a + b · r_WIG,t)`.
- **CAR windows:** [0,+1], [0,+5], [0,+20], [0,+60]. The [−5,+20] path is used for charts.
  An event counts for a window only if every day of the window is known.
- **Statistics per (signal key, horizon):** n, mean and median CAR, hit rate (share of
  CAR > 0), t-statistic and two-sided p-value of the mean, and a 95 % percentile **bootstrap CI
  resampling events** (2,000 draws). The seed is derived from SHA-256 of
  `(20261109, signal key, horizon)`, so results are reproducible and independent of row order.
  Fewer than 10 events: no statistics are shown.
- **Multiple testing:** Benjamini–Hochberg q-values across every (signal key, horizon) tested
  that night.

## 4. Walk-forward test (does it survive trading?)

- Expanding yearly windows. Before each calendar year Y, the rule is chosen **only from events
  before Y**: direction (long/short) and holding period h ∈ {1, 5, 20, 60}, ranked by the
  t-statistic of the mean net return. Ranking by raw mean favours the noisiest horizon by
  chance (we saw this happen on synthetic data). If no candidate had a positive past net mean,
  the signal is not traded in Y. At least 30 past events are required.
- **Trade:** enter at the close of day 0, exit at the close of day +h. The return is the
  abnormal return over (0, h] (market-hedged), minus **0.3 % round-trip costs**.
- The out-of-sample result is the mean net return of all trades made this way.
- **Caveat:** the short side assumes stock borrow is available, which is unrealistic for most
  GPW/NewConnect small caps. A `short` rule that "holds up" is a statement about information,
  not a tradable strategy.
- **Deflated Sharpe ratio** of each signal's out-of-sample
  per-trade returns. The number of trials is every (signal × horizon × direction) considered,
  and the trial variance is the cross-signal variance of out-of-sample Sharpe ratios.

## 5. Verdicts

| verdict | rule |
|---|---|
| `holds_up` | best q across horizons < 0.05 **and** walk-forward mean net return > 0 |
| `eaten_by_costs` | best q < 0.05, walk-forward not positive after costs |
| `noise` | everything else |

All three are shown on the site. Losers are never hidden.

## 6. Point-in-time correctness

- Every input row (events, returns, benchmark) carries `known_at`. Price rows are known at
  17:35 Warsaw on their session (after the closing auction).
- `study.cut_inputs(..., as_of)` drops every row with `known_at > as_of`, and
  `assert_point_in_time` raises if anything slipped through. The nightly run uses
  `as_of = 23:59:59 Warsaw` of the run date.
- `tests/replay/` recomputes the statistics as of past dates and asserts that no input row is
  later than the cut-off and that event counts can only grow with time.

## 7. Reproducibility

- Inputs sorted deterministically, seeded RNGs, no wall-clock values in outputs (`as_of` is an
  input), fixed Parquet writer settings (zstd level 9, statistics on).
- `tests/reproducibility/` computes `signal_stats` twice and asserts identical SHA-256. The
  nightly log prints the SHA-256 of the file it wrote.

## 8. Known limitations

- Insider trade details (side, volume, price, value) are mostly missing because the PDF forms
  are blocked by bot protection (see DECISIONS). Size as % of free float is always missing:
  there is no free-float source within the constraints.
- 2026-format EBI reports (NewConnect) keep their body only in PDF attachments. Those PDFs are
  downloadable from `newconnect.pl/ebi/files/…` (unlike ESPI attachments), but they are not
  parsed: EBI current reports feed no signal, so sub-typing falls back to the title.
- Dividends under 0.2 % yield are ignored by the factor tolerance.
- Survivorship: the price universe is built from archive files, which include delisted
  companies for the days they traded, so there is no survivorship filter. Events of companies
  with < 120 sessions of history are skipped.
