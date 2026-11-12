# trader

A systematic US stock and ETF trading system that holds positions for days. It is a research project run on a small paper account (Alpaca). **It is not investment advice, and nothing here is a recommendation to buy or sell anything.**

The working assumption is that there is no edge until a strategy proves one. Every strategy must clear pre-declared gates (`docs/PLAN.md`) at base and 2× trading costs, measured against SPY. If none clears, the money sits in SPY.

## How it works

- **Research:** point-in-time data lake (Parquet + DuckDB) → features → signals → backtest engine. Every backtest, including quick checks and failures, is logged to an experiment registry so overfitting statistics (deflated Sharpe, PBO) use the true number of trials.
- **Live:** a nightly job refreshes data, runs the same signal code, and writes a target portfolio. A morning job hands it to the **risk gateway** (`src/trader/risk/`), the only code allowed to place orders. The gateway checks the target against hard limits (`config/limits.yaml`), reconciles with the broker, and supports a kill switch.
- **LLM features:** news is scored by Claude into schema-validated features. The LLM has no order power. Evidence for LLM features only counts if it comes from documents published after the scoring model's training cutoff.

The current paper strategy is S03, 12-1 momentum on the top-500 universe. Results and failures for all strategies are in `docs/STRATEGIES.md`.

## Docs

| File | What's in it |
|---|---|
| `docs/ARCHITECTURE.md` | System design and principles |
| `docs/PLAN.md` | Phases and the gates each must pass |
| `docs/DECISIONS.md` | Binding design decisions (D1, D2, ...) |
| `docs/STRATEGIES.md` | Every strategy, its declared grid, and its result |
| `docs/RESEARCH.md` | Evidence from the literature, with strength ratings |

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
cp .env.example .env    # then fill in your own keys (Alpaca paper, Anthropic, etc.)
uv run pytest
uv run trader --help
```

On Windows, `scripts/register-tasks.ps1` registers the nightly and morning jobs with Task Scheduler. Run it from an elevated PowerShell.

## License

MIT. See `LICENSE`.
