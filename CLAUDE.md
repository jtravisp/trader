# trader: Claude working rules

This is a systematic US stock and ETF trading system. Positions are held for days. Read `docs/ARCHITECTURE.md` and `docs/PLAN.md` first. The decisions in `docs/DECISIONS.md` are binding unless the user changes them.

## Non-negotiables
1. **Trading is fully automated, and orders only go through the gateway.** The system trades unattended (D9). Strategies and models output target portfolios. Only `src/trader/risk/` (the gateway) may call Alpaca order endpoints. Never add order calls anywhere else. Never use trading tools from the Alpaca MCP server.
2. **Never touch the holdout.** Data with `known_ts >= HOLDOUT_START` (config/settings.toml) is off-limits. Don't load it, plot it, or "peek" at it. The only exception is the P7 holdout run, which needs explicit user approval.
3. **Log every trial.** Every backtest goes through the experiment registry, including quick checks and failures. Use the `run-experiment` skill.
4. **Declare before testing.** Add a strategy and its parameter grid to `docs/STRATEGIES.md` before running it.
5. **Use point-in-time data only.** Joins use `known_ts <= decision_ts`. Fills happen at the next tradable price, never at the signal bar's close unless the strategy explicitly models MOC orders.
6. **LLM look-ahead.** Go/no-go evidence for LLM features must come from documents published after the scoring model's training cutoff (D5).
7. **Never commit secrets.** Keys live in `.env`. Never print them or write them to logs.
8. **Report honestly.** Report results at base and 2× costs, alongside SPY. Say so plainly when something fails a gate.

## Stack
Python 3.12 via `uv`, with Polars, DuckDB, Parquet, alpaca-py, LightGBM, Pydantic, and the Anthropic SDK.
- Run tests: `uv run pytest`
- Run the CLI: `uv run trader <cmd>`

## Conventions
- Write features and signals as pure functions, with no I/O and no wall-clock reads.
- Timestamps are timezone-aware: UTC for storage, America/New_York for trading-day logic.
- Every new data source gets a quality check in `src/trader/data/quality.py`.
- Record new evidence in `docs/RESEARCH.md` with a source and a strength rating.
