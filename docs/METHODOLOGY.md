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
  report. Each session's digest covers everything published after the previous session's
  cut-off and up to its own: **18:30 Warsaw**. The nightly run starts as soon after that as
  GitHub delivers an attempt (see DECISIONS, "Schedule"). A report published later is in the next session's digest. The
  site and the study share this one rule (`study.digest_window`, `study.digest_day`), so:

  | published | day 0 | digest | entry |
  |---|---|---|---|
  | Tue 10:00 | Tue | Tue evening | Wed close (day +1) |
  | Tue 18:00 | Wed | Tue evening | Wed close (day 0) |
  | Tue 19:00 | Wed | Wed evening | Thu close (day +1) |
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
- Nothing is derived across a hole in the lake (months not backfilled yet): the first session
  after it has no factor and no return, because its "previous close" is years old.
- Gates (fail CI and the nightly job). The adjusted return is built from the published change,
  so agreeing with it proves only that the model's arithmetic holds
  (`assert_adjusted_returns_reconcile`, ±0.5 pp). The data gates use what the change does not
  determine: an adjusted daily move > 35 % must have traded volume
  (`assert_no_unexplained_jumps`); a large factor undone within a month is a bad print, not a
  corporate action (`assert_no_reversed_factors`); and NewConnect's published reference price
  must agree with its published change (`assert_nc_reference_matches_change`).
- **Quarantine.** A move > 35 % on the very day the exchange adjusted the reference cannot be
  split into "corporate action" and "market move" from one file. That day gets no return (7
  days in 2015–2016), so no event window may include it, until an override row settles it.
- Look-ahead note: back-adjusted *levels* use future factors, so the study uses only daily
  *returns*. A return on day t depends only on information known on day t.
- **Holes in the lake are not adjacency.** While the backfill is incomplete the calendar has
  holes (whole months, or years). Real sessions are never more than a week apart, so a longer
  step is treated as a hole: there is no stock or WIG return across it, and no estimation
  window, event window or trade may reach over it. A report whose day 0 would be more than a
  week after its publication is skipped.
- **A daily return exists only between consecutive sessions.** When a stock does not trade
  for a few sessions (common on NewConnect), the move across the gap spans several sessions
  and is left out rather than booked as one day; the event windows treat those
  sessions as missing. The gates still compare the move since the
  previous close with the exchange's published change.

## 3. Event study

- **Benchmark:** WIG.
- **Excess return** over a window is the stock's compounded return minus WIG's compounded
  return over the same sessions (buy-and-hold, market-adjusted): what holding the stock
  against the index earned. There is no fitted baseline. An earlier version fitted a market
  model (`r = a + b · r_WIG`) on sessions [−250, −30]; on real 2015–2016 data the fitted
  intercept averaged +10 bp a day on GPW and +22 bp on NewConnect, because stocks tend to
  rise before they have news, and subtracting it produced a spurious drift of −1.6 % over 19
  sessions for every signal type. Measured against WIG directly the same drift is −0.5 %
  (GPW) and −0.1 % (NewConnect). Compounding, rather than adding up daily differences,
  also keeps bid-ask bounce from accumulating.
- **Which stocks count.** Of the 60 sessions before day 0, at least 40 must have a return and
  the stock must have traded (volume > 0) on at least half of those; otherwise the event is
  skipped. A stock that did not trade on the entry session has no close to buy at, so the
  event is not counted for any holding period.
- **Only stocks a reader could buy.** Over the same 60 sessions the median close must be at
  least 0.50 zł and the median daily turnover at least 10,000 zł (sessions without a trade
  count as zero). Below 0.50 zł one 0.01 zł tick is a move of 2 % or more: a stock quoted at
  0.01–0.02 zł "returns" +100 % and −50 % on alternate days without anyone earning anything.
  Below 10,000 zł a day an ordinary order is the whole market. On 2015–2016 this leaves out
  about half of all events, most of them on NewConnect, and cuts 60-session outcomes beyond
  +100 % from 105 to 14. Both figures are measured before day 0 only.
- **Windows:** excess returns from the entry close over the next 1, 5, 20 and 60 sessions,
  (entry, entry+h]. The chart path stays relative to day 0 ([−5,+20]; its band is a
  bootstrap that resamples the same clusters as the confidence intervals), so the reaction
  before entry is visible there. An event counts for a window only if every day of it is
  known; events whose entry would be more than 5 sessions after day 0 are skipped.
- **One event per (signal, stock, day 0).** Several reports about the same news, or a report
  and its correction, count once.
