# Strategy Registry (pre-declared)

Declare each strategy here **before** running any experiment on it: the hypothesis, universe, rebalance schedule, and the full parameter grid. Changing the grid after seeing results counts as new trials, and the registry must reflect that.

| ID | Family | Hypothesis | Universe | Rebalance | Param grid (max trials) | Status |
|---|---|---|---|---|---|---|
| S01 | ETF trend + vol target | Time-series momentum persists across asset-class ETFs | ETF list | weekly | lookback {63,126,252} × vol target {10%,15%} (6) | declared |
| S02 | ST reversal | Liquid losers over the past 5 days rebound in the following week | top 500 | weekly | lookback {5,10} × hold {5} × top/bottom {decile, quintile} (4) | declared |
| S03 | Momentum 12-1 | Medium-term winners keep outperforming | top 500 | monthly | skip {1} × lookback {126,252} (2) | declared |
| S04 | Earnings drift | Large announcement reactions drift in mid caps | top 1000 | event | reaction threshold {1σ,2σ} × hold {5,20} (4) | declared |
| S05 | LGBM ranker | Nonlinear feature combinations rank 5-day returns | top 500 | weekly | see experiment spec (≤ 30) | planned |
| S06 | LGBM + LLM text | Post-cutoff news scores add incremental lift | top 500 | weekly | ablation only (≤ 6) | planned |
