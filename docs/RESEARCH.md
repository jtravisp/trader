# Research Summary

Last updated 2026-11-03. This is a living file: add findings with sources and dates. Claims rated **[strong]** (peer-reviewed or primary), **[moderate]** (credible preprint or primary vendor docs), or **[weak]** (blogs, forums, press).

## What the evidence says

### Approaches that have failed
- **Autonomous LLM traders.**
  - Alpha Arena stock contests: models were profitable in 6 of 32 results, and the combined portfolio lost about a third. [weak; short, noisy]
  - TradingAgents (multi-agent): backtests only, no live record, est. $0.50–2.00 API cost per trade. [moderate]
- **LLM backtests on pre-cutoff data.** The models have memorized what happened, so measured alpha decays out of sample.
  - FINSABER: LLM advantages deteriorate over 20 years and 100+ symbols. [moderate]
  - Look-Ahead-Bench (2026): significant bias in Llama 3.1 and DeepSeek. [moderate; author affiliated with a PIT-model vendor]
  - Kelly, Malamud et al., NBER w35247 (2026): point-in-time LLMs trained to monthly cutoffs from 2013–2024. PIT embeddings gave positive out-of-sample Sharpe close to the leaky full-sample models. [strong]
- **Retail day trading.** In Brazil, 1.1% of persistent futures day traders beat minimum wage. In Taiwan, under 1% were consistently profitable. [moderate; via summaries]
- **Time-series foundation models (Chronos, TimesFM, etc.), zero-shot on returns.** Generally they don't beat a random walk. Fine-tuned or domain-pretrained versions can match tree ensembles at longer horizons. [moderate; arXiv 2606.27100, 2511.18578]
- **RL (FinRL-style).** Results and algorithm rankings flip under realistic nonlinear costs. Out-of-sample 2025 Sharpe was mostly negative. [moderate; arXiv 2603.29086]

### Approaches with promise
- **Gradient boosting on short-horizon cross-sectional returns.** Huddleston, Liu & Stentoft (J. Fin. Econometrics 2023): 5-min returns, Sharpe 0.98 after costs. Predictability has declined since decimalization and is higher on volatile or illiquid days. [strong]
- **Intraday momentum on SPY.** Zarattini, Aziz & Barbon: 19.6%/yr, Sharpe 1.33 net, 2007–2024. One author sells day-trading education, and there is no independent replication with post-2024 numbers. [moderate]
- **LLM headline scoring → returns.** Lopez-Lira & Tang: predictive, but only exploitable at very low cost, and the Sharpe decayed as adoption spread. With multi-day holds, costs are lower, so this is worth testing on post-cutoff data. [strong for existence, weak for current exploitability]
- **Earnings drift (PEAD).** Largely gone in large caps by ~2006 (Martineau, "Rest in Peace"). It may survive in hard-to-arbitrage names. [strong]

### Methodology
- **Overfitting.**
  - Use the Probability of Backtest Overfitting via CSCV (Bailey, Borwein, López de Prado, Zhu, 2015).
  - Use the deflated Sharpe ratio (Bailey & López de Prado).
  - In one 2024 study, CPCV beat walk-forward at preventing false discoveries (Arian et al.). [strong/moderate]
- **Statistical power.** The t-stat is roughly Sharpe × √years. A Sharpe of 1 needs about 4 years for t ≈ 2.
- **Paper trading realism (Alpaca).**
  - Fills happen at the live NBBO quote.
  - About 10% partial fills, randomized.
  - No market impact, no queue position, no depth check.
  - Opening-auction paper fills are reportedly unreliable. [weak; forums]
  - Log signal, submit, and quote timestamps, then compare paper with live fills.

## Data and vendor facts (verify before relying on them)
- **Alpaca free:**
  - Bars since 2016 and IEX real-time, 200 calls/min.
  - Staff say history older than 15 min matches the paid tier with `feed=sip` (old post).
  - SIP real-time costs $99/mo.
  - The News API (Benzinga, 2015+) was free with rate limits as of its 2022 launch.
  - **Verified 2026-11-03:** free paper keys return SIP daily bars for SPY in Jan 2016 (`feed=sip`).
