# Architecture

Status: draft v1 (2026-11-03). Owner: Travis. Decisions marked **[D#]** are logged in `docs/DECISIONS.md`.

## Goal and design principles

**Goal:** risk-adjusted profit from US stocks and ETFs held for days (not strictly intraday), beating a SPY buy-and-hold benchmark after all costs.

Principles, in priority order:

1. **Assume no edge until proven.** Every strategy must clear the gates in `docs/PLAN.md`. If none clears, the money sits in SPY. That is a valid outcome, not a failure.
2. **Backtest and live share one code path.** A signal function used in research is imported unchanged by the live runner.
3. **Point-in-time everything.** Every datum carries the timestamp at which it was *knowable*, not the period it describes. This applies to prices, filings, news, universe membership, and LLM outputs.
4. **Fully automated, with risk controls outside the strategy.** Orders are placed automatically, with no human approval per trade. Models (rules, ML, LLM) output a target portfolio. A deterministic gateway with its own config and credentials validates the target against hard limits and submits the orders itself. Models never call the broker directly, so a bad model output or a prompt-injected news item can at worst produce a target that the gateway clips or refuses.
5. **Count every trial.** All experiments are logged so that overfitting statistics (deflated Sharpe, PBO) use the true number of attempts.
6. **Forward time is the scarcest resource.** Start forward collection (paper trading, live LLM scoring) as early as possible, in parallel with research.

## System overview

```
                     ┌──────────────────────── RESEARCH (offline) ────────────────────────┐
 Alpaca bars/news ─┐ │                                                                     │
 Massive ref data ─┼─▶ ingest ─▶ Parquet lake ─▶ features ─▶ signals ─▶ backtest engine ─▶ │
 SEC EDGAR ────────┤ │  (raw, immutable)  (DuckDB)   (PIT)   rules/ML/LLM   costs, sizing  │
 FRED macro ───────┘ │                                              │                      │
                     │                            experiment registry (every trial)       │
                     │                            → metrics, DSR, PBO, reports            │
                     └──────────────────────────────┬──────────────────────────────────────┘
                                                    │  promote: frozen strategy version
                     ┌──────────────────────── LIVE (paper → live) ───────────────────────┐
                     │ scheduler ─▶ runner: refresh data → same signals → TARGET PORTFOLIO │
                     │                                   │  (written to orders DB)         │
                     │                   LLM scorer ─────┤  (schema-validated features,    │
                     │                                   │   veto flags; no order power)   │
                     │                                   ▼                                 │
                     │          RISK GATEWAY (separate process, own keys, limits.yaml)    │
                     │          diff target vs broker positions → validated orders        │
                     │                                   ▼                                 │
                     │                         Alpaca (paper / live)                      │
                     │  reconcile on start · idempotent client_order_id · kill switch     │
                     │  monitor: fills vs model, live vs backtest replay, alerts          │
                     └─────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. Data layer (`src/trader/data/`)

| Source | Use | Cost | Notes |
|---|---|---|---|
| Alpaca Market Data (free) | Daily and minute bars 2016+, IEX real-time | $0 | Request `feed=sip` for history older than 15 min. Verify that free-tier SIP history still works. |
| Alpaca News API | Benzinga headlines and bodies, 2015+ | $0 (rate-limited) | Our primary text source for LLM features. |
| Massive (ex-Polygon) Stocks Starter | All tickers including **delisted**, corporate actions, flat files, 5-year bars | $29/mo | **[D3]** Adopt after Phase 1 confirms the survivorship gap matters. |
| SEC EDGAR (free) | 8-K filings (Item 2.02 = earnings release), XBRL fundamentals, acceptance timestamps | $0 | A natural point-in-time source. Requires a User-Agent with contact info per SEC policy. |
| FRED (free) | Rates, VIX, credit spreads for regime features | $0 | Use the vintage/ALFRED data where revisions matter. |

Storage:
- **Raw:** files are stored as downloaded under `data/raw/<source>/<date>/` and never edited.
- **Normalized:** a Parquet lake under `data/lake/`, partitioned by dataset/year and queried through DuckDB.
- **Point-in-time columns:** every table has `event_ts` (what the data describes) and `known_ts` (when we could have known it). Joins use `known_ts <= decision_ts`.
- **Holdout guard:** the loader refuses rows with `known_ts >= HOLDOUT_START` unless called with an explicit `unlock_holdout=True`, which is logged to the registry.
- **Quality checks:** missing bars, split/dividend adjustments, outlier returns, stale quotes. Results go to `data/quality/` reports.

### 2. Universe (`src/trader/universe/`)
- Point-in-time liquid universe: the top ~500 US common stocks by 60-day median dollar volume, price > $5, rebuilt monthly *using only data available then* and including names that later delisted.
- A separate ETF universe (SPY, QQQ, IWM, sector SPDRs, TLT, GLD, etc.) for trend and regime strategies.

### 3. Features (`src/trader/features/`)
Pure functions of point-in-time data:
- **Price:** returns over 1/5/20/60/252 days, volatility, overnight vs intraday split, gaps, distance from highs, volume surprises.
- **Event:** days since or until earnings (EDGAR 8-K 2.02), announcement-day reaction, filing types.
- **Fundamental:** XBRL-derived accruals, growth, and surprise proxies.
- **Text:** LLM scores (see §5) and their decay-weighted aggregates.
- **Regime:** VIX level/term, trend of SPY, credit spreads.

### 4. Signals and strategies (`src/trader/signals/`)
Interface: `signal(features_at_t) -> target weights` (or scores to be turned into weights by the portfolio module). Every strategy declares its rebalance schedule, universe, and any parameters it uses. Planned families, simplest first:

1. **ETF trend + vol targeting:** robust, low turnover; also the "regime" overlay.
2. **Cross-sectional short-term reversal / medium-term momentum:** weekly rebalance on the liquid universe.
3. **Earnings-event drift:** enter after the 8-K, sized by surprise and reaction. This effect is weaker in large caps, so it needs testing.
4. **LightGBM ranker:** predicts 5-day forward return rank from all features; this is the main ML candidate.
5. **LLM-augmented:** text features fed into (4), plus an event-risk veto.
6. **Ensemble:** risk-parity or inverse-variance blend of the strategies that survive.

### 5. LLM layer (`src/trader/llm/`)
The LLM is a **feature extractor and veto**, never a trader.
- **Input:** one news item or filing at a time. All text is treated as untrusted; prompts never include tools or account data.
- **Output:** strict JSON validated with Pydantic, containing `{event_type, direction(-1..1), magnitude(0..1), confidence(0..1), horizon_days, risk_flags[]}`. Invalid output is dropped, not repaired.
- **Model:** a small, cheap Claude model (Haiku class) for bulk scoring. Scores are cached by `(model_id, prompt_version, doc_id)`, so each document is scored once.
- **Look-ahead rule [D5]:** an LLM signal may only be evaluated on documents published **after the scoring model's documented training cutoff**. For a longer historical window, use an open-weight model with an old, known cutoff, or a point-in-time model. Pre-cutoff backtests of LLM features are reported but never used for go/no-go decisions.
- **Forward collection starts in Phase 1:** every new news item is scored daily and stored, building a clean out-of-sample record while the rest is built.

### 6. Backtest engine (`src/trader/backtest/`)
- Vectorized daily engine in Polars, with an event loop only where path dependence requires it (stops).
- **Timing:** signals computed from data known at the close of day T; trades fill at the open of T+1 (or a configurable 15:45→close variant). Nothing fills at a price we could not have traded at.
- **Costs [D4]:**
  - half-spread + slippage, in basis points, scaled by liquidity bucket. The default is 5 bps per side for the top 500, 2 bps for big ETFs.
  - SEC/FINRA fees.
  - borrow cost if shorting is ever enabled (it is off by default: cash account, long only).
  - every result is also reported at **2× costs**.
- **Sizing:** vol-targeted, with position caps, matching what the risk gateway will enforce.
- **Validation:**
  - purged and embargoed K-fold or CPCV for anything fitted.
  - walk-forward for reporting.
  - subperiod stability.
  - bootstrap confidence intervals.

### 7. Experiment registry (`src/trader/research/registry.py`)
SQLite at `experiments/registry.db`. One row per trial: strategy id, code git hash, params, data window, cost model, all metrics, and a `holdout_touched` flag. Reports go to `experiments/reports/<trial_id>.md` (+ equity-curve PNG). The deflated Sharpe uses the trial count *per strategy family*. PBO is computed via CSCV across each family's variants.

### 8. Live runner (`src/trader/live/`)
- Scheduled once per trading day (plus an optional midday check). Steps: refresh data → compute features → run the promoted, frozen strategy version → write a **target portfolio** (symbol → target weight) with a run id to `live/orders.db`.
- Stateless: everything is rebuilt from the broker and the DB on every start.

### 9. Risk gateway (`src/trader/risk/`)
A separate process with its own API keys. It is the only component allowed to call order endpoints.
- Reads the target portfolio, pulls actual positions/cash from Alpaca, and computes the order diff.
- Enforces `config/limits.yaml`:
  - max weight per name.
  - max gross exposure (≤ 100% equity; no margin, no shorts in phase 1).
  - max orders per run and per day.
  - max notional per order.
  - max daily turnover.
  - daily and rolling drawdown halts.
  - symbol allowlist (current universe only).
  - trading-hours window.
- Uses idempotent `client_order_id = hash(run_id, symbol, side)`, so retries never duplicate orders.
- **Kill switch:** the `KILL` file or `trader kill` command cancels all open orders and blocks new ones. `trader flatten` closes everything.
- Normal operation is fully unattended: the scheduler runs the strategy, the gateway submits orders, and alerts are informational.
- Breach handling [D9]:
  - Per-order violations, such as an oversized order or a symbol not in the universe: that order is refused, the rest proceed, and an alert goes out.
  - Daily loss halt: no new entries for the rest of the day. Trading resumes automatically next trading day.
  - Peak-drawdown halt, a reconciliation mismatch, or a duplicate-order detection: trading stops until a human clears it. These are the only cases that need you.

### 10. Monitoring (`src/trader/monitor/`)
- Daily report:
  - P&L vs SPY.
  - realized vs modeled slippage, per fill.
  - **live vs backtest replay of the same days** (tracking error).
  - LLM scoring stats (count, invalid rate, cost).
  - limit hits.
- Alerts via ntfy.sh (free push) or email: failed run, limit breach, reconciliation mismatch, data gaps.
- The `daily-review` skill (see below) turns this into a short written review.

## Tooling decisions

| Tool | Decision | Why |
|---|---|---|
| Python 3.12 via `uv` | **Use** | Pinned, reproducible environments. The system Python is 3.10. |
| Polars + DuckDB + Parquet | **Use** | Fast, local, no server; DuckDB queries the lake directly. |
| `alpaca-py` | **Use** | Official SDK for data, news, trading. |
| LightGBM, scikit-learn | **Use** | Gradient boosting is the best-evidenced ML family for return ranking. |
| Anthropic SDK + Pydantic | **Use** | LLM scoring with schema-validated output. |
| vectorbt / NautilusTrader / LEAN | **Not now** | Daily, long-only rebalancing is simple; our own engine keeps costs and timing explicit. Revisit Nautilus if we go intraday. |
| Time-series foundation models, RL, multi-agent LLM traders | **Not now** | Evidence is weak or negative after costs (see `docs/RESEARCH.md`). |
| **Alpaca MCP server (v2, pinned 2.4.0)** | **Use, read-only** | Lets Claude query quotes, bars, news, assets, and corporate actions during research and reviews. `ALPACA_TOOLSETS=stock-data,news,assets,corporate-actions` with paper keys. The `account`, `trading`, `watchlists`, and `locates` toolsets are excluded because they include write tools. It is never given trading tools: orders go only through the gateway. |
| MCP for DuckDB / SQLite | **Skip** | Claude can query DuckDB directly through the shell; adding an MCP server would add little. |
| RAG / vector DB | **Skip for now** | The research corpus is small enough for `docs/RESEARCH.md`. Revisit embeddings if we add filing-similarity features (e.g., 10-K change detection). |
| Claude Code skills | **Use** | `run-experiment`, `promote-strategy`, `daily-review` encode the methodology so it is followed every time. |
| Hosting | PC → VPS | Run on this PC with Task Scheduler through paper trading; move to a ~$5–10/mo Linux VPS before live money. |

## Repository layout

```
trader/
  CLAUDE.md                 # rules for Claude working in this repo
  .mcp.json                 # Alpaca MCP (read-only data toolsets)
  .claude/skills/           # run-experiment, promote-strategy, daily-review
  config/                   # settings.toml, limits.yaml, universe.yaml
  docs/                     # ARCHITECTURE, PLAN, RESEARCH, DECISIONS, STRATEGIES
  src/trader/
    data/ universe/ features/ signals/ llm/ backtest/ research/
    portfolio/ live/ risk/ broker/ monitor/ cli.py
  tests/
  data/        (gitignored) raw/, lake/, quality/
  experiments/ (gitignored db; reports committed selectively)
  live/        (gitignored) orders.db, logs/
```

## Secrets
- `.env` (gitignored) is the single source of secrets. Python reads it with python-dotenv. The Alpaca MCP server is started by `scripts/alpaca-mcp.ps1`, which reads the data keys from `.env`, so no user-level environment variables are needed. `scripts/setup-env.ps1` reports which keys are set and never prints values.
- The Anthropic key is `TRADER_ANTHROPIC_API_KEY`, so it never overrides Claude Code's own login. It is billed to the Claude Console's prepaid API credits, not to claude.ai subscription credits.
- HTTP errors are raised with the query string removed (`trader.data.http.HttpError`), and CLI tracebacks don't show locals, so keys in request parameters can't leak into logs.
- Separate key pairs:
  - `ALPACA_DATA_*` for research and the MCP server.
  - `ALPACA_TRADE_*` for the gateway only.
  - paper and live are separate accounts.
- Live trading keys exist only on the production host.
- None of the keys have withdrawal permissions. Alpaca API keys cannot move money out, but verify that for the account type.
