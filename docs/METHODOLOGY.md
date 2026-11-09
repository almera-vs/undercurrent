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
  `insider_tx:buy|sell|unknown`. The side comes from the MAR notification form when a portal
  copy exists (only transactions in the issuer's shares count; a form with both purchases and
  sales is `mixed`), otherwise from the report text. Pledges, gifts (`gift`), incentive-plan
  grants (`incentive`) and mixed or unstated sides count as `unknown`: they are not one-way
  market trades.
- **`known_at`** is the publication timestamp on the exchange page. It is the earliest moment
  Undercurrent could have known the report.
- **Day 0** is the first GPW session whose close (17:05 Warsaw) is after `known_at`. A report
  published at 16:30 has day 0 = that day; one at 17:30 has day 0 = the next session.
- **Entry** is the close of the first session after the evening digest that first carries the
  report. The digest goes out after each session (by 20:30 Warsaw) and covers everything
  published since the previous one, so:

  | published | day 0 | digest | entry |
  |---|---|---|---|
  | Tue 10:00 | Tue | Tue evening | Wed close (day +1) |
  | Tue 18:00 | Wed | Tue evening | Wed close (day 0) |
  | Tue 21:00 | Wed | Wed evening | Thu close (day +1) |
  | Sat 12:00 | Mon | Mon evening | Tue close (day +1) |

  Holidays work the same way through the session calendar. Whatever the price does before
  the entry close is not something a reader of the digest could have traded, so it is left
  out of the statistics and the walk-forward. Entering at the next open would capture more,
  but adjusted opening prices are not modelled; the close is the conservative choice.

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
- **A daily return exists only between consecutive sessions.** When a stock does not trade
  for a few sessions (common on NewConnect), the move across the gap spans several sessions
  and is left out rather than booked as one day; the market-model regression and the event
  windows treat those sessions as missing. The gates still compare the move since the
  previous close with the exchange's published change.

## 3. Event study

- **Benchmark:** WIG.
- **Market model:** `r_i,t = a + b · r_WIG,t + e`, OLS on sessions [−250, −30] relative to
  day 0, requiring ≥ 120 valid returns (otherwise the event is skipped, not imputed).
- **Abnormal return:** `AR_t = r_i,t − (a + b · r_WIG,t)`.
- **Windows:** abnormal returns from the entry close over the next 1, 5, 20 and 60 sessions,
  (entry, entry+h]. The chart path stays relative to day 0 ([−5,+20]), so the reaction
  before entry is visible there. An event counts for a window only if every day of it is
  known; events whose entry would be more than 5 sessions after day 0 are skipped.
- **One event per (signal, stock, day 0).** Several reports about the same news, or a report
  and its correction, count once.
- **Statistics per (signal key, horizon):** n, mean and median CAR, hit rate (share of
  CAR > 0), t-statistic and two-sided p-value of the mean, and a 95 % percentile bootstrap CI
  (2,000 draws). Both treat **each stock as a cluster**: the t-statistic uses a cluster-robust
  (CR1) standard error with G − 1 degrees of freedom, and the bootstrap resamples whole stocks.
  Repeated filings on one stock (an insider buying every day for a week) have overlapping
  windows and are not independent; on a simulated market with bursts of 8 daily filings per
  stock and no real effect, the plain t-test called 53 % of runs significant at 5 %, the
  clustered one 7 %. The seed is derived from SHA-256 of `(20261109, signal key, horizon)`, so
  results are reproducible and independent of row order. Fewer than 10 events or fewer than 5
  stocks: no statistics are shown.
- **Multiple testing:** Benjamini–Hochberg q-values across every (signal key, horizon) tested
  that night.

## 4. Walk-forward test (does it survive trading?)

- Expanding yearly windows. Before each calendar year Y, the rule is chosen **only from trades
  that had exited before Y began** (a December event's 60-session trade is still open in
  February, so it cannot inform the rule for that year): direction (long/short) and holding
  period h ∈ {1, 5, 20, 60}, ranked by the t-statistic of the mean net return. Ranking by raw mean favours the noisiest horizon by
  chance (we saw this happen on synthetic data). If no candidate had a positive past net mean,
  the signal is not traded in Y. At least 30 past events are required.
- **Trade:** enter at the entry close, exit h sessions later. The return is the abnormal
  return over (entry, entry+h] (market-hedged), minus **0.3 % round-trip costs**.
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
| `holds_up` | best q across horizons < 0.05, at least 10 walk-forward trades, mean net return > 0 |
| `eaten_by_costs` | best q < 0.05, at least 10 walk-forward trades, not positive after costs |
| `untested` | best q < 0.05 but fewer than 10 walk-forward trades so far |
| `noise` | statistics exist, no horizon significant |
| `too_few` | fewer than 10 events (or 5 stocks) at every horizon |

All are shown on the site. Losers are never hidden. Cards, event pages and the digest show
the numbers of the horizon behind the verdict: the holding period of the latest walk-forward
rule for `holds_up` / `eaten_by_costs`, the most significant horizon for `untested`, and 20
sessions otherwise.

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

- Insider trade details (side, volume, price, value) depend on a portal copy of the
  notification form. Parkiet's copies end on 2026-10-26 and the listing portals reach back
  about three weeks, so reports no portal carries fall back to the report text (see
  DECISIONS). Size as % of free float is always missing: there is no free-float source.
- 2026-format EBI reports (NewConnect) keep their body only in PDF attachments. Those PDFs are
  downloadable from `newconnect.pl/ebi/files/…` (unlike ESPI attachments), but they are not
  parsed: EBI current reports feed no signal, so sub-typing falls back to the title.
- Dividends under 0.2 % yield are ignored by the factor tolerance.
- Survivorship: the price universe is built from archive files, which include delisted
  companies for the days they traded, so there is no survivorship filter. Events of companies
  with < 120 sessions of history are skipped.