- **Massive (ex-Polygon) Stocks Starter:**
  - $29/mo, 5 years of bars, flat files.
  - All tickers, including delisted ones, via the reference endpoint with `active=false` and a date.
  - Corporate actions included.
- **Databento:** usage-based, best for tick and L2 data. Overkill for daily horizons.
- **Alpaca MCP server:** official repo `alpacahq/alpaca-mcp-server`. The `ALPACA_TOOLSETS` setting restricts the tools exposed, and paper trading is the default.

## Sources
- Kelly, Malamud, Schwab & Xu, *Scaling Point-in-Time Language Models*: https://www.nber.org/papers/w35247
- Look-Ahead-Bench: https://arxiv.org/pdf/2601.13770
- Huddleston, Liu & Stentoft (2023): https://econpapers.repec.org/RePEc:oup:jfinec:v:21:y:2023:i:2:p:485-527.
- Zarattini, Aziz & Barbon: https://concretumgroup.com/beat-the-market-an-effective-intraday-momentum-strategy-for-sp500-etf-spy/
- TSFMs for returns: https://arxiv.org/pdf/2606.27100 · https://arxiv.org/html/2511.18578v1
- FinRL realistic costs: https://arxiv.org/abs/2603.29086v2
- TradingAgents: https://arxiv.org/pdf/2412.20138 · review: https://toolbrain-astro.pages.dev/blog/tauric-tradingagents-review-2026/
- PBO: https://papers.ssrn.com/abstract=2326253 · CSCV lab: https://quinfer.github.io/financial-data-science/labs/lab10_backtesting.html
- PEAD decline: https://cfr.ivo-welch.info/published/papers/martineau2021rest.pdf · https://experts.colorado.edu/display/pubid_511043
- Alpaca data docs: https://docs.alpaca.markets/docs/api-documentation/how-to/market-hours/ · forum: https://forum.alpaca.markets/t/market-historical-data-discrepancies/14059
- Alpaca news: https://docs.alpaca.markets/us/docs/historical-news-data
- Alpaca MCP: https://github.com/alpacahq/alpaca-mcp-server · https://docs.alpaca.markets/docs/alpaca-mcp-server
- Alpaca paper fills: https://blog.traderspost.io/article/alpaca-paper-trading
- Massive pricing: https://massive.com/pricing · delisted: https://massive.com/knowledge-base/article/does-massive-have-a-downloadable-security-master
- Data vendor comparison 2026: https://aifinhub-monorepo.pages.dev/articles/market-data-apis-compared-2026/
- Your original research notes (Nov 3, 2026) cover Alpha Arena, the Kalshi and Polymarket studies, Knight Capital, and the security incidents.

## Verified in P1 (2026-11-03)
- **Alpaca free history begins 2016-01-04.** There is no 2015 SIP daily bar, so the 2016 calendar return can't be computed. [strong; direct test]
- **Free-tier SIP requests are rejected (403) if the window reaches into the last 15 minutes.** Forward ingests stop at the last session that closed more than 20 minutes ago. [strong; direct test]
- **SPY adjusted returns from Alpaca (adjustment=all) match published total returns:** 2017–2024 all within 0.4%/yr, and 6 of 8 years within 0.13%. The 2018 gap is the largest (−0.39%). [strong; vs totalrealreturns.com/n/SPY, cross-checked with Yahoo for 2023–2025]
- **Alpaca news volume:** about 650–800 items per day (2016, 2021, 2025 samples), about 15 pages of 50 items. The headline+summary average is about 120 characters. About 1000 items/day mention a universe name. The full 2015–2025 backfill is about 40k requests, roughly 3.5 hours at 190/min. [strong; direct test]
- **Survivorship (Alpaca):** of 48 notable names delisted 2016–2025, only **19/48** can be enumerated from Alpaca's active+inactive asset lists. Nothing delisted from mid-2022 on is listed (TWTR, SIVB, FRC, BBBY, ATVI, VMW, SPLK, PXD, HES...). Some active matches are ticker reuse (APC, STI, SBNY). However, **45/48 still return 2017 daily bars by symbol**. Renamed tickers carry the full history under both old and new symbols (FB/META, SQ/XYZ, UTX/RTX). So the gap is the *list of historical tickers*, not the bars. Report: `data/quality/survivorship.md`. [strong; direct test]
- **EDGAR `acceptanceDateTime` is true UTC.** Apple's 16:30 ET earnings 8-Ks show 20:30Z (EDT) and 21:30Z (EST). [strong; direct test]
- **alpaca-py 0.44 can't parse the asset list:** its exchange enum lacks `TXSE` (Texas Stock Exchange). We use raw REST for assets. [strong]
- **Yahoo adjusted closes match Alpaca adjustment=all to the cent** on the AAPL 2020 split window. Yahoo (yfinance) is used only as the second source in quality checks. [moderate; one spot check, the full 20-name check is in the quality report]

