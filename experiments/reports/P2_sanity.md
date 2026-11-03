# P2 harness sanity tests

## SAN01 buy-and-hold SPY reproduces P1: PASS

| year | P1 lake | engine | diff | |
|---|---|---|---|---|
| 2017 | +21.7463% | +21.7463% | -0.0000% | PASS |
| 2018 | -4.9617% | -4.9617% | +0.0000% | PASS |
| 2019 | +31.1360% | +31.1360% | +0.0000% | PASS |
| 2020 | +18.4533% | +18.4533% | +0.0000% | PASS |
| 2021 | +28.6011% | +28.6011% | -0.0000% | PASS |
| 2022 | -18.1753% | -18.1753% | -0.0000% | PASS |
| 2023 | +26.1768% | +26.1768% | -0.0000% | PASS |
| 2024 | +24.8854% | +24.8854% | +0.0000% | PASS |

Trial `20261103-205524-8b3fd8`

## SAN03 leaky signal is caught: PASS

Leak check: `LEAK: targets at day index 1683 change when data after 1683 is removed`; status `leak_detected`. (Had it been trusted: Sharpe 4.6, CAGR +321%.) Trial `20261103-205525-4f9bbe`

## SAN04 textbook 12-1 momentum is plausible: PASS

Report: `experiments/reports/20261103-205310-0f40d1.md`. Sharpe 1.08, CAGR +35.50%, max DD -40.4%.
Pass rule: leak check ok and 0 < Sharpe < 1.5 (positive but not implausible). The level is inflated by the
survivorship-biased seed (today's top-1000, D14): SPY-beta 1.34 and a +35% CAGR are what picking from
today's winners looks like. Not gate evidence for S03.


## SAN02 random signal adds nothing: PASS

100 seeds, 50 random names from the liquid top-500 each week, vs the weekly equal-weight top-500.

| | mean | 5th pct | 95th pct |
|---|---|---|---|
| information ratio before costs | -0.054 | -0.601 | +0.500 |
| information ratio after costs | -1.095 | -1.616 | -0.530 |
| Sharpe after costs (absolute) | 0.58 | 0.45 | 0.70 |

Equal-weight benchmark: Sharpe 0.84, CAGR +15.89% (trial `20261103-205526-134428`).
Pass rule: |mean IR before costs| < 0.15 and costs lower the IR. Absolute Sharpe is positive only because
the long-only universe rose; the random selection itself adds nothing.
