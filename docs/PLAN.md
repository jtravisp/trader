# Phased Plan

Status: draft v1 (2026-11-03). Every phase ends with a **gate**. Failing a gate means you fix the problem, go back a phase, or stop. You never skip a gate.

Budget: about $25/mo for data, going over only for a clear advantage. Expected run-rate:
- Phases 1–2: $0 (Alpaca free tier plus EDGAR and FRED).
- Phase 3: a one-time $29 for one month of Massive Starter, to bulk-download 5 years of survivorship-free history, then cancel (D3 revised).
- LLM scoring: capped at $3/mo in code (D11).

## Timeline at a glance

```
Week:      1  2  3  4  5  6  7  8  9 10 11 12 13 ... 24+
P0 setup   ■
P1 data       ■  ■
   forward LLM scoring ───────────────────────────────────▶ (runs continuously)
P2 harness       ■  ■
P3 baselines        ■  ■  ■
P4 live stack          ■  ■  ■   → paper-trade best baseline ────────────▶
P5 ML ranker              ■  ■  ■  ■
P6 LLM features              ■  ■  ■  ■
P7 holdout + selection                  ■
P8 paper trading (final candidate)        ■ ■ ■ ■ ■ ■ ■ ■ ■ ■ ■ ■  (≥ 3 months)
P9 live small                                                     ■ ...
```

Forward collection (LLM scores, paper fills) starts early. Elapsed time is the bottleneck, not coding.

---

## P0 — Setup (this week)
- [x] Research synthesis (`docs/RESEARCH.md`), architecture, plan, skills, CLAUDE.md
- [x] Alpaca account. Create **paper** keys, with separate data and trade key pairs.
- [x] Anthropic API key for LLM scoring
- [x] `git init` and a private GitHub remote
- [x] `uv` project on Python 3.12, with lint, test, and type-check tooling

**Gate:** `uv run pytest` passes on an empty test suite, and the Alpaca MCP can return a SPY quote.

## P1 — Data foundation (weeks 1–2)
- Ingest:
  - Alpaca daily bars from 2016 for the ETF universe plus current top ~1000 stocks.
  - Minute bars for the ETFs only, for slippage modeling.
  - Alpaca news from 2015.
  - EDGAR 8-K index plus XBRL company facts.
  - FRED series.
- Build the Parquet lake with `event_ts` and `known_ts` columns, plus the holdout guard.
- **Holdout = 2025-11-01 → 2026-11-01**, locked. New data after today counts as forward data.
- Run data-quality checks:
  - Compare split/dividend adjustment against a second source for 20 random names.
  - Check for bar gaps.
- **Survivorship check:** compare an Alpaca universe for one past date against the list of names that have since delisted. If the gap is material, which is expected, subscribe to Massive Starter ($29) in P3.
- **Start forward LLM scoring:** a daily job scores new news items and caches them. Measure cost per day.

**Gate:**
- SPY total return computed from the lake matches a published figure within 0.5%/yr.
- Zero unexplained quality failures.
- The forward scoring job has run for 5 consecutive days.

**Status (2026-11-03):**
- [x] Lake with `event_ts`/`known_ts` and the holdout guard (D12). The holdout year is never written. Tests: `tests/test_lake_guard.py`.
- [x] Universe seed: 1000 stocks + 30 ETFs (D14).
- [x] Daily bars 2016-01-04 → 2025-10-31, raw + adjusted, 1030 symbols. Forward bars from 2026-11-02.
- [~] Minute bars (ETFs, 2021+): backfilling.
- [~] News 2015+ (headline + summary): backfilling, about 3.5 h.
- [x] EDGAR: 1.41M filings (39k earnings 8-Ks with acceptance times) + 2.05M XBRL facts for 992 CIKs.
- [x] FRED: 15 series, point-in-time (D15, D17).
- [x] Quality checks (`trader quality`). Known Alpaca adjustment defects at ticker reuse, spin-offs and redomiciles are documented in `config/quality_exceptions.yaml`.
- [x] Survivorship check: **gap confirmed.** 19/48 delisted names can be enumerated from Alpaca, but 45/48 still have bars. The fix needs the *list* of historical tickers.
- [x] Forward job (`trader forward`): data + LLM scoring work (`score-smoke` passes, ~$0.00008/item via batch). Scheduled daily at 18:00 CT (`trader-forward` task). Gate clock started 2026-11-03.
- Gate 1 (SPY within 0.5%/yr): **PASS** for 2017–2024. 2016 can't be checked (no 2015 base bar).

