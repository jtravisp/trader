# trader

A systematic US stock and ETF trading system that holds positions for days. It is a research project run on a small paper account (Alpaca). **It is not investment advice, and nothing here is a recommendation to buy or sell anything.**

The working assumption is that there is no edge until a strategy proves one. Every strategy must clear pre-declared gates (`docs/PLAN.md`) at base and 2× trading costs, measured against SPY. If none clears, the money sits in SPY.

## How it works

- **Research:** point-in-time data lake (Parquet + DuckDB) → features → signals → backtest engine. Every backtest, including quick checks and failures, is logged to an experiment registry so overfitting statistics (deflated Sharpe, PBO) use the true number of trials.
- **Live:** a nightly job refreshes data, runs the same signal code, and writes a target portfolio. A morning job hands it to the **risk gateway** (`src/trader/risk/`), the only code allowed to place orders. The gateway checks the target against hard limits (`config/limits.yaml`), reconciles with the broker, and supports a kill switch.
- **LLM features:** news is scored by Claude into schema-validated features. The LLM has no order power. Evidence for LLM features only counts if it comes from documents published after the scoring model's training cutoff.

Paper currently holds 100% SPY (FALLBACK): S03, 12-1 momentum on the top-500 universe, passed research but failed the one-time holdout test (D33). Results and failures for all strategies are in `docs/STRATEGIES.md`.

## Docs

| File | What's in it |
|---|---|
| `docs/ARCHITECTURE.md` | System design and principles |
| `docs/PLAN.md` | Phases and the gates each must pass |
| `docs/DECISIONS.md` | Binding design decisions (D1, D2, ...) |
| `docs/STRATEGIES.md` | Every strategy, its declared grid, and its result |
| `docs/LIVE_PAPER.md` | Plan for running and monitoring the live and paper accounts side by side |
| `docs/RESEARCH.md` | Evidence from the literature, with strength ratings |

## Setup

Runs on Windows, macOS and Linux (CI tests all three). Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). On macOS, LightGBM also needs OpenMP: `brew install libomp`.

```sh
uv sync
uv run trader env-check   # creates .env from .env.example; fill in your own keys, then re-run
uv run pytest
uv run trader --help
```

`env-check` lists which keys are set and never prints their values. Claude Code's Alpaca MCP server (`.mcp.json`, read-only data tools) uses the same `.env`.

## Scheduling the live jobs

Two jobs run the system unattended. Both call `uv run --no-sync trader job <name>`, which runs each step and logs to `data/logs/<name>_<date>.log`.

| Job | When (New York time) | Steps |
|---|---|---|
| `forward` | daily 19:00 | forward data + LLM scoring, daily report, tomorrow's target |
| `gateway` | weekdays 09:40 | the risk gateway executes the latest target (paper unless `live_mode = "live"`) |

**Windows:** run `scripts/register-tasks.ps1` from an elevated PowerShell. The tasks run whether or not you're logged in, so a Windows Update reboot overnight doesn't skip the morning run. The schedule assumes the PC's clock is on Central time; edit the times in the script if it isn't.

**macOS:** run `scripts/macos/install-launchd.sh`. It converts the New York times to the Mac's local time.
- By default it installs LaunchAgents, which run only while you're logged in. `sudo scripts/macos/install-launchd.sh --daemon` installs LaunchDaemons instead, which run whether or not anyone is logged in.
- A sleeping Mac runs a missed job only when it next wakes, and a closed-lid laptop won't wake for it. A late evening job is worse than a skipped one: run the next morning, it treats that day as its trading day, before the close, so it works from incomplete data. Keep the Mac on power with sleep off: `sudo pmset -c sleep 0` (the display can still sleep).
- Remove with `--uninstall` (add `--daemon` if you installed daemons).

**Linux:** add both commands to your crontab with `CRON_TZ=America/New_York` (cronie), e.g. `40 9 * * 1-5 cd /path/to/trader && uv run --no-sync trader job gateway`.

To be alerted when a job stops running entirely (PC off, task broken), set `HC_FORWARD_URL` and `HC_GATEWAY_URL` in `.env` to [healthchecks.io](https://healthchecks.io) ping URLs. Set `NTFY_TOPIC` for push alerts on halts and errors.

## Getting the data

Market data is not in the repo: the lake is about 5 GB, and the vendors' terms don't allow redistribution. You build your own copy under `data/` (gitignored) with your own keys. Every loader refuses rows inside the locked holdout window (`holdout_start` to `holdout_end` in `config/settings.toml`), so a fresh backfill can't leak it.

| Source | Cost | Key in `.env` | Used for |
|---|---|---|---|
| Alpaca (paper account) | free | `ALPACA_DATA_KEY`, `ALPACA_DATA_SECRET` | Daily and minute bars, news, corporate actions |
| SEC EDGAR | free | `SEC_USER_AGENT` (a name and contact email, required by the SEC) | Filings and XBRL fundamentals |
| FRED | free | `FRED_API_KEY` | Macro regime series |
| Massive (formerly Polygon) | free tier; Starter $29 for one month (D3) | `MASSIVE_API_KEY` | Delisted tickers and month-end listings for the survivorship-free universe |
| Anthropic | pay per use, a few $/month | `TRADER_ANTHROPIC_API_KEY` | LLM news scoring (optional) |

The free Alpaca plan can't query the most recent 15 minutes of consolidated (SIP) data, so the forward job runs after the close.

Typical first build, in order:

```sh
uv run trader ingest all            # Alpaca bars, minute bars, news (long; resumable)
uv run trader ingest edgar
uv run trader ingest fred
uv run trader ingest massive        # reference data, ~4 h on the free tier
uv run trader build-universe        # point-in-time universe (D20, D21, D25)
uv run trader quality               # data checks; exits 1 on unexplained failures
```

`uv run trader <command> --help` documents each step.

## Running trials

The rules (also in `CLAUDE.md`) exist to keep the backtests honest:

1. **Declare first.** Add the strategy, its hypothesis and its parameter grid to `docs/STRATEGIES.md` (and the grid to `src/trader/research/grids.py`) before running anything.
2. **Run through the registry.** Use the CLI, never an ad hoc script, so every trial is counted:
   ```sh
   uv run trader grid --strategy S03 --dry-run   # list grid points not yet run
   uv run trader grid --strategy S03             # run them
   uv run trader backtest --strategy S03 --params '{"lookback": 126, "skip": 21}'
   ```
   Each point runs three times: base costs, 2× costs, and fills delayed one day. All three are logged.
3. **Judge against the gate** in `docs/PLAN.md` (P3): Sharpe vs SPY at base and 2× costs, alpha after beta, deflated Sharpe and PBO across every variant tried. Record the verdict in `docs/STRATEGIES.md`, including failures.
4. **Never touch the holdout** until the single P7 run.

### Published results

- `experiments/trials.csv`: every trial ever run (one row each, failures and superseded runs included), with parameters, git commit, status and metrics. It is regenerated from the local registry (`experiments/registry.db`) after every trial, so the trial counts behind the overfitting statistics can be checked.
- `experiments/reports/`: a Markdown report and equity-curve chart per backtest command.
- `docs/STRATEGIES.md`: the verdict on each strategy.

The daily return series behind each trial (`experiments/returns/`) are not published.

## License

MIT. See `LICENSE`.
