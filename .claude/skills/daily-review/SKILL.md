---
name: daily-review
description: Review the trading system's daily run (paper or live). Checks fills, P&L vs SPY, slippage vs model, risk limit events, reconciliation, data and LLM-scoring health. Use when the user asks how the bot did, for a daily/weekly review, or after an alert.
---

# Daily review

## Gather
1. `uv run trader report daily --date <YYYY-MM-DD>` (defaults to the last trading day).
2. Read `live/logs/<date>/` for errors and warnings.
3. Use the Alpaca MCP server (read-only data tools) only to cross-check quotes or bars if a fill looks off. Never use order tools.

## Check, flagging anything abnormal
- **Run health:** did each scheduled job run and finish? Was the data refresh complete, with no missing symbols?
- **Reconciliation:** broker positions = expected positions after fills. Any mismatch is **critical**.
- **Orders:** count vs limits, rejected orders, partial fills, duplicate `client_order_id`s.
- **Slippage:** realized fill price vs the decision-time quote, in bps, compared with the cost model (D4). Flag any fill worse than 3× the model.
- **P&L:** today's and month-to-date P&L vs SPY, and vs the backtest replay of the same day (tracking error).
- **Risk:** current drawdown from peak vs the halt thresholds. Any gateway refusals.
- **LLM scorer:** items scored, invalid-output rate, spend today, any risk-flag vetoes triggered.

## Output
Under 15 lines. A status line (🟢 / 🟡 / 🔴), then the numbers, then any actions needed. On 🔴, recommend `trader kill` if positions are at risk, but never run it without the user's confirmation unless the gateway has already halted.

Weekly (Fridays): also compare the week's returns with the backtest's expected distribution, and update the running tracking-error statistic used by the promote-strategy gate.