- **Statistics per (signal key, horizon):** n, mean and median excess return, hit rate (share
  above zero), t-statistic and two-sided p-value of the mean, and a 95 % percentile bootstrap
  CI (2,000 draws). Both are **clustered two ways: by stock and by calendar month of day 0**.
  The t-statistic uses the two-way cluster-robust standard error of Cameron, Gelbach and
  Miller (variance by stock plus variance by month minus variance by their intersection),
  with degrees of freedom from the smaller cluster count; the bootstrap resamples stocks and
  months independently and multiplies the weights (pigeonhole bootstrap). By stock, because
  repeated filings on one stock (an insider buying every day for a week) have overlapping
  windows: on a simulated market with bursts of 8 daily filings per stock and no real
  effect, the plain t-test called 53 % of runs significant at 5 %, the stock-clustered one
  7 %. By month, because events in the same weeks share whatever moved small caps then. The
  seed is derived from SHA-256 of `(20261109, signal key, horizon)`, so results are
  reproducible and independent of row order. Fewer than 10 events or fewer than 5 stocks: no
  statistics are shown.
- **Multiple testing:** Benjamini–Hochberg q-values across every (signal key, horizon) tested
  that night.

## 4. Walk-forward test (does it survive trading?)

- Expanding yearly windows. Before each calendar year Y, the rule is chosen **only from trades
  that had exited before Y began** (a December event's 60-session trade is still open in
  February, so it cannot inform the rule for that year): direction (long/short) and holding
  period h ∈ {1, 5, 20, 60}, ranked by the t-statistic of the mean net return. Ranking by raw mean favours the noisiest horizon by
  chance (we saw this happen on synthetic data). If no candidate had a positive past net mean,
  the signal is not traded in Y. At least 30 past events are required.
- **Trade:** enter at the entry close, exit h sessions later. The return is the excess
  return over (entry, entry+h] (the stock held against WIG), minus round-trip costs: **2.0 %
  on GPW, 5.2 % on NewConnect**. That is one effective bid-ask spread plus 0.2 % commission
  each way. Spreads are estimated from daily high, low and close with the Abdi–Ranaldo
  estimator (`scripts/estimate_spreads.py`), over stocks that trade on at least half of the
  sessions and weighted by where the events are: about 1.6 % on GPW and 4.8 % on NewConnect
  in the history loaded so far. Most events are in small caps; a blue chip costs far less.
- The out-of-sample result is the mean net return of all trades made this way.
- **Caveat:** the short side assumes stock borrow is available, which is unrealistic for most
  GPW/NewConnect small caps. A `short` rule that "holds up" is a statement about information,
  not a tradable strategy.
- **Out-of-sample significance:** a one-sided test that the mean net return of those trades
  is above zero, clustered by stock and month like the in-sample test.
- **Deflated Sharpe ratio** of each signal's out-of-sample per-trade returns. A trial is every
  rule the walk-forward could have picked: one per (signal × horizon × direction). The hurdle
  comes from the number of trials and the variance of the per-trade Sharpe ratios across
  (signal × horizon) pairs; the short side mirrors the long side, so it adds a trial but not
  spread. With fewer than two pairs no ratio is reported. It is shown for information and does not enter the verdict.

## 5. Verdicts

| verdict | rule |
|---|---|
| `holds_up` | best q across horizons < 0.05, at least 10 walk-forward trades, mean net return > 0 and significantly so (one-sided p < 0.05, clustered by stock and month) |
| `unconfirmed` | best q < 0.05, at least 10 walk-forward trades, mean net return > 0 but not significantly |
| `eaten_by_costs` | best q < 0.05, at least 10 walk-forward trades, not positive after costs |
| `untested` | best q < 0.05 but fewer than 10 walk-forward trades so far |
| `noise` | statistics exist, no horizon significant |
| `too_few` | fewer than 10 events (or 5 stocks) at every horizon |

All are shown on the site. Losers are never hidden. Cards, event pages and the digest show
the numbers of the horizon behind the verdict: the holding period of the latest walk-forward
rule for `holds_up` / `unconfirmed` / `eaten_by_costs`, the most significant horizon for `untested`, and 20
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

- **Costs are one figure per market**, estimated from daily prices, not from quotes. A liquid
  stock is charged too much and an illiquid one too little, and the estimate should be re-run
  when more history is loaded. The in-sample averages (mean excess return, q) are before
  costs on both markets.
- **WIG is the only benchmark.** Small caps can drift against it for months; clustering by
  month widens the intervals for that, it does not remove it.
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

## 9. Stock of the week

Code: `src/undercurrent/pick_features.py` (features), `src/undercurrent/pick.py` (model).

- **Question.** Of the liquid main-market (WIG) stocks, which one has the highest expected
  result against WIG over the next 20 sessions? Only prices, turnover and current reports are
  available: no financial statements, share counts or market values, so no valuation.