**Follow-up once a strategy passes P3 (noted 2026-11-03):** decide intraday protection. The live system acts once a day (decide after the close, trade at 09:40 ET), so nothing reacts to an intraday crash until the next run. Backtest the passing strategy with and without (a) protective stop orders resting at Alpaca (these work even when the host is down) and (b) a midday reduce-only gateway check. Adopt either only if it improves risk-adjusted results net of costs and whipsaw.

**Carried into P3 from P1:**
- Rebuild split/dividend adjustment factors from raw bars + Massive corporate actions, then diff against Alpaca's `adjustment=all` for every name. Alpaca double-counts dividends on reused tickers (DOC), and its spin-off handling is suspect (BDX).
- Build the point-in-time universe with the delisted-ticker list. Alpaca already serves most delisted names' bars, so the Massive month mainly buys the list and the corporate actions.

## P2 — Backtest harness (weeks 2–3)
- Build:
  - the engine (next-open fills).
  - the cost model (base cost and 2×).
  - vol-targeted sizing with caps matching `limits.yaml`.
  - metrics, purged K-fold/CPCV, deflated Sharpe, PBO via CSCV, bootstrap confidence intervals.
  - the experiment registry and the report generator.
- Run sanity tests:
  - Buy-and-hold SPY reproduces P1's number.
  - A random signal has Sharpe ≈ 0 after costs, across 100 seeds.
  - A deliberately leaky signal (using tomorrow's return) is caught by the timing test.
  - Textbook 12-1 momentum on the universe gives a plausible result: positive but noisy, in line with the literature.

**Gate:** all sanity tests pass, and the leak test fails as it should.

**Status (2026-11-03): gate PASSED.**
- Built: engine (next-open fills, delay option, gateway caps + min drift), cost model (bps/side + SEC fee, base and 2×), vol-target sizing, metrics, PSR/DSR, PBO (CSCV), stationary bootstrap, CPCV splitter (purge + embargo), SQLite registry with per-trial returns, markdown + PNG reports, a truncation leak check on every run, and `trader backtest` / `trader sanity`.
- Sanity: SAN01 exact, SAN02 IR −0.05 ± 0.03, SAN03 leak caught, 12-1 momentum positive (inflated by the biased seed). See `experiments/reports/P2_sanity.md`.

## P3 — Rule baselines (weeks 3–5)
Use the `run-experiment` skill for every trial. Candidates:
1. ETF trend + vol targeting
2. Cross-sectional short-term reversal (weekly)
3. Medium-term momentum (monthly)
4. Earnings drift from EDGAR 8-K 2.02 plus the announcement-day reaction

Keep the parameter grids small and pre-declared in `docs/STRATEGIES.md` before running.

**Status (2026-11-03):**
- S01 (ETF trend): **failed** the gate (DSR 0.94, PBO 0.86, Sharpe below SPY). Not a paper-trading candidate as a standalone edge.
- **Point-in-time universe built (2026-11-04):** 109 snapshots, top 1000, delisting returns (D24), bars clipped to listing windows (D25). Quality is clean.
- S02 (reversal) and S04 (earnings drift): **failed**. No alpha after beta.
- S03 (momentum): **passes the gate as declared**, but it is mostly beta (1.27). CAPM alpha t is 1.6. See RESEARCH.md. It is the best P3 baseline, so it is the candidate for paper trading via `promote-strategy`. Paper trading costs nothing, and it tests the stock path of the live stack.
- S05 (LightGBM ranker): **failed** (best Sharpe 0.76 vs SPY 0.92, PBO 0.69).
- S03 robustness (lookback 63/189) and the overlays S07 (vol target) and S08 (stops): S03's family PBO is 0.17, so the gate now fully passes. Neither overlay is adopted, and there's no intraday protection (D27). **S03 v1 is on paper (D26).**
- 2026-11-05: S09 (daily reversal), S10 (quality), S11 (low vol) and S12 (sector momentum) all **failed**; none beats SPY or shows alpha after beta. S03 stays the only passing baseline.
- P5/P6 groundwork, built ahead of time:
  - Feature library: price, event, fundamental (point-in-time XBRL TTM and growth per filing) and FRED regime.
  - The S05 walk-forward LightGBM ranker, declared (12 trials) and tested on a planted signal.
  - S06 stage 1, the forward LLM event study, declared, with a single read at 60 sessions.
  - XBRL facts for the full universe are downloading.

**Gate (per strategy), on in-sample + validation (pre-holdout) data, net of base costs:**
- Deflated Sharpe probability ≥ 0.95, using the family trial count.
- PBO ≤ 0.30.
- Sharpe > 0 under 2× costs.
- Positive in at least 2 of 3 subperiods.
- Correlation to SPY reported. A low-correlation strategy with a lower Sharpe can still pass if it improves a SPY blend.

The best passing baseline goes to paper trading as soon as P4 is ready. That starts the forward clock early.

## P4 — Live stack on paper (weeks 4–6, parallel)
- Build:
  - the runner and the risk gateway with `limits.yaml`.
  - reconciliation and idempotent orders.
  - the kill and flatten commands.
  - monitoring, alerts, and the daily report.
  - Task Scheduler jobs.
- Run failure drills:
  - Kill the process mid-run, then restart. It must reconcile, with no duplicate orders.
  - Feed it a corrupted target. The gateway must refuse it.
  - Simulate a limit breach. Trading must halt and an alert must go out.
  - Test the kill switch.
- Start paper trading the best P3 baseline.

**Gate:** all drills pass, 10 consecutive clean trading days, and live-vs-backtest replay tracking error is within an agreed bound. Proposed bound: daily return difference under 10 bps on 90% of days.

**Status (2026-11-03): built; burn-in about to start.**
- Risk gateway (`src/trader/risk/`, the only order path): target validation (a corrupt target is refused whole), caps (35% ETF, 10% stock, SPY up to 100% per D19), chunked orders (D18), turnover/order-count/buying-power limits, trading window, daily-loss (auto-resume) and peak-drawdown (human) halts, write-ahead order log, reconciliation, idempotent client order ids, kill / unkill / flatten.
- Drills pass as tests: crash mid-run and restart with no duplicates, a lost order resubmitted once, corrupted targets, limit breach and halt, kill switch, unknown broker order → halt (`tests/test_gateway_drills.py`).
- Live runner (D16 in-memory history) currently trades FALLBACK = 100% SPY (D19). Daily report with slippage vs open and replay tracking error: `trader live report`.
- Scheduling: `scripts/register-tasks.ps1` registered 2026-11-03 (evening forward + report + target at 18:00 CT; weekday 08:40 CT gateway on paper). Since 2026-11-12 both run `trader job <name>` (src/trader/jobs.py) under Task Scheduler (S4U, runs without logon) or, on macOS, launchd (`scripts/macos/install-launchd.sh`).
- Paper account reset to **$200** (matches D10). One key pair serves data + paper trading; verified 401 on the live endpoint.
- Alerts: ntfy push working (`trader live test-alert`). Refusals/halts/failures push; turnover deferrals are log-only; a low-priority daily summary pushes after the evening report.
- **Replay mode (2026-11-05, `trader replay`):** runs the unchanged runner + gateway over past sessions with a simulated broker (fills at the open ± modeled cost, T+1 settlement) and compares with the engine. S03 over 2025-01..2025-10 with human halts auto-cleared: +47.9% vs engine +49.7%, daily correlation 0.985, mean daily difference −0.4 bps (the rest is the 60% turnover cap spreading rebalances over 2 days, and leftover cash). With the real halt policy, the 15% peak-drawdown halt fires on 2025-02-26 (8 weeks in) and S03 then fell to −43% from peak by 2025-04-07.
- First paper orders: 2026-11-04 09:40 ET, FALLBACK builds 60% SPY on day 1 (5 x $24 chunks), the rest on day 2.
- **2026-11-10 (D30):** the TE bound is now measured against the submit-minute (09:40) price on days with fills (≥ 2 needed in the streak); open-to-09:40 drift is reported as timing. Under the old open-based measure every fill day failed (−38 to +36 bps); vs the 09:40 price, slippage was −1 to +7 bps per day. S03 rebalances at month-end, so the first fill days that count are expected around 2026-11-30 / 12-01.

## P5 — ML ranker (weeks 5–9)
- Train LightGBM on 5-day forward return rank across the universe, using price, event, fundamental, and regime features.
- Use purged CV for hyperparameters and keep the search budget small, with every trial logged.
- Check feature importance and stability across folds. Run ablations against the best baseline.

**Gate:** beats the best baseline by a margin that survives the deflated Sharpe at the combined trial count, at 2× costs.

## P6 — LLM features (weeks 6–10)
- Score news and 8-K text with the schema-validated scorer.
- **Evaluate only on documents after the scoring model's training cutoff** [D5]. To get a longer test window, also score with an open-weight model whose cutoff is older and documented, and test on the period after that cutoff.
- Use the scores in two ways:
  - (a) as features added to the P5 ranker.
  - (b) as an event-risk veto in the gateway's pre-trade checks.
- Measure the incremental Sharpe and return net of API cost, using forward data collected since P1.

**Gate:** a statistically positive incremental lift on post-cutoff data. If the lift isn't there, the LLM is dropped from the signal path, and that's fine.

## P7 — Holdout test and selection (week ~10)
- Freeze the code version. Run the chosen candidate(s), at most 3, **once** on the holdout year.
- Pass if the holdout Sharpe is > 0.5, is ≥ 50% of the in-sample Sharpe, and the max drawdown is within 1.5× the in-sample max drawdown.
- If it fails, do **not** tweak and rerun the holdout. Go back to research. The holdout is then spent: the next holdout becomes forward paper data.

**Status (2026-11-13): run once; S03 FAILED (D31, D33).** Holdout Sharpe 0.11 vs SPY 0.96, CAGR −9.7%, max DD −40.8%. S13 (faster S03) was tested first and did not qualify. Paper is back on FALLBACK (SPY); the holdout is spent.

## P8 — Paper trading the final candidate (≥ 3 months)
- Use the full live stack. The daily review runs every trading day, and a weekly summary compares results against the backtest's expected distribution.
- **Gate (`promote-strategy` skill):**
  - ≥ 60 trading days.
  - Realized results within the backtest's 90% band.
  - Slippage ≤ the modeled level.
  - Zero unexplained incidents.

## P9 — Live, small ($200, taxable; D10)
- The whole $200 goes in at once. Scaling up means adding new deposits, only after 3+ months of live results that track paper.
- Taxes: short-term gains are taxed as ordinary income. The fill log must support Form 8949 export and wash-sale tracking. Alpaca issues a 1099-B, and our log is the cross-check.
- Use a separate cash account (no margin) with a fixed deposit. Treat that deposit as the maximum loss.
- Months 1–3: the full $200 (D10). Add new deposits only if live tracks paper and the modeled slippage holds; at most 2x per step.
- Hard rules:
  - Drawdown of 45% from peak → automatic halt and review (D32; was 15%).
  - Strategy drift (live outside the 95% band for 20 days) → revert to SPY.
- Taxes: log every fill, and see a CPA before year-end. Watch wash sales: frequent rebalancing around losses can trigger them.

**Going live on FALLBACK (D34).** No candidate has passed P7, so live starts on 100% SPY. FALLBACK claims no edge, so the edge criteria (P7, the 60-day band, P8) don't apply. The gate tests operations only:
1. The P4 gate passes (10 clean days, ≥ 2 fill days with exec TE < 10 bp) on FALLBACK paper.
2. **Paper and live keep separate gateway state.** Today `live/orders.db` holds paper's orders, equity peak and halts. A live run against it would reconcile live positions against paper orders and halt. Split the state per account (e.g. `live/paper/` and `live/live/`) and let paper keep running alongside live with its own key pair. Add a drill test.
3. A separate Alpaca **cash** account (no margin), with a $200 deposit, settled. Live key pair in `.env` on this host only; `trader env-check` passes; `[live] mode = "live"`. The gateway already refuses live keys unless the mode is `live`.
4. A dry run on live keys (`trader live execute` without `--submit`) shows the expected SPY orders and no refusals.
5. The user approves going live in conversation (never inferred). The first live gateway run is watched, then reviewed with `daily-review`.
6. Review written to `docs/reviews/<date>-FALLBACK-live-small.md`.

Design for running and monitoring both accounts: [LIVE_PAPER.md](LIVE_PAPER.md).

---

## Open questions
1. ~~Capital~~ **Resolved (D10):** $200.
2. ~~Account type~~ **Resolved (D10):** taxable.
3. ~~Order confirmation~~ **Resolved (D9):** orders are fully automated in paper and live. A human is needed only to clear a peak-drawdown halt, a reconciliation mismatch, or a duplicate-order halt. Promotion to live is a one-time approval, not per-order.
4. ~~Holdout vs P4 paper trading~~ **Resolved (D16):** the live runner fetches its trailing window into memory, never to the lake, and paper results are judged on forward days only.
