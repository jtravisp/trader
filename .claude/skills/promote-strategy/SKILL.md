---
name: promote-strategy
description: Gate review for moving a strategy between stages (research → paper → live, or scaling live capital). Use when the user asks to promote, deploy, go live, scale up, or start paper trading a strategy.
---

# Promote a strategy

Stages: `research → paper → live-small → live-full`. Promotion requires passing **every** criterion for the target stage. Never relax a criterion mid-review. If the user wants to change one, update `docs/PLAN.md` and log it in `docs/DECISIONS.md` first.

## research → paper
- [ ] The P3 or P5 gate (docs/PLAN.md) passes. Cite the trial IDs.
- [ ] The P6 gate passes if LLM features are used. Otherwise, remove them.
- [ ] Code frozen: a git tag `strategy/<ID>/v<N>`. The live runner pins this version.
- [ ] `config/limits.yaml` reviewed. Position caps match backtest sizing.
- [ ] The P4 failure drills passed on the current gateway version.

## paper → live-small
- [ ] The P7 holdout test passed (one shot, recorded in the registry).
- [ ] ≥ 60 paper trading days on the frozen version.
- [ ] Paper returns fall within the backtest's 90% band for the same period. Use the replay comparison from `monitor`.
- [ ] Realized slippage ≤ the modeled cost, per fill on average.
- [ ] No unexplained incidents: reconciliation mismatches, duplicate orders, or limit breaches.
- [ ] Live account is a separate cash account. Deposit = the max acceptable loss. Live keys exist only on the production host.
- [ ] The user has explicitly approved going live in this conversation. **Never infer approval.**

## live-small → scale-up
- [ ] ≥ 3 months live at the current size.
- [ ] Live tracks paper within bounds. Live slippage ≤ the model's.
- [ ] No drawdown-halt events, or each one is fully explained.
- [ ] Raise size by at most 2× per step.

## Output
Write a short review to `docs/reviews/<date>-<ID>-<stage>.md` with a checklist and evidence links, then give the user a pass/fail summary. On fail, state the specific criterion and what would fix it.