## Backtest evidence (P2/P3, pre-holdout 2016-2025-10)
- **Momentum overlays (2026-11-04, family S03, 9 trials):** the lookback is robust across 126 to 252 sessions (Sharpe 0.97 to 1.00), but weak at 63 (0.78). The family PBO is 0.17. Vol targeting at 15% or 20% (Barroso & Santa-Clara style, monthly) cuts beta and drawdown, but its Sharpe of 0.84 is below S03's. In this sample the published crash-protection gain doesn't appear, maybe because a long-only top decile with no short leg has milder crashes. Per-position stops of 10% and 20% lower Sharpe to 0.83 and 0.87 with little drawdown benefit, and a 10% stop is oddly delay-sensitive (0.83 vs 0.95 with a 1-day delay), which is a sign of noise. [moderate; 9 years, simulated stops on daily lows]
- **Live-stack replay of S03, 2025-01..2025-10 (2026-11-05).** The live code reproduces the engine (+47.9% vs +49.7%, daily corr 0.985), so P4 machinery parity holds in simulation. The halt policy is the real issue: a $200 S03 account starting in January 2025 hits the 15% peak-drawdown halt on Feb 26 and would have fallen to −43% from peak by April 7 had a human cleared it at once. Halted, it froze the February portfolio, which happened to recover better (+73%), luck rather than design. Expect the drawdown halt to fire on paper; deciding what a human does then (resume, de-risk to SPY, or stop S03) should happen before it fires. [strong for parity; one 10-month replay]
- **Classic long-only factors on the pit top 500, 2016–2025-10 (2026-11-05; S10 quality, S11 low volatility, S12 sector momentum; 18 declared variants).** None beats SPY's Sharpe (best 0.84 vs 0.89) or shows alpha after beta (50/50-blend alpha within ±0.8%/yr everywhere), and every family fails PBO (0.37–0.66). Quality earns more than SPY (CAGR up to 18%) but only through beta 1.2. Low volatility delivers its beta (0.6) and nothing more: CAGR ~10%, and no drawdown protection in 2020 or 2022. Sector momentum is a noisy SPY. Hold period hardly matters for slow signals because the 2% min-drift suppresses most daily trading (turnover 1–20x). Read: in this decade of mega-cap-led US returns, published long-only premia in large caps add nothing over SPY at our scale; any edge must come from information (S06) rather than another public price/fundamental sort. Caveat: PBO among near-identical variants is close to a coin flip by construction, so the PBO failures say "no reliable best variant", not "negative". [moderate; one 9-year sample, 18 variants]
- **Open vs 09:40 fill timing (2026-11-05).** The gateway trades at 09:40 ET; backtests fill at the 09:30 open. From open to 09:40 the mean move is +0.25 bps for 30 ETFs (2021–2025-10, 36k ETF-days; SPY +0.67 bps, mean absolute 14 bps) and −0.27 bps (s.e. 1.2) for the 50 current S03 names in 2025 (10k stock-days, 5-minute bars; mean absolute 86 bps per name, 35 bps for the equal-weight basket). 09:35 is barely quieter (72 bps). So the 10-minute delay is unbiased noise, not a cost: no backtest change is needed, but single rebalance days can exceed the P4 10 bps bound by chance. The first paper day's +37.6 bps on SPY was an unlucky morning. [strong for no bias; direct measurement]
- **S09 daily short-term reversal (2026-11-04, 4 declared variants, pit top 500, 2016–2025-10).** Buying the worst 1- or 3-session losers and holding 1 or 3 sessions fails at every point: best Sharpe 0.58 (lb3, hold 3) vs SPY 0.89, 2x 0.36. The daily version turns over 212x a year, and at our 5 bps/side model costs eat 23%/yr, turning a gross return of about beta into −6% CAGR. Slower holds help monotonically (hold 3 > hold 1, lookback 3 > lookback 1), the same direction as S02 weekly and S03 monthly. With retail costs, daily bars and long-only, faster trading on price signals destroys value here; any daily edge must come from new information (S06 news), not price patterns. [moderate; one 9-year sample, 4 variants]
- **S05 LightGBM ranker on price/fundamental/regime features (2026-11-04, 12 declared variants, yearly walk-forward from 2019).** No variant beats SPY (best Sharpe 0.76 vs 0.92); PBO 0.69; beta 1.40; 33x turnover costs ~4%/yr; the regime features lower every variant. A generic weekly 5-day-return ranker on public daily data adds nothing over beta here. [moderate; 7-year walk-forward]
- **Stock baselines on the point-in-time universe (2026-11-04, delisted names and delisting returns included).** S02 weekly reversal and S04 8-K earnings drift have no alpha after beta (CAPM alpha −0.3% and −2.2%/yr, |t| < 0.5) and fail the gate. S03 6- and 12-month momentum (skip 1 month) passes the declared gate (Sharpe 0.99 vs SPY 0.89, cost- and delay-robust, DSR 0.98 even against all 328 base trials) but most of it is beta 1.27: CAPM alpha +13.8%/yr with HAC t 1.6, earned in 2020, 2021 and 2025; 1% of days give 77% of log return; top names CHPT/GME/LCID. Read as: long-only momentum in liquid US stocks is a high-beta, speculative-regime bet with weak, unproven alpha. Survivorship bias on the seed universe had inflated it only modestly (1.08 → 0.97 for lb252). [moderate; one 9-year sample, 2 variants]
- **S01 ETF trend + vol targeting fails the P3 gate.** 30 ETFs, weekly, 6 pre-declared variants. Sharpe 0.49–0.61 vs SPY 0.86. DSR 0.94 at 6 trials, PBO 0.86 (variant choice is not robust). The 50/50 blend with SPY has negative alpha. It does cut drawdowns (−23% vs −34%) and is insensitive to costs and a 1-day delay. Consistent with the post-2009 weakness of trend-following in equities-heavy ETF sets. [strong for this sample; one family, 9 years]
- **Survivorship bias, measured:** 12-1 momentum on today's top-1000 names (selected on 2025 volume) shows +35.5% CAGR, Sharpe 1.08, beta 1.34 (2017–2025). That is what selecting on later winners looks like, and the reason stock strategies wait for the point-in-time universe. [strong as a warning, uninformative as a signal]
- **Harness:** the engine's buy-and-hold SPY matches the lake exactly. 100 random-selection seeds have IR −0.05 ± 0.03 before costs. A next-day-return leak is caught by the truncation test. Report: `experiments/reports/P2_sanity.md`.
- **Massive free tier (Stocks Basic):** reference data, including historical ticker lists by date and corporate actions, works without the paid plan's 5-year limit (5 calls/min). Snapshots are complete from 2016-10-31. Earlier ones lack NASDAQ (D20). On reused tickers, dividends are attributed to the company that held the ticker *at that time* (DOC pre-2024 = Physicians Realty), while Alpaca's bars and adjustments for reused tickers follow the *current* company. Reused tickers therefore need entity-level (FIGI/CIK) handling. [strong; direct test 2026-11-03]
- **Paper execution vs the submit-minute price (2026-11-04..09, 56 fills, S03/FALLBACK):** mean slippage per day vs the open of the 1-minute bar at submission was −1.0, +1.9, +1.0, +6.9 bps (modeled 5 bps per side for stocks). Vs the session open it was +37.6, +65.0, −50.2, +72.9 bps: almost all of that is open-to-09:40 drift (+38.5, +63.1, −51.2, +66.1 bps). Alpaca paper fills are simulated at the quote, so this says nothing yet about live market impact. Basis for D30. [moderate; 4 days, paper fills]
