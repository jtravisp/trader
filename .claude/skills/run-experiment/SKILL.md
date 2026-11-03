---
name: run-experiment
description: Run, log and evaluate a backtest experiment for a trading strategy in this repo. Use whenever testing a strategy idea, parameter set, feature, or model, including quick checks, so every trial is counted for overfitting statistics.
---

# Run an experiment

Follow every step. Skipping registry logging corrupts the deflated Sharpe and PBO for the whole family.

## 1. Pre-flight
- Confirm the strategy ID and its parameter grid exist in `docs/STRATEGIES.md`. If not, add them **before** running, along with the hypothesis.
- If the grid needs to grow, append the new values to the table. The family trial count goes up.
- Confirm the data window ends before `HOLDOUT_START` (`config/settings.toml`). Never pass `unlock_holdout=True`.
- For LLM-feature experiments, confirm the evaluation window starts after the scoring model's training cutoff (decision D5). Pre-cutoff results may be reported as "contaminated", but they can't count toward any gate.

## 2. Run
- Use `uv run trader backtest --strategy <ID> --params <json> --costs base` and repeat with `--costs 2x`. Both runs register automatically.
- Use purged/embargoed CV (CPCV) for anything with fitted parameters. The embargo must be at least the label horizon.

## 3. Evaluate
Report a compact table:

| metric | base costs | 2× costs | SPY same window |
|---|---|---|---|

Rows: CAGR, Sharpe, Sortino, max drawdown, annual turnover, cost drag, % positive months, correlation to SPY, and the beta-adjusted alpha of a 50/50 blend with SPY.

Then add:
- the deflated Sharpe probability using the **family** trial count from the registry.
- the PBO (CSCV) once the family has ≥ 4 variants.
- the subperiod table (thirds of the sample).
- a check against the P3 or P5 gate in `docs/PLAN.md`: pass or fail on each criterion.

## 4. Red flags (investigate before believing a good result)
- Sharpe > 2 on daily data, or a smooth equity curve with no drawdowns.
- Most of the profit comes from a few days or a few names.
- The result vanishes at 2× costs or with a one-day execution delay. Always run the delay check.
- Performance concentrated in pre-2018 data only, which suggests decay.
- Any feature that could encode future information, such as revised fundamentals or a survivorship-biased universe.

## 5. Record
- Append a one-line conclusion to the strategy's row in `docs/STRATEGIES.md` (status + trial IDs).
- If the result changes beliefs, add it to `docs/RESEARCH.md`.
