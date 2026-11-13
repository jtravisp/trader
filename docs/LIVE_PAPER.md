# Running live and paper side by side

Plan for P9 (D34). Live starts with $200 on FALLBACK (100% SPY). Paper keeps running on its own account, first on FALLBACK, later on whatever candidate is under forward test. Nothing here is built yet. Live orders need the user's explicit approval (PLAN.md P9 step 5).

## Goals
1. **Isolation:** a problem in one account can never trade, halt or reconcile the other.
2. **Live comes first:** every check, alert and review leads with the live account.
3. **One comparison per day:** a single report shows live, paper and SPY together, plus how far each drifted from its own model.
4. **No new manual steps:** the evening and morning jobs cover both accounts.

## 1. Accounts and state

An `accounts` table in `config/settings.toml` replaces the single `[live]` mode:

```toml
[accounts.paper]
enabled = true
strategy = "FALLBACK"      # later: the candidate under forward test
key_prefix = "ALPACA_PAPER"
[accounts.live]
enabled = false            # true only after the user approves (D34)
strategy = "FALLBACK"      # stays SPY until a strategy passes P7-equivalent forward test + P8
key_prefix = "ALPACA_LIVE"
```

- **Keys:**
  - Paper uses `ALPACA_PAPER_TRADE_KEY/SECRET`; live uses `ALPACA_LIVE_TRADE_KEY/SECRET`. The old `ALPACA_TRADE_*` names still work for paper, so nothing breaks before the move.
  - `env-check` reports each account separately.
  - The broker refuses to start if the endpoint doesn't match the account name: live keys on `paper`, or paper keys on `live`. This replaces today's `mode` check.
- **State:**
  - Each account gets its own `live/<account>/` folder holding `orders.db`, `reports/`, `logs/` and `KILL`.
  - The current `live/orders.db` and `live/reports/` move to `live/paper/` unchanged, so the P4 record and the equity peak carry over.
  - The kill switch works per account. `trader live kill --account live` stops only live, and `--all` stops both.
- **CLI:** every `trader live …` command takes `--account paper|live` and defaults to `paper`. A command that sends orders with `--account live` also needs `enabled = true`.
- **Limits:** the existing `limits.yaml` limits apply to both accounts. The only difference is in `on_breach`: paper keeps alert-and-continue at the peak-drawdown halt (D29), and live halts until a human clears it.

## 2. Jobs

| Job | Steps (each account's failure is isolated and returns nonzero at the end) |
|---|---|
| forward, 18:00 CT daily | `forward` (data + LLM scoring, once) → for each enabled account: `live target`, `live report` → `live compare` |
| gateway, 08:40 CT Mon–Fri | for each enabled account, **live first**: `live execute --submit --account X` |

- The panel is computed once and shared. Each account gets a target for its own strategy.
- The gateway job sleeps until 09:40 ET the same way it does today. Live goes first, then paper about a minute later, so the two never compete for the same minute bar in the slippage reference.
- **Heartbeats:**
  - `HC_FORWARD_URL` stays as it is.
  - The gateway gets one ping per account, `HC_GATEWAY_LIVE_URL` and `HC_GATEWAY_PAPER_URL`, so a dead live run can't hide behind a healthy paper run.

## 3. Daily comparison (`trader live compare`)

The command writes `live/reports/compare/<date>.md` and appends a line to `compare.jsonl`. It only reads, and it reuses each account's daily report record.

| | live | paper | SPY |
|---|---|---|---|
| equity, day return, month to date, since live start | ✓ | ✓ | ✓ |
| execution error vs own model (P4 measure, bp) | ✓ | ✓ | |
| slippage per fill vs model (bp) | ✓ | ✓ | |
| fills, refusals, deferrals, halts | ✓ | ✓ | |
| largest weight difference vs target | ✓ | ✓ | |

There are three numbers to watch:
1. **Live execution error.** This is the P4 measure applied to live: |error| < 10 bp on 90% of fill days, over a rolling 20 fill days. A day breaching it triggers a warning. Breaching the rolling bound is a 🔴 review.
2. **Live minus paper** (only while both run the same strategy).
   - On FALLBACK both should equal SPY minus cash drag. A daily gap above 15 bp, or a cumulative gap above 1%, triggers a warning, because a gap that size points to a fill, settlement or sizing difference.
   - Once paper runs a different candidate, this row switches to "live vs SPY" and "paper vs its backtest band" (the P8 band).
3. **Live vs SPY (tracking).** Live on FALLBACK should track SPY within cash drag. This is the P9 drift rule: live outside the 95% band for 20 days → revert to SPY. On FALLBACK it degenerates to "the cash drag stays under 2%".

The 18:00 CT summary push becomes one message: `live +0.42% · paper +0.41% · SPY +0.43% · TE 2bp/3bp · no halts`.

## 4. Alerts

- Every alert is tagged `[LIVE]` or `[paper]`. Live alerts go out one priority higher; a live warning is pushed as an error.
- The alert log moves to the account folder, `live/<account>/logs/alerts.log`. `live/logs/alerts.log` is kept as a combined feed for the review skill.
- **Live-only checks:**
  - **Unsettled-cash refusals:** buys deferred because cash hasn't settled are expected after a sell. A third straight day of them is a warning.
  - **Equity marked from Alpaca vs our own mark:** a gap above $0.50 is a warning. This catches fees and corporate actions.
  - **Live account type and margin:** Alpaca must report a cash account with no margin, checked each run. Anything else is critical and halts.
  - **Tax fill log:** every live fill also goes to `live/live/fills_tax.csv` (date, symbol, qty, price, proceeds/cost, lot id) for the 1099-B cross-check (P9).

## 5. Review routine

- **`daily-review`:** the skill covers both accounts, live first. The status line is the worst of the two, with any live problem ranked above any paper one.
- **Weekly (Fridays):** add the live-vs-paper cumulative gap and the rolling live execution error to the existing weekly section.
- **Monthly:** a short `docs/reviews/<month>-live.md` with live vs paper vs SPY, incidents, slippage, and whether the D34 conditions still hold. These monthly notes are the evidence for any later scale-up (3 months live tracking paper, PLAN P9).

## 6. Tests (all in CI on three OSes)

- **Isolation:**
  - A live gateway run against a temp tree never opens `live/paper/orders.db`, and the reverse.
  - A kill on one account leaves the other trading.
- **Key routing:** paper keys under `--account live` are refused, as are live keys under `paper`. A disabled live account can't submit.
- **Migration:** the old `live/orders.db` layout moves to `live/paper/` with history, the equity peak and halts intact. Running it twice does nothing.
- **Compare:** pure-function tests for the live-minus-paper gap, the rolling execution-error bound and the summary line.
- **Jobs:** a failing paper step still runs the live step, the job still exits nonzero, and live runs before paper.

## 7. Rollout

1. **After P4 passes:** build the account refactor and migration with paper only (live disabled). Merge it before an evening run and confirm one full evening + morning cycle is unchanged: same orders, same report, P4 record intact.
2. **The user opens the cash account and deposits $200.** I add the live key names to `.env.example` and the user fills in the values in `.env`. `env-check` passes for both accounts.
3. **Dry run:** `trader live target --account live` plus `live execute --account live` without `--submit`. Expected result: buy about $199 of SPY in two chunks (60% turnover cap), no refusals.
4. **User approval in conversation** → `enabled = true`, merged before the evening run. The next 09:40 run is watched live, then reviewed with `daily-review`.
5. **First two weeks:** the comparison report runs daily, and I go over it with the user each Friday. After that it moves to the normal weekly and monthly cadence.