- **Universe on a date.** Main market only, a year (260 sessions) of continuous history with
  returns on at least 200 of the last 252, and the study's buyability rule with a higher
  turnover floor: median close ≥ 0.50 zł and median daily turnover ≥ 100,000 zł over the
  last 60 sessions. On 2016–2018 that is 90–115 stocks a week.
- **Features** (all from sessions up to and including the rebalance close):
  - trend: 12-1 and 6-1 month momentum against WIG, the last month against WIG, distance
    from the 52-week high;
  - risk: 60-session volatility, 250-session beta and residual volatility, 250-session
    drawdown;
  - liquidity: median turnover, share of sessions without a trade, Amihud illiquidity;
  - news: signal events of the last 60 sessions by kind, insider net buying of the last 20
    sessions in days of turnover, corporate actions.

  Each is turned into a rank-normal score across the universe on that date, so units and
  outliers do not matter.
- **Target.** The compounded stock-minus-WIG return over the next 20 sessions, winsorized at
  the 1st/99th percentile per date and rank-normalized.
- **Model.** Ridge regression on the pooled weekly cross-sections. A stock's score is the sum
  of feature × weight, which the page shows term by term. A least-squares fit of the
  (winsorized) 20-session excess on the score turns scores into expected returns. The penalty
  (1, 10, 100 or 1,000) is chosen on the last year of the training history, by mean rank IC.
- **Walk-forward.** Expanding yearly re-fits. The model for year Y sees only weeks whose
  20-session target ended before Y began (the purge), and needs at least 40 such weeks.
- **The pick.** On the first session of each week the top expected excess is chosen. The
  current pick is kept unless the new leader beats it by more than the round-trip cost (2.0 %),
  and every switch, the first purchase included, pays that cost. The holding result runs from
  the rebalance close to the next one.
- **Baselines.** The same rule on 12-1 momentum alone, the equal-weighted universe, and, as a
  diagnostic, the model's ten best scores equally weighted before costs.
- **Verdict.** On the out-of-sample weeks:
  - "beats WIG after costs" needs a positive mean weekly net excess with a one-sided p below
    0.05, clustered by calendar month;
  - "positive, not confirmed" if the mean is positive but not significant;
  - "no edge" otherwise;
  - "too little history" under 26 weeks.

  The rank IC (with a moving-block bootstrap t), the deflated Sharpe ratio (trials: the four
  penalties and the momentum baseline), the drawdown and the switches are reported alongside.
- **With the market state (§ 10).** Five more features: GARCH σ of the next 20 sessions,
  Kalman betas to WIG and to bonds, residual momentum (12-1 months of the returns the betas
  did not explain) and the last 20 sessions' surprises. Every purchase pays the bought
  stock's own round-trip cost, and a switch must beat the held stock by that cost. Three
  nested variants are walked forward (the 22 original features; plus the state's five; plus
  an expected return in units of each stock's σ). The one with the best out-of-sample rank
  IC of its expected return is the model; the page and `pick_ablation.parquet` show all
  three. Without the state the three are the same model and costs are the flat 2.0 %.
- **Band.** Conformal, per regime. Every training year after the first is scored by a
  model fitted only on the purged weeks before it; each error (outcome − expected) ÷ σ is
  kept with its week's regime (stress when the market state's stress probability is at
  least ½). The 5th and 95th percentiles of a regime's errors give its band, expected +
  percentile × σ; a regime with fewer than 8 weeks of errors uses the pooled percentiles.
  "Bad case (1 in 20)" on the page is the lower edge of the band for the current regime. The
  backtest records, per week, the regime, the band used and the share of outcomes inside it
  (nominal 90 %). Bands are calibrated and scored on the outcomes before winsorizing.
- **Coverage that corrects itself.** Adaptive conformal inference (Gibbs and Candès 2021):
  the band's miss rate α starts at 10 % and, after each week whose 20-session outcome is
  known, moves by 0.05 × (10 % − that week's share of outcomes outside the band), within
  1 %–50 %. The percentiles become α/2 and 1 − α/2. The history is the walk-forward's own
  out-of-sample weeks until the first live week, and the forecast ledger's after it. The
  step of 0.05 is fixed in advance, not fitted.
- **The basket** (`basket.py`). From the same weekly forecasts: up to 10 stocks whose
  expected excess beats their own round-trip cost, best first; a held stock stays while it
  is in the best 20 and still beats its cost. Weights by hierarchical risk parity on the
  covariance of the names' Kalman residual returns over the last 120 sessions (at least 60
  known), shrunk halfway to its diagonal, at most 25 % per name; inverse variance from σ
  when the window is too short. With fewer than 10 names the basket invests names / 10 and
  the rest stays in WIG. Every increase of a weight pays that stock's round-trip cost on the
  increase. It is walked forward week by week beside the single pick, with the same verdict
  rule, and appended weekly to `state/basket.parquet`, never rewritten. **It is the
  decision:** the stock of the week is its largest position, the verdict on the page is the
  basket's, and its weekly record is the live record (the single pick stays as a comparison).
