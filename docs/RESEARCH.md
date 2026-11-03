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
