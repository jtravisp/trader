"""Cost calibration from real fills (D4: "recalibrate from real fills"; D37).

For every filled order of an account: slippage vs the execution-price reference the daily report uses
(the open of the 1-minute bar in which the order was submitted, D30), signed so that positive = worse
than the reference (paid more on a buy, received less on a sell). Aggregated by asset class and side,
plain and notional-weighted, with a standard error. Read-only: it reads orders through the broker's
read endpoints and minute bars from the data API; it never trades.

Paper caveat: Alpaca paper fills are simulated at the quote, with no market impact. Paper slippage is
a lower bound on the cost of crossing the spread, not a measurement of live impact (RESEARCH.md).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import UTC, date, datetime

from trader.config import NY


def fill_rows(fills: list, ref_px: dict[tuple[date, str], float], is_etf: set[str]) -> list[dict]:
    """Pure. fills: objects with symbol, side, filled_qty, filled_avg_price, submitted_at (ISO UTC).
    ref_px: (NY session date, symbol) -> execution-price reference. Fills without a reference are dropped."""
    out = []
    for o in fills:
        if not o.filled_qty or not o.filled_avg_price or not o.submitted_at:
            continue
        d = datetime.fromisoformat(o.submitted_at.replace("Z", "+00:00")).astimezone(NY).date()
        ref = ref_px.get((d, o.symbol))
        if not ref:
            continue
        sign = 1 if o.side == "buy" else -1
        out.append({"date": d.isoformat(), "symbol": o.symbol, "side": o.side,
                    "cls": "etf" if o.symbol in is_etf else "stock",
                    "notional": o.filled_qty * o.filled_avg_price,
                    "slip_bps": sign * (o.filled_avg_price / ref - 1) * 1e4})
    return out


def summarize(rows: list[dict]) -> dict[str, dict]:
    """Pure. Per group ('etf', 'stock', 'stock/buy', ...): n, days, mean, median, s.e. (clustered by day,
    since fills on one day share the same market move), notional-weighted mean."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["cls"]].append(r)
        groups[f"{r['cls']}/{r['side']}"].append(r)
    out = {}
    for k, g in sorted(groups.items()):
        x = sorted(r["slip_bps"] for r in g)
        n = len(x)
        mean = sum(x) / n
        med = x[n // 2] if n % 2 else (x[n // 2 - 1] + x[n // 2]) / 2
        w = sum(r["notional"] for r in g)
        wmean = sum(r["slip_bps"] * r["notional"] for r in g) / w if w else float("nan")
        by_day: dict[str, list[float]] = defaultdict(list)
        for r in g:
            by_day[r["date"]].append(r["slip_bps"] - mean)
        days = len(by_day)
        # cluster-robust s.e. of the mean (day clusters); nan with one day
        se = math.sqrt(sum(sum(v) ** 2 for v in by_day.values()) * days / max(days - 1, 1)) / n if days > 1 else float("nan")
        out[k] = {"n": n, "days": days, "mean": mean, "median": med, "se": se, "wmean": wmean}
    return out


def _ref_prices(fills: list) -> dict[tuple[date, str], float]:
    from trader.monitor.daily import _minute_px

    by_day: dict[date, list] = defaultdict(list)
    for o in fills:
        if o.submitted_at and o.filled_qty:
            by_day[datetime.fromisoformat(o.submitted_at.replace("Z", "+00:00")).astimezone(NY).date()].append(o)
    out = {}
    for d, fs in sorted(by_day.items()):
        for sym, px in _minute_px(fs, d).items():
            out[(d, sym)] = px
    return out


def report(broker, since: date, path, log=print) -> dict[str, dict]:
    """Writes `path` (markdown) and `path`.jsonl-free summary; returns the summary."""
    from trader.config import settings
    from trader.universe.build import etf_list

    since_ts = datetime.combine(since, datetime.min.time(), NY).astimezone(UTC).isoformat()
    fills = [o for o in broker.orders_since(since_ts) if o.filled_qty > 0]
    rows = fill_rows(fills, _ref_prices(fills), set(etf_list()))
    summ = summarize(rows)
    s = settings()
    L = [f"# Cost calibration from fills since {since}", "",
         f"{len(rows)} fills with an execution-price reference (of {len(fills)} filled orders). Slippage vs the open "
         "of the 1-minute bar at submission (D30); positive = worse. Day-clustered standard errors.", "",
         f"Model: ETFs {s.etf_bps:g} bps, stocks {s.stock_bps:g} bps per side (2x stress: "
         f"{s.etf_bps * s.stress_multiplier:g} / {s.stock_bps * s.stress_multiplier:g}).", "",
         "| group | fills | days | mean bps | s.e. | median | notional-weighted |", "|---|---|---|---|---|---|---|"]
    for k, v in summ.items():
        L.append(f"| {k} | {v['n']} | {v['days']} | {v['mean']:+.1f} | {v['se']:.1f} | {v['median']:+.1f} | {v['wmean']:+.1f} |")
    L += ["", "Paper fills are simulated at the quote (no impact): treat these as a floor on live costs.", "",
          "| date | symbol | side | class | notional | slippage bps |", "|---|---|---|---|---|---|"]
    L += [f"| {r['date']} | {r['symbol']} | {r['side']} | {r['cls']} | ${r['notional']:.2f} | {r['slip_bps']:+.1f} |"
          for r in sorted(rows, key=lambda r: (r["date"], r["symbol"]))]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps({"since": since.isoformat(), "summary": summ}, indent=1), encoding="utf-8")
    log(f"costs: {len(rows)} fills -> {path}")
    return summ
