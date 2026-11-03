# Strategy Registry (pre-declared)

Declare each strategy here **before** running any experiment on it: the hypothesis, universe, rebalance schedule, and the full parameter grid. Changing the grid after seeing results counts as new trials, and the registry must reflect that.

| ID | Family | Hypothesis | Universe | Rebalance | Param grid (max trials) | Status |
|---|---|---|---|---|---|---|
| SAN01 | P2 sanity | Buy-and-hold SPY in the engine reproduces the P1 lake returns | SPY | once | none (1); caps off | pass (exact, 2017–2024) |
| SAN02 | P2 sanity | 50 random liquid names weekly have ~0 information ratio vs the equal-weight universe before costs, negative after | top 500 | weekly | seed {0..99} (100) + EW benchmark (1) | pass: IR −0.05 before costs (s.e. 0.03), −1.10 after. First run failed on a harness bug (min-drift blocked the EW benchmark), kept in the registry |
| SAN03 | P2 sanity | A deliberately leaky signal (tomorrow's return) is caught by the truncation leak check | top 500 | weekly | none (1) | pass: flagged (would have shown Sharpe 4.6) |
| S01 | ETF trend + vol target | Time-series momentum persists across asset-class ETFs | ETF list | weekly | lookback {63,126,252} × vol target {10%,15%} (6) | **failed P3 gate** 2026-11-03: best Sharpe 0.61 vs SPY 0.86; DSR 0.94 (6 trials), PBO 0.86; 50/50-blend alpha −0.7%; max DD −23% vs −34%. Reports `20261103-2056*`/`-2057*` |
| S02 | ST reversal | Liquid losers over the past 5 days rebound in the following week | top 500 | weekly | lookback {5,10} × hold {5} × top/bottom {decile, quintile} (4) | declared |
| S03 | Momentum 12-1 | Medium-term winners keep outperforming | top 500 | monthly | skip {1} × lookback {126,252} (2) | 1/2 run (P2 sanity, biased seed, not evidence): Sharpe 1.08, beta 1.34. Report `20261103-205310-0f40d1` |
| S04 | Earnings drift | Large announcement reactions drift in mid caps | top 1000 | event | reaction threshold {1σ,2σ} × hold {5,20} (4) | declared |
| S05 | LGBM ranker | Nonlinear feature combinations rank 5-day returns | top 500 | weekly | see experiment spec (≤ 30) | planned |
| S06 | LGBM + LLM text | Post-cutoff news scores add incremental lift | top 500 | weekly | ablation only (≤ 6) | planned |

Notes:
- S03 "skip {1}" = skip the most recent month (21 sessions). The P2 sanity run "textbook 12-1 momentum" is the S03 grid point `{lookback: 252, skip: 21}`, so it counts as an S03 trial. It runs on the survivorship-biased seed universe (D14), so it is **not** gate evidence.
- Every backtest is registered three times (base costs, 2x costs, +1 day delay). Family trial counts use the base-cost rows.
- **S01 implementation (declared 2026-11-03, before any S01 run):** each week (last session), an ETF in `config/universe.yaml` is "on" if its trailing `lookback`-session total return is > 0. "On" ETFs get equal weight, then the portfolio is scaled to `vol_target` using the trailing 63-session covariance, gross ≤ 100%, cash otherwise. The engine applies the caps from limits.yaml (35% per ETF) and the 2% min-drift rule. ETF costs are 2 bps per side (D4). ETFs enter once they have `lookback` sessions of history (XLC from mid-2018).
