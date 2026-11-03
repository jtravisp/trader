"""Daily live report: live/reports/<date>.md (read-only; the daily-review skill summarizes it).

- Equity and day return vs SPY.
- Fills: average fill price vs the session OPEN (the backtest's fill price) -> realized slippage
  in bps, against the modeled cost (D4).
- Live vs replay: close-to-close return of the executed target's weights. Tracking error = live day
  return - replay return (P4 gate: < 10 bps on 90% of days). Only meaningful once positions have
  reached the target: build-up days (turnover cap, D18) are excluded from the gate count.
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

    # replay: the target that executed today, at open fills, with the modeled costs
    run = con.execute("SELECT run_id FROM runs WHERE status IN ('executed','incomplete') AND created_at < ? ORDER BY created_at DESC LIMIT 1",
                      (datetime.combine(d, time(9, 30), NY).astimezone(UTC).isoformat(),)).fetchone()
    replay = None
    if run:
        tgt = dict(state.read_target(con, run[0]))
        tgt.pop("$CASH", None)
        r = 0.0
        for s, w in tgt.items():
            if s in px:
                b = px[s]
                r += float(w) * (b["close"] / b["prev_close"] - 1)
        replay = r

    slip = []
    s = settings()
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
    if replay is not None:
        te = (day_ret - replay) * 1e4
        L.append(f"Replay of `{run[0]}` (close-to-close on target weights): {replay:+.2%}; tracking error **{te:+.1f} bps**")
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
           "n_fills": len(fills), "mean_slip_bps": (sum(x[5] for x in slip) / len(slip)) if slip else None,
           "halts": [h["kind"] for h in halts]}
    with (REPORTS / "daily.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    log(f"report: {path}")
    return str(path)
