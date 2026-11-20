"""Daily live-vs-paper-vs-SPY comparison (docs/LIVE_PAPER.md part 3). Read-only: it reuses each account's
daily report record (live/<account>/reports/daily.jsonl) and writes live/reports/compare/<date>.md plus a
line in compare.jsonl. With more than one account enabled it sends the one combined evening push.

Three numbers to watch:
1. Live execution error (the P4 measure applied to live): a single fill day over 10 bps is a warning; fewer
   than 90% of the last 20 fill days within 10 bps is a review (error).
2. Live minus paper, while both run the same strategy: a daily gap over 15 bps, or a cumulative gap over 1%,
   is a warning (a fill, settlement or sizing difference).
3. Live vs SPY on FALLBACK: the cumulative shortfall is the cash drag; over 2% is a warning.
"""

from __future__ import annotations

import json
import math
from datetime import date

from trader.config import ROOT

OUT = ROOT / "live" / "reports" / "compare"
TE_BOUND_BPS, TE_SHARE, TE_WINDOW = 10.0, 0.90, 20
GAP_DAY_BPS, GAP_CUM, CASH_DRAG = 15.0, 0.01, 0.02


def by_date(records: list[dict]) -> dict[str, dict]:
    return {r["date"]: r for r in records}  # latest record per date wins


def _cum(rs: list) -> float:
    """Chained return; missing or non-finite days (no bars that evening) are skipped, not counted as 0."""
    out = 1.0
    for r in rs:
        if r is not None and math.isfinite(r):
            out *= 1 + r
    return out - 1


def account_view(records: list[dict], d: str, start: str | None = None) -> dict | None:
    """Pure: one account's numbers for day d. Returns are chained from the daily records (deposits don't
    distort them). start: first date counted for 'since start' (default: the account's first record)."""
    rec = by_date(records)
    if d not in rec:
        return None
    days = sorted(k for k in rec if k <= d and (start is None or k >= start))
    r = rec[d]
    month = [k for k in days if k[:7] == d[:7]]
    # same fill days as the P4 gate: execution-price records (D30) without exit fills (D35)
    fill_days = [rec[k] for k in days if rec[k].get("n_fills") and rec[k].get("exec_te_bps") is not None
                 and rec[k].get("te_ref") == "exec" and rec[k].get("n_exit_fills") == 0][-TE_WINDOW:]
    within = [abs(x["exec_te_bps"]) < TE_BOUND_BPS for x in fill_days]
    return {
        "equity": r.get("equity"), "day": r.get("day_ret"), "spy_day": r.get("spy_ret"),
        "mtd": _cum([rec[k].get("day_ret") for k in month]),
        "spy_mtd": _cum([rec[k].get("spy_ret") for k in month]),
        "since": _cum([rec[k].get("day_ret") for k in days]),
        "spy_since": _cum([rec[k].get("spy_ret") for k in days]),
        "start": days[0] if days else d,
        "exec_te_bps": r.get("exec_te_bps") if r.get("n_fills") else None,
        "slip_bps": r.get("mean_exec_slip_bps"), "fills": r.get("n_fills", 0), "halts": r.get("halts", []),
        "errors": r.get("errors", []), "max_weight_diff": r.get("max_weight_diff"), "bp_deferrals": r.get("bp_deferrals"),
        "te_fill_days": len(within), "te_within_share": (sum(within) / len(within)) if within else None,
    }


def compare(views: dict[str, dict | None], same_strategy: bool, fallback: bool) -> dict:
    """Pure: warnings from the per-account views (live first)."""
    warn: list[tuple[str, str]] = []
    lv, pv = views.get("live"), views.get("paper")
    out: dict = {"warnings": warn}
    if lv:
        te = lv["exec_te_bps"]
        if te is not None and abs(te) >= TE_BOUND_BPS:
            warn.append(("warning", f"live execution error {te:+.1f} bps (bound {TE_BOUND_BPS:g})"))
        if lv["te_within_share"] is not None and lv["te_fill_days"] >= 5 and lv["te_within_share"] < TE_SHARE:
            warn.append(("error", f"live execution error within {TE_BOUND_BPS:g} bps on only {lv['te_within_share']:.0%} "
                                  f"of the last {lv['te_fill_days']} fill days (bound {TE_SHARE:.0%}): review"))
        if fallback:
            drag = lv["spy_since"] - lv["since"]
            out["cash_drag"] = drag
            if drag > CASH_DRAG:
                warn.append(("warning", f"live trails SPY by {drag:.2%} since {lv['start']} on FALLBACK (cash drag bound {CASH_DRAG:.0%})"))
    if lv and pv and same_strategy:
        day_gap = ((lv["day"] or 0.0) - (pv["day"] or 0.0)) * 1e4
        out["gap_day_bps"] = day_gap
        if abs(day_gap) > GAP_DAY_BPS:
            warn.append(("warning", f"live minus paper {day_gap:+.1f} bps today (bound {GAP_DAY_BPS:g})"))
    return out


