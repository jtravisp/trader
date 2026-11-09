"""Daily live report: live/reports/<date>.md (read-only; the daily-review skill summarizes it).

- Equity and day return vs SPY.
- Fills: average fill price vs the session OPEN (the backtest's fill price) -> realized slippage
  in bps, against the modeled cost (D4).
- Live vs model, split in two (`attribution`, pure):
  * execution error = live return - holdings model. Live return is marked at official closes (not
    Alpaca's after-hours equity). The model uses the actual start-of-day holdings for the overnight leg,
    fills today's trades at the session open, holds the actual end-of-day positions open -> close, and
    charges the modeled cost (D4). This is the P4 tracking-error bound (< 10 bps on 90% of days).
  * strategy gap = live return - close-to-close return of the target weights (what the backtest
    holds). Large on build-up days (turnover cap, D18), which are excluded from the gate count.
- `gate()`: the P4 gate over live/reports/daily.jsonl (latest record per date).
- Halts, refusals and alerts of the day.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta

from trader.config import NY, ROOT, settings
from trader.data.http import alpaca_data
from trader.risk import state

REPORTS = ROOT / "live" / "reports"


def _bars(symbols: list[str], d: date) -> dict[str, dict]:
    if not symbols:
        return {}
    http = alpaca_data()
    start = datetime.combine(d - timedelta(days=7), time(0), NY).isoformat()
    # free-tier SIP rejects windows that reach into the last 15 minutes
    end = min(datetime.combine(d, time(23, 59), NY), datetime.now(NY) - timedelta(minutes=16)).isoformat()
    page = http.get_json("/v2/stocks/bars", {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start,
                                             "end": end, "adjustment": "all", "feed": "sip", "limit": 10000})
    out = {}
    for sym, bars in (page.get("bars") or {}).items():
        if len(bars) >= 2 and bars[-1]["t"][:10] == d.isoformat():
            out[sym] = {"open": bars[-1]["o"], "close": bars[-1]["c"], "prev_close": bars[-2]["c"]}
    return out


BUILD_UP_L1 = 0.10  # start-of-day weights this far (L1) from the target = still building up


def attribution(qty_end: dict[str, float], fills: list, px: dict[str, dict], cash_end: float, last_equity: float,
                target: dict[str, float] | None, is_etf: set[str], etf_bps: float, stock_bps: float) -> dict:
    """Pure. fills: objects with symbol, side, filled_qty, filled_avg_price. px: symbol -> open/close/prev_close."""
    qty0 = dict(qty_end)
    for o in fills:
        qty0[o.symbol] = qty0.get(o.symbol, 0.0) - (o.filled_qty if o.side == "buy" else -o.filled_qty)
    held = {k for k, q in {**qty0, **qty_end}.items() if abs(q) > 1e-12}
    missing = sorted(k for k in held if k not in px)
    out: dict = {"missing_prices": missing}
    if last_equity <= 0 or missing:
        return out
    live = sum(q * px[k]["close"] for k, q in qty_end.items() if abs(q) > 1e-12) + cash_end
    out["live_ret"] = live / last_equity - 1
    pnl = sum(q * (px[k]["open"] - px[k]["prev_close"]) for k, q in qty0.items() if abs(q) > 1e-12)
    pnl += sum(q * (px[k]["close"] - px[k]["open"]) for k, q in qty_end.items() if abs(q) > 1e-12)
    pnl -= sum(o.filled_qty * px[o.symbol]["open"] * (etf_bps if o.symbol in is_etf else stock_bps) / 1e4 for o in fills)
    out["model_ret"] = pnl / last_equity
    out["exec_te_bps"] = (out["live_ret"] - out["model_ret"]) * 1e4
    if target is not None:
        tgt = {k: float(v) for k, v in target.items() if k != "$CASH"}
        if all(k in px for k in tgt):
            out["replay_ret"] = sum(w * (px[k]["close"] / px[k]["prev_close"] - 1) for k, w in tgt.items())
            out["strategy_gap_bps"] = (out["live_ret"] - out["replay_ret"]) * 1e4
        w0 = {k: q * px[k]["prev_close"] / last_equity for k, q in qty0.items() if abs(q) > 1e-12}
        l1 = sum(abs(w0.get(k, 0.0) - tgt.get(k, 0.0)) for k in {*w0, *tgt})
        out["start_l1_vs_target"] = l1
        out["build_up"] = l1 > BUILD_UP_L1
    return out


def gate(records: list[dict], bound_bps: float = 10.0, share: float = 0.90, days: int = 10,
         is_session=None) -> dict:
    """P4: `days` consecutive clean trading days (latest streak), and |exec TE| < bound on >= share of
    the non-build-up days in that streak. Reports for non-sessions (weekends, holidays: the evening job
    runs every day) are skipped, not counted as unclean."""
    if is_session is None:
        from trader.calendar import is_session
    by = {r["date"]: r for r in records if is_session(date.fromisoformat(r["date"]))}  # latest per date wins
    streak: list[dict] = []
    for k in sorted(by):
        r = by[k]
        clean = not r.get("halts") and not r.get("errors") and r.get("exec_te_bps") is not None
        streak = [*streak, r] if clean else []
    te = [abs(r["exec_te_bps"]) for r in streak if not r.get("build_up")]
    within = sum(x < bound_bps for x in te) / len(te) if te else float("nan")
    return {"clean_streak": len(streak), "te_days": len(te), "te_within_share": within,
            "pass": len(streak) >= days and bool(te) and within >= share}


def read_records() -> list[dict]:
    f = REPORTS / "daily.jsonl"
    if not f.exists():
        return []
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]


def build(d: date, broker, log=print) -> str:
    con = state.connect()
    acct, pos = broker.account(), broker.positions()
    since = datetime.combine(d, time(0), NY).astimezone(UTC).isoformat()
    fills = [o for o in broker.orders_since(since) if o.filled_qty > 0]
    syms = sorted({"SPY", *pos, *(o.symbol for o in fills)})
    px = _bars(syms, d)
    day_ret = acct.equity / acct.last_equity - 1 if acct.last_equity else 0.0
    spy = px.get("SPY")
    spy_ret = spy["close"] / spy["prev_close"] - 1 if spy else float("nan")

    # the target in force today: the latest one executed before today's open
    run = con.execute("SELECT run_id FROM runs WHERE status IN ('executed','incomplete') AND created_at < ? ORDER BY created_at DESC LIMIT 1",
                      (datetime.combine(d, time(9, 30), NY).astimezone(UTC).isoformat(),)).fetchone()
    target = dict(state.read_target(con, run[0])) if run else None
    from trader.universe.build import etf_list

    s0 = settings()
    att = attribution({p.symbol: p.qty for p in pos.values()}, fills, px, acct.cash, acct.last_equity, target,
                      set(etf_list()), s0.etf_bps, s0.stock_bps)
    replay = att.get("replay_ret")

    slip = []
    s = s0
    for o in fills:
        b = px.get(o.symbol)
        if b and o.filled_avg_price:
            sign = 1 if o.side == "buy" else -1
            bps = sign * (o.filled_avg_price / b["open"] - 1) * 1e4
            slip.append((o.symbol, o.side, o.filled_qty, o.filled_avg_price, b["open"], bps))
    events = con.execute("SELECT ts, level, kind, detail FROM events WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
    halts = state.active_halts(con, d.isoformat())

    L = [f"# Live report {d} ({'paper' if getattr(broker, 'paper', True) else 'LIVE'})", "",
         f"Equity **${acct.equity:,.2f}** (prev close ${acct.last_equity:,.2f}): day {day_ret:+.2%}; SPY {spy_ret:+.2%}  ",
         f"Cash ${acct.cash:,.2f}; positions {len(pos)}"]
    if "exec_te_bps" in att:
        L.append(f"Marked at official closes: {att['live_ret']:+.2%}; holdings model (open fills, modeled costs) "
                 f"{att['model_ret']:+.2%}; **execution error {att['exec_te_bps']:+.1f} bps** (P4 bound 10)  ")
    if replay is not None:
        bu = att.get("build_up")
        L.append(f"Target `{run[0]}` close-to-close {replay:+.2%}; strategy gap {att['strategy_gap_bps']:+.1f} bps"
                 + (f" (build-up day: start weights {att['start_l1_vs_target']:.0%} L1 from target, excluded from the gate)" if bu else ""))
    if att.get("missing_prices"):
        L.append(f"No bars for {', '.join(att['missing_prices'])}: attribution skipped")
    L += ["", "## Fills vs session open (backtest fill price)", "", "| symbol | side | qty | fill | open | slippage bps |", "|---|---|---|---|---|---|"]
    L += [f"| {a} | {b} | {c:.4f} | {e:.2f} | {f:.2f} | {g:+.1f} |" for a, b, c, e, f, g in slip] or ["| none | | | | | |"]
    if slip:
        avg = sum(x[5] for x in slip) / len(slip)
        L.append(f"\nMean slippage vs open {avg:+.1f} bps (modeled cost: {s.etf_bps} bps ETFs / {s.stock_bps} bps stocks per side)")
    L += ["", "## Positions", "", "| symbol | qty | value | weight |", "|---|---|---|---|"]
    L += [f"| {p.symbol} | {p.qty:.4f} | ${p.market_value:,.2f} | {p.market_value / acct.equity:.1%} |" for p in pos.values()] or ["| none | | | |"]
    L += ["", "## Halts", ""] + ([f"- **{h['kind']}**: {h['reason']}" for h in halts] or ["none"])
    L += ["", "## Events", ""] + ([f"- {t[11:19]} {lvl} {k}: {det[:160]}" for t, lvl, k, det in events] or ["none"])
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{d}.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    rec = {"date": d.isoformat(), "equity": acct.equity, "day_ret": day_ret, "spy_ret": spy_ret, "replay_ret": replay,
           "live_ret": att.get("live_ret"), "model_ret": att.get("model_ret"), "exec_te_bps": att.get("exec_te_bps"),
           "strategy_gap_bps": att.get("strategy_gap_bps"), "build_up": att.get("build_up"), "run_id": run[0] if run else None,
           "n_fills": len(fills), "mean_slip_bps": (sum(x[5] for x in slip) / len(slip)) if slip else None,
           "halts": [h["kind"] for h in halts],
           "errors": [k for _, lvl, k, _ in events if lvl in ("error", "critical")]}
    with (REPORTS / "daily.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    from trader.monitor.alerts import send

    te = f", exec TE {att['exec_te_bps']:+.0f}bp" if "exec_te_bps" in att else ""
    send(f"trader daily {d}", f"Equity ${acct.equity:,.2f} ({day_ret:+.2%}) vs SPY {spy_ret:+.2%}{te}; "
         f"{len(fills)} fills; halts: {', '.join(h['kind'] for h in halts) or 'none'}", "2")
    g = gate(read_records())
    log(f"report: {path}; P4 gate: clean streak {g['clean_streak']}/10, |exec TE| < 10bp on "
        f"{'n/a' if g['te_days'] == 0 else format(g['te_within_share'], '.0%')} of {g['te_days']} non-build-up days -> {'PASS' if g['pass'] else 'not yet'}")
    return str(path)
