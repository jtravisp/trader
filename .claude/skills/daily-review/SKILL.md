---
name: daily-review
description: Review the trading system's daily run (paper or live). Checks fills, P&L vs SPY, slippage vs model, risk limit events, reconciliation, data and LLM-scoring health. Use when the user asks how the bot did, for a daily/weekly review, or after an alert.
---

# Daily review

## Gather
1. Per enabled account (`[accounts.*]` in config/settings.toml; **live first** when enabled): `uv run trader live --account <name> report --day <YYYY-MM-DD>` (default today) writes `live/<name>/reports/<date>.md`; read it. `uv run trader live --account <name> gate` and `... halts` give the P4 gate and any halts. Lead the summary with the live account.
2. Read the job logs for errors and warnings: `data/logs/forward_<date>.log` and `data/logs/gateway_<date>.log` (one per scheduled run; a missing gateway log on a weekday means the run never started), `data/logs/scheduler.log` (scheduler/uv errors) and `live/logs/alerts.log` (combined feed, every line tagged with its account). Check the scheduler itself: Windows `Get-ScheduledTask trader-* | Get-ScheduledTaskInfo` (LastRunTime, LastTaskResult); macOS `launchctl print gui/$(id -u)/local.trader.gateway` (or `system/...` for the daemon install).
3. `live/reports/compare/<date>.md` (`uv run trader live compare`, run by the evening job): live vs paper vs SPY, live execution error, live minus paper, cash drag, and its warnings. Each account report's **Checks** section has the live-only checks (equity mark vs Alpaca, buying-power deferral streak, largest weight difference vs target, tax fill log).
4. `uv run trader shadow report` writes `live/shadow/report.md`: data health of the shadow forward tests (D38). Flag late or error rows, or a missing row for the last session. **Don't** run it with `--force` in a routine review: performance stays hidden until each candidate's read.
5. Use the Alpaca MCP server (read-only data tools) only to cross-check quotes or bars if a fill looks off. Never use order tools.

## Check, flagging anything abnormal
- **Run health:** did each scheduled job run and finish? Was the data refresh complete, with no missing symbols?
- **Reconciliation:** broker positions = expected positions after fills. Any mismatch is **critical**.
- **Orders:** count vs limits, rejected orders, partial fills, duplicate `client_order_id`s.
- **Slippage:** realized fill price vs the decision-time quote, in bps, compared with the cost model (D37: stocks 15, ETFs 2 bps per side). Flag any fill worse than 3× the model. `trader live --account <name> costs` recalibrates from all fills.
- **P&L:** today's and month-to-date P&L vs SPY, and vs the backtest replay of the same day (tracking error).
- **Risk:** current drawdown from peak vs the halt thresholds. Any gateway refusals.
- **LLM scorer:** items scored, invalid-output rate, spend today, any risk-flag vetoes triggered.

## Output
Under 15 lines. A status line (🟢 / 🟡 / 🔴), then the numbers, then any actions needed. On 🔴, recommend `trader kill` if positions are at risk, but never run it without the user's confirmation unless the gateway has already halted.

Weekly (Fridays): also compare the week's returns with the backtest's expected distribution, and update the running tracking-error statistic used by the promote-strategy gate.