def summary_line(views: dict[str, dict | None], cmp: dict) -> str:
    parts = [f"{n} {v['day']:+.2%}" for n, v in views.items() if v and v.get("day") is not None]
    spy = next((v["spy_day"] for v in views.values() if v and v.get("spy_day") is not None), None)
    if spy is not None:
        parts.append(f"SPY {spy:+.2%}")
    te = "/".join(f"{v['exec_te_bps']:+.0f}" for v in views.values() if v and v.get("exec_te_bps") is not None)
    halts = sorted({h for v in views.values() if v for h in v["halts"]})
    return " · ".join([*parts, *( [f"TE {te}bp"] if te else []), f"halts: {', '.join(halts) or 'none'}",
                       *([f"{len(cmp['warnings'])} warning(s)"] if cmp["warnings"] else [])])


def run(d: date, log=print) -> str:
    from trader.live import accounts
    from trader.monitor import alerts

    ds = d.isoformat()
    names = accounts.enabled()
    views: dict[str, dict | None] = {}
    starts = {}
    for n in names:
        f = accounts.folder(n) / "reports" / "daily.jsonl"
        recs = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()] if f.exists() else []
        views[n] = account_view(recs, ds)
        starts[n] = views[n]["start"] if views[n] else None
    strat = {n: accounts.strategy(n).get("strategy") for n in names}
    same = len(set(strat.values())) == 1 and len(names) > 1
    # live-minus-paper compounds from the later of the two starts, so both cover the same days
    if same and all(starts.values()):
        common = max(starts.values())
        for n in names:
            f = accounts.folder(n) / "reports" / "daily.jsonl"
            views[n] = account_view([json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()], ds, common)
    cmp = compare(views, same, strat.get("live") == "FALLBACK")
    if same and all(views.values()):
        cmp["gap_cum"] = views["live"]["since"] - views["paper"]["since"]
        if abs(cmp["gap_cum"]) > GAP_CUM:
            cmp["warnings"].append(("warning", f"live minus paper {cmp['gap_cum']:+.2%} since {views['live']['start']} (bound {GAP_CUM:.0%})"))

    def cell(v, k, fmt):
        x = v.get(k) if v else None
        return "n/a" if x is None else format(x, fmt)

    cols = [*names, "SPY"]
    L = [f"# Live vs paper vs SPY, {ds}", "", "| | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    ref = next((v for v in views.values() if v), None)
    rows = [("equity", "equity", ",.2f", None), ("day", "day", "+.2%", "spy_day"), ("month to date", "mtd", "+.2%", "spy_mtd"),
            ("since start", "since", "+.2%", "spy_since"), ("execution error (bp)", "exec_te_bps", "+.1f", None),
            ("slippage vs submit minute (bp)", "slip_bps", "+.1f", None), ("fills", "fills", "d", None),
            ("buying-power deferrals", "bp_deferrals", "d", None), ("largest weight diff vs target", "max_weight_diff", ".1%", None)]
    for label, k, fmt, spyk in rows:
        L.append(f"| {label} | " + " | ".join(cell(views[n], k, fmt) for n in names) + f" | {cell(ref, spyk, fmt) if spyk else ''} |")
    L.append("| halts | " + " | ".join(", ".join(views[n]["halts"]) or "none" if views[n] else "n/a" for n in names) + " | |")
    L += ["", f"Strategies: {', '.join(f'{n} {s}' for n, s in strat.items())}"
          + (" (same: live minus paper is tracked)" if same else ""), ""]
    if "gap_cum" in cmp:
        L.append(f"Live minus paper: {cmp.get('gap_day_bps', float('nan')):+.1f} bps today, {cmp['gap_cum']:+.2%} cumulative  ")
    if "cash_drag" in cmp:
        L.append(f"Live vs SPY since start (cash drag): {-cmp['cash_drag']:+.2%}  ")
    for n in names:
        v = views[n]
        if v and v["te_within_share"] is not None:
            L.append(f"{n}: |execution error| < {TE_BOUND_BPS:g} bp on {v['te_within_share']:.0%} of the last {v['te_fill_days']} fill days  ")
    L += ["", "## Warnings", ""] + ([f"- {lvl}: {m}" for lvl, m in cmp["warnings"]] or ["none"])
    line = summary_line(views, cmp)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{ds}.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    with (OUT / "compare.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": ds, "views": views, "warnings": cmp["warnings"], "summary": line}) + "\n")
    if cmp["warnings"]:
        alerts.ACCOUNT = "live"  # every compare warning is about the live account
        for lvl, m in cmp["warnings"]:
            alerts.notify(lvl, m)
    if len(names) > 1:
        alerts.send(f"trader daily {ds}", line, "2")
    log(f"compare: {path}; {line}")
    return str(path)
