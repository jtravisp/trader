"""P2 gate: harness sanity tests (all trials registered). Report: experiments/reports/P2_sanity.md"""

from __future__ import annotations

import numpy as np

from trader.data.gates import spy_total_return_check
from trader.research import metrics, registry, runner


def _calendar_returns(r: np.ndarray, dates: np.ndarray) -> dict[int, float]:
    years = dates.astype("datetime64[Y]").astype(int) + 1970
    return {int(y): float(np.prod(1 + r[years == y]) - 1) for y in np.unique(years)}


def san01(log=print) -> tuple[bool, list[str]]:
    p = runner.panel()
    tr = runner.run_one("SAN01", {"symbol": "SPY"}, 1.0, 0, "none", p, notes="P2 sanity")
    eng = _calendar_returns(tr.result.net, p.dates)
    _, lake_tab = spy_total_return_check(log=lambda *_: None)
    lines, ok = [], True
    for y, lr in lake_tab.select("year", "lake_return").iter_rows():
        d = eng.get(y, np.nan) - lr
        good = abs(d) <= 0.0005
        ok &= good
        lines.append(f"| {y} | {lr:+.4%} | {eng.get(y, np.nan):+.4%} | {d:+.4%} | {'PASS' if good else 'FAIL'} |")
    log(f"SAN01 buy-and-hold SPY vs P1 lake: {'PASS' if ok else 'FAIL'}")
    return ok, ["| year | P1 lake | engine | diff | |", "|---|---|---|---|---|", *lines, "", f"Trial `{tr.trial_id}`"]


def san02(n_seeds: int = 100, log=print) -> tuple[bool, list[str]]:
    p = runner.panel()
    # constraints off for both legs: the gateway min-drift rule (2% of equity) would block every
    # 0.2% equal-weight position, turning the benchmark into cash
    ew = runner.run_one("SAN02-EW", {"n": 500}, 1.0, 0, "none", p, notes="P2 sanity benchmark")
    s = int(np.argmax(ew.result.exposure > 0))
    ir_g, ir_n, sh_n = [], [], []
    leak = None
    for seed in range(n_seeds):
        tr = runner.run_one("SAN02", {"seed": seed, "k": 50, "n": 500}, 1.0, 0, "none", p, leak=leak, notes="P2 sanity")
        if leak is None:  # leak-check the first seed; the generator is identical for all seeds
            leak = (True, registry.read_trial(tr.trial_id)["leak_check"])
        a = slice(max(s, int(np.argmax(tr.result.exposure > 0))), None)
        dg = tr.result.gross[a] - ew.result.gross[a]
        dn = tr.result.net[a] - ew.result.net[a]
        ir_g.append(dg.mean() / dg.std(ddof=1) * np.sqrt(252))
        ir_n.append(dn.mean() / dn.std(ddof=1) * np.sqrt(252))
        sh_n.append(metrics.sharpe_ann(tr.result.net[a]))
    ir_g, ir_n = np.array(ir_g), np.array(ir_n)
    se = ir_g.std(ddof=1) / np.sqrt(len(ir_g))
    ok = abs(ir_g.mean()) < 0.15 and ir_n.mean() < ir_g.mean()
    log(f"SAN02 random picks: IR before costs {ir_g.mean():+.3f} (seed s.e. {se:.3f}), after costs {ir_n.mean():+.3f}: {'PASS' if ok else 'FAIL'}")
    lines = [
        f"{n_seeds} seeds, 50 random names from the liquid top-500 each week, vs the weekly equal-weight top-500.",
        "",
        "| | mean | 5th pct | 95th pct |", "|---|---|---|---|",
        f"| information ratio before costs | {ir_g.mean():+.3f} | {np.percentile(ir_g, 5):+.3f} | {np.percentile(ir_g, 95):+.3f} |",
        f"| information ratio after costs | {ir_n.mean():+.3f} | {np.percentile(ir_n, 5):+.3f} | {np.percentile(ir_n, 95):+.3f} |",
        f"| Sharpe after costs (absolute) | {np.mean(sh_n):.2f} | {np.percentile(sh_n, 5):.2f} | {np.percentile(sh_n, 95):.2f} |",
        "",
        f"Equal-weight benchmark: Sharpe {ew.summary['sharpe']:.2f}, CAGR {ew.summary['cagr']:+.2%} (trial `{ew.trial_id}`).",
        "Pass rule: |mean IR before costs| < 0.15 and costs lower the IR. Absolute Sharpe is positive only because",
        "the long-only universe rose; the random selection itself adds nothing.",
    ]
    return ok, lines