- **Forecast ledger.** On the first session of each week every scored stock's expected
  excess, band, α, regime and cost are appended to `state/forecasts.parquet` and never
  rewritten. Its realized coverage is shown on the page next to the backtest's.
- **Live record.** On the first session of each week the nightly run appends the pick to
  `state/picks.parquet` and never rewrites it. The page shows its results while each pick was
  held, net of switch costs, separately from the backtest.
- **First result (2016-05 → 2018-12 history, out of sample 2018, 49–50 weeks).** The ranking
  works: mean weekly rank IC 0.17 (t ≈ 4.4), led by low volatility and drawdown, closeness to
  the 52-week high and liquidity. The model's top 10 returned +4.4 % against WIG before costs,
  while the whole universe returned −14.2 %. A single weekly pick did not beat WIG: −3 % to
  −6 % after costs, p ≈ 0.6, so the verdict is "no edge". 12-1 momentum alone under the same
  rule lost 88 % after costs. One test year is a small sample; the result will be re-run as
  the backfill adds years.

## 10. Market state

Code: `src/undercurrent/vol.py`, `beta.py`, `regime.py`, `market_state.py`; output
`gold/market_state.parquet`, one row per (stock, session). It is the shared input layer of
the forecast (DECISIONS, "One source of truth"); the stock of the week reads it (§ 9).

- **Fitting.** Every model is fitted once per calendar year Y on the 750 sessions before Y and
  frozen for Y. A value on session t uses returns up to t's close and parameters from before
  t's year, so cutting the data at any date leaves every earlier value unchanged; the tests
  check exactly that. Nothing reaches across a hole in the lake.
- **σ.** GARCH(1,1) with variance targeting, (α, β) chosen on a fixed grid by Gaussian
  quasi-likelihood. A stock with fewer than 250 returns in the window gets the RiskMetrics
  EWMA (λ = 0.94) after 20 returns. `sigma` is the next session's forecast standard deviation,
  `sigma_20` that of the next 20 sessions' return, with the GARCH forecast decaying toward the
  long-run variance. Stock variances never go below (0.5 % a day)².
- **Betas and surprise.** A Kalman filter per stock, without an intercept, on WIG and the
  treasury bond index TBSP. Betas follow a random walk whose daily drift q is chosen per year
  from {0.002, 0.005, 0.01, 0.02} by predictive likelihood. The observation noise is the
  stock's GARCH variance minus what the factors explain. `resid` is the session's return the
  betas did not predict; `surprise` is `resid` over its forecast standard deviation. Betas
  are reported after 60 returns.
- **Regime.** A two-state Gaussian HMM on WIG's daily return. `regime_stress` is the
  filtered probability of the high-variance state, never the smoothed one.
- **Liquidity.** The study's buyability rule over the last 60 sessions (median close ≥ 0.50
  zł): `tradable` from a median turnover of 10,000 zł, `liquid` from 100,000 zł, `thin` below.
- **Cost.** Round trip of a 10,000 zł order: the stock's own Abdi–Ranaldo spread over the last
  60 sessions (the market-wide 1.6 % / 4.8 % with fewer than 20 traded pairs), 0.2 % commission
  each way, and square-root impact σ·√(10,000 / median turnover) each way, capped at 25 %.
- **First run (2015–2018).** Median σ of liquid stocks 2.1 % a day, median cost 1.7 % for
  liquid and 4.7 % for tradable stocks, surprise standard deviation 1.09 on tradable stocks.
- **Read by the pick (out of sample 2018).** Rank IC 0.167 without the state, 0.172 with
  its features (adopted), 0.171 with σ scaling as well (not adopted). The 90 % band held
  86.5 % of outcomes with a fixed α and 87.8 % with the self-correcting one, which ended the
  year at α ≈ 7.5 %. The basket held 3.8 names on average (38 % invested; few names beat their
  cost) and ended −2.8 % against WIG after 3.6 % of costs, against −2.2 % for the single pick
  and −14.2 % for the whole liquid universe. No stress band exists yet: 2017,
  the history it would be calibrated on, had almost no stress weeks, and the 8 stress weeks
  of 2018 (November and December) have no 20-session outcome before the hole in the lake.
