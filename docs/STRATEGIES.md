# Strategy Registry (pre-declared)

Declare each strategy here **before** running any experiment on it: the hypothesis, universe, rebalance schedule, and the full parameter grid. Changing the grid after seeing results counts as new trials, and the registry must reflect that.

| ID | Family | Hypothesis | Universe | Rebalance | Param grid (max trials) | Status |
|---|---|---|---|---|---|---|
| SAN01 | P2 sanity | Buy-and-hold SPY in the engine reproduces the P1 lake returns | SPY | once | none (1); caps off | pass (exact, 2017–2024) |
| SAN02 | P2 sanity | 50 random liquid names weekly have ~0 information ratio vs the equal-weight universe before costs, negative after | top 500 | weekly | seed {0..99} (100) + EW benchmark (1) | pass: IR −0.05 before costs (s.e. 0.03), −1.10 after. First run failed on a harness bug (min-drift blocked the EW benchmark), kept in the registry |
| SAN03 | P2 sanity | A deliberately leaky signal (tomorrow's return) is caught by the truncation leak check | top 500 | weekly | none (1) | pass: flagged (would have shown Sharpe 4.6) |
| S01 | ETF trend + vol target | Time-series momentum persists across asset-class ETFs | ETF list | weekly | lookback {63,126,252} × vol target {10%,15%} (6) | **failed P3 gate** 2026-11-03: best Sharpe 0.61 vs SPY 0.86; DSR 0.94 (6 trials), PBO 0.86; 50/50-blend alpha −0.7%; max DD −23% vs −34%. Reports `20261103-2056*`/`-2057*` |
| S02 | ST reversal | Liquid losers over the past 5 days rebound in the following week | top 500 | weekly | lookback {5,10} × hold {5} × top/bottom {decile, quintile} (4) | **failed P3 gate** 2026-11-04 (pit): Sharpe 0.59–0.75 vs SPY 0.89, beta 1.28, CAPM alpha −0.3%/yr (t −0.07); family DSR fails. 4 trials (+4 superseded by the min-drift bug). Reports `20261104-13384*` |
| S03 | Momentum 12-1 | Medium-term winners keep outperforming | top 500 | monthly | skip {1} × lookback {126,252} (2) | **passes P3 gate as declared** 2026-11-04 (pit), with caveats: lb126 Sharpe 0.99 (2x 0.97, delay 0.96) vs SPY 0.89; DSR 0.999 family / 0.98 over all 328 base trials; 3/3 subperiods positive; PBO n/a (2 variants). Beta 1.27, CAPM alpha +13.8%/yr (t 1.6, not significant), concentrated in 2020/21/25; best 1% of days = 77% of log return; max DD −41% vs −34%. lb252 near-identical (0.97). Reports `20261104-133930-0c6163`, `-133933-6d9a9a`. Earlier seed run is not evidence |
| S04 | Earnings drift | Large announcement reactions drift in mid caps | top 1000 | event | reaction threshold {1σ,2σ} × hold {5,20} (4) | **failed P3 gate** 2026-11-04 (pit): Sharpe 0.38–0.64, 2x 0.16–0.56; beta 1.11, CAPM alpha −2.2%/yr (t −0.5) at best point; DSR < 0.95. Reports `20261104-13400*`–`-13401*` |
| S05 | LGBM ranker | Nonlinear feature combinations rank 5-day returns | top 500 | weekly | feature set {price, price+fund, all} × num_leaves {15, 63} × n_estimators {300, 800} (12) | **failed P3 gate** 2026-11-04 (pit, walk-forward 2019–2025-10): Sharpe price 0.69–0.72, price+fund 0.58–0.76, all 0.48–0.49 vs SPY 0.92; best 2x 0.66; PBO 0.69; beta 1.40; turnover 33x (cost drag 4.2%/yr); 2021-04..2023-07 subperiod negative; best 1% of days > 100% of log return. Regime features hurt every variant. Reports `20261104-1346*`–`-1401*` |
| S06 | LGBM + LLM text | Post-cutoff news scores add incremental lift | top 500 | weekly | ablation only (≤ 6) | planned |

Notes:
- S03 "skip {1}" = skip the most recent month (21 sessions). The P2 sanity run "textbook 12-1 momentum" is the S03 grid point `{lookback: 252, skip: 21}`, so it counts as an S03 trial. It runs on the survivorship-biased seed universe (D14), so it is **not** gate evidence.
- Every backtest is registered three times (base costs, 2x costs, +1 day delay). Family trial counts use the base-cost rows.
- **S01 implementation (declared 2026-11-03, before any S01 run):** each week (last session), an ETF in `config/universe.yaml` is "on" if its trailing `lookback`-session total return is > 0. "On" ETFs get equal weight, then the portfolio is scaled to `vol_target` using the trailing 63-session covariance, gross ≤ 100%, cash otherwise. The engine applies the caps from limits.yaml (35% per ETF) and the 2% min-drift rule. ETF costs are 2 bps per side (D4). ETFs enter once they have `lookback` sessions of history (XLC from mid-2018).
- **S02 implementation (declared 2026-11-03, before any S02 run):** on the last session of each week, rank the point-in-time top-500 (D20/D21) by trailing `lookback`-session return. Buy the worst decile (50 names) or worst quintile (100 names) at equal weight. Hold until the next weekly rebalance (`hold` = 5 sessions). Long only; gateway caps and min-drift apply. Universe `pit`.
- **S04 implementation (declared 2026-11-03, before any S04 run):** events are SEC 8-K filings with Item 2.02, timed by the EDGAR acceptance timestamp. The reaction session is the first session whose close falls after acceptance. The reaction is that session's close-to-close return minus SPY's, standardized by the name's trailing 60-session standard deviation of SPY-relative returns. If the reaction is ≥ `threshold` σ (positive surprises only, long-only), buy at the next open and hold `hold` sessions. Active positions are equal-weighted at 1/max(n, 10), so with fewer than 10 events the rest is cash. Universe: point-in-time top-1000. Needs EDGAR filings for every CIK that was ever a member (to be fetched with the universe).
- **S05 implementation (declared 2026-11-03, before any S05 run or model fit):**
  - **Universe and schedule:** point-in-time top 500 (D20/D21), rebalanced on the last session of each week. Long the top decile of predictions (50 names) at equal weight. Gateway caps and min-drift apply.
  - **Label:** row t's label is the 5-session forward return from open t+1 to open t+6, minus SPY's over the same window, as a cross-sectional percentile rank among universe members on day t. Objective: LightGBM regression (L2) on that rank.
  - **Features:** all point-in-time, from `src/trader/features/`.
    - Stock features are cross-sectional percentile ranks per day: returns 1/5/20/60 and 12-1, volatility 20/60, overnight and intraday means, gap, distance from the 252-day high, volume surprise, log dollar volume, beta, days since earnings, last earnings reaction z.
    - Fundamentals (from filings, as known at acceptance time): log market cap, earnings yield, book-to-market, sales-to-price, ROA, accruals, leverage, revenue growth, EPS growth, filing age.
    - Regime features are raw: SPY 200-day trend and 20-day volatility, VIX, 10y−2y and 10y−3m curve, BAA−AAA spread, 10-year yield.
    - Feature sets: `price` = price/volume/event; `price+fund` adds fundamentals; `all` adds the regime features (SPY trend/vol and FRED).
    - *Amended 2026-11-03, before any S05 result was computed.* A targets-only timing run on the seed panel computed no returns, metrics or trials. Its feature importances showed the regime features (constant across stocks on a day) taking the top 7 gain slots. With a per-day rank label they mostly identify the date, which is a memorization risk. So regime features moved from every set to `all` only, and the ablation now tests them. The grid size is unchanged (12).
  - **Walk-forward:**
    - The model is retrained each January 1 on an expanding window from 2016-11 to the prior December 31. The last 5 sessions before the cutoff are purged so no training label overlaps the test year, with a 5-session embargo.
    - The first test year is 2019 (training data 2016-11 to 2018-12). Test years run from 2019 to 2025-10, all before the holdout.
    - Fixed hyperparameters besides the grid: learning_rate 0.03, min_data_in_leaf 500, feature_fraction 0.8, bagging_fraction 0.8 / freq 1, lambda_l2 1.0, seed 0.
    - Every grid point is a registered trial (12).
  - **Gate (P5):** beats the best P3 baseline by a margin that survives the deflated Sharpe at the combined trial count, at 2× costs. Feature importance must also be stable across retrain years.
- **S06 stage 1, forward event study (declared 2026-11-03, before any score was evaluated; `trader llm-eval`, `src/trader/llm/evaluate.py`):**
  - **Data:** prompt-v1 Haiku 5.5 scores of forward news (D5/D13: documents after 2026-11-01 only).
  - **Signal:** direction × magnitude × confidence.
  - **Entry:** the first session open after the score's known_ts.
  - **Outcome:** the SPY-relative open-to-open return over h ∈ {1, 5} sessions.
  - **Primary metric:** the mean daily cross-sectional Spearman IC at h = 5, with a Newey-West (lag 5) t-stat.
  - **When it is read:** outcome statistics are computed **once**, when 60 entry days have a full 5-day outcome (about early February 2027). Until then the report shows data health only. A `--force` read is logged as forced and doesn't count as the read.
  - **Stage 2:** the P6 gate is the S05 ablation (best S05 config ± LLM features, ≤ 6 trials) on the same forward window.
  - **If stage 1 fails:** stage 1 is diagnostic, not a gate. A clearly non-positive IC (t < 0) is still reason enough to drop the LLM from the signal path early.