def san03(log=print) -> tuple[bool, list[str]]:
    p = runner.panel()
    tr = runner.run_one("SAN03", {"k": 50}, 1.0, 0, "limits", p, notes="P2 sanity: must be flagged")
    row = registry.read_trial(tr.trial_id)
    ok = row["status"] == "leak_detected"
    log(f"SAN03 leaky signal flagged: {'PASS' if ok else 'FAIL'} ({row['leak_check']})")
    return ok, [f"Leak check: `{row['leak_check']}`; status `{row['status']}`. "
                f"(Had it been trusted: Sharpe {tr.summary['sharpe']:.1f}, CAGR {tr.summary['cagr']:+.0%}.) Trial `{tr.trial_id}`"]


def san04(log=print) -> tuple[bool, list[str]]:
    """Textbook 12-1 momentum = the S03 grid point {lookback 252, skip 21}. Reuses the registered
    trial if it exists, so re-running the suite doesn't add duplicate S03 trials."""
    import json

    params = {"lookback": 252, "skip": 21}
    want = json.dumps({**params, "constraints": "limits"}, sort_keys=True)
    ft = registry.family_trials("S03", 1.0, 0)
    ft = ft.filter((ft["params"] == want) & (ft["status"] == "ok")) if ft.height else ft
    if ft.height:
        tid = ft.sort("trial_id")["trial_id"][-1]
        path = registry.report_dir() / f"{tid}.md"
        log(f"S03 {params}: reusing registered trial {tid}")
    else:
        path = runner.full_experiment("S03", params, notes="P2 sanity: textbook 12-1 momentum on biased seed", log=log)
        tid = registry.family_trials("S03", 1.0, 0).sort("trial_id")["trial_id"][-1]
    m = registry.read_trial(tid)
    mt = json.loads(m["metrics"])
    ok = m["status"] == "ok" and 0 < mt["sharpe"] < 1.5
    log(f"S03 12-1 momentum: Sharpe {mt['sharpe']:.2f}, CAGR {mt['cagr']:+.2%}: {'PASS (plausible)' if ok else 'CHECK'}")
    return ok, [f"Report: `experiments/reports/{path.name}`. Sharpe {mt['sharpe']:.2f}, CAGR {mt['cagr']:+.2%}, max DD {mt['max_dd']:.1%}.",
                "Pass rule: leak check ok and 0 < Sharpe < 1.5 (positive but not implausible). The level is inflated by the",
                "survivorship-biased seed (today's top-1000, D14): SPY-beta 1.34 and a +35% CAGR are what picking from",
                "today's winners looks like. Not gate evidence for S03.", ""]


def run_all(n_seeds: int = 100, log=print) -> bool:
    out = ["# P2 harness sanity tests", ""]
    ok_all = True
    for name, fn in (("SAN01 buy-and-hold SPY reproduces P1", san01), ("SAN03 leaky signal is caught", san03),
                     ("SAN04 textbook 12-1 momentum is plausible", san04),
                     ("SAN02 random signal adds nothing", lambda log: san02(n_seeds, log))):
        ok, lines = fn(log=log)
        ok_all &= ok
        out += [f"## {name}: {'PASS' if ok else 'FAIL'}", "", *lines, ""]
    (registry.report_dir() / "P2_sanity.md").write_text("\n".join(out), encoding="utf-8")
    log(f"P2 sanity: {'ALL PASS' if ok_all else 'FAILURES'} -> experiments/reports/P2_sanity.md")
    return ok_all
