"""Run, register and report a backtest (the run-experiment skill's step 2-3)."""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

from trader.backtest import engine
from trader.backtest.panel import Panel, load_panel
from trader.research import metrics, registry, stats
from trader.signals.library import REGISTRY


@dataclass
class Trial:
    trial_id: str
    cost_mult: float
    delay: int
    result: engine.Result
    summary: dict


_PANELS: dict[str, Panel] = {}


def panel(universe: str = "seed", fields: bool = False) -> Panel:
    """'seed' = survivorship-biased D14 seed (P2 sanity); 'pit' = point-in-time universe (P3, D20/D21).
    fields=True also attaches fundamentals + FRED matrices (large; only strategies that need them)."""
    key = f"{universe}+fields" if fields else universe
    if key in _PANELS:
        return _PANELS[key]
    if universe not in _PANELS:
        if universe == "pit":
            from trader.universe.pit import load_pit_panel

            _PANELS[universe] = load_pit_panel()
        else:
            _PANELS[universe] = load_panel()
    if not fields:
        return _PANELS[universe]
    from trader.research.fields import attach

    _PANELS[key] = attach(_PANELS.pop(universe))  # fundamentals (PIT lines only) + FRED regime; reuse the base arrays
    return _PANELS[key]


def spy_returns(p: Panel) -> np.ndarray:
    r = p.returns()[:, p.col("SPY")]
    return np.nan_to_num(r)


def run_one(strategy_id: str, params: dict, cost_mult: float = 1.0, delay: int = 0, constraints: str = "limits",
            p: Panel | None = None, leak: tuple[bool, str] | None = None, notes: str = "", family: str | None = None,
            universe: str = "seed") -> Trial:
    p = p or panel(universe, REGISTRY[strategy_id].needs_fields)
    strat = REGISTRY[strategy_id](**params)
    if leak is None:
        leak = engine.truncation_leak_check(strat, p)
    tg = strat.targets(p)
    cons = engine.Constraints.from_limits() if constraints == "limits" else engine.Constraints.none()
    res = engine.run(p, tg, engine.CostModel.from_settings(cost_mult), cons, delay=delay)
    # evaluate from the first day the strategy is invested (warm-up excluded), same window for SPY
    start = int(np.argmax(res.exposure > 0)) if (res.exposure > 0).any() else 0
    sl = slice(start, None)
    summ = metrics.summary(res.net[sl], res.gross[sl], res.turnover[sl], p.dates[sl], spy_returns(p)[sl])
    summ["clipped_days"], summ["stale_exits"] = res.clipped_days, res.stale_exits
    status = "ok" if leak[0] else "leak_detected"
    tid = registry.record(
        family=family or strat.family, strategy_id=strategy_id,
        params={**params, "constraints": constraints, **({"universe": "pit"} if (p.pit_rank is not None and strat.stock_universe) else {})},
        cost_mult=cost_mult, delay=delay, data_start=str(p.dates[start]), data_end=str(p.dates[-1]),
        leak_check=leak[1], status=status, metrics=summ, dates=p.dates[sl], net=res.net[sl],
        sharpe_daily=stats.sharpe(res.net[sl]), notes=notes,
    )
    return Trial(tid, cost_mult, delay, res, summ)


def full_experiment(strategy_id: str, params: dict, constraints: str = "limits", notes: str = "", log=print,
                    universe: str = "seed") -> str:
    """Base costs, 2x costs and a 1-day-delay check; all registered. Returns the report path."""
    p = panel(universe, REGISTRY[strategy_id].needs_fields)
    strat = REGISTRY[strategy_id](**params)
    leak = engine.truncation_leak_check(strat, p)
    log(f"{strategy_id} {params}: leak check {leak[1]}")
    base = run_one(strategy_id, params, 1.0, 0, constraints, p, leak, notes)
    x2 = run_one(strategy_id, params, 2.0, 0, constraints, p, leak, notes)
    dly = run_one(strategy_id, params, 1.0, 1, constraints, p, leak, notes)
    path = write_report(strategy_id, params, base, x2, dly, p, leak, notes)
    log(f"  base Sharpe {base.summary['sharpe']:.2f}, 2x {x2.summary['sharpe']:.2f}, delay+1 {dly.summary['sharpe']:.2f} -> {path}")
    return str(path)


def family_stats(family: str, trial: Trial) -> dict:
    ft = registry.family_trials(family, trial.cost_mult, trial.delay)
    ft = ft.filter(ft["status"] == "ok") if ft.height else ft  # leaky trials can't define the null
    srs = ft["sharpe_daily"].to_list() if ft.height else [stats.sharpe(trial.result.net)]
    start = int(np.argmax(trial.result.exposure > 0))
    r = trial.result.net[start:]
    dsr, sr0 = stats.deflated_sharpe(r, srs)
    out = {"n_trials": len(srs), "dsr": dsr, "sr0_ann": sr0 * np.sqrt(252)}
    # PBO over distinct variants (one per params) once the family has >= 4
    variants = ft.unique(subset=["params"], keep="last") if ft.height else ft
    if variants.height >= 4:
        mat, _ = registry.returns_matrix(variants["trial_id"].to_list())
        out.update(stats.pbo_cscv(mat))
    return out


def red_flags(base: Trial, dly: Trial, p: Panel, start: int) -> list[str]:
    """run-experiment step 4: concentration in days/names, too-good Sharpe, delay sensitivity, decay."""
    r = base.result.net[start:]
    lr = np.log1p(r)
    tot = lr.sum()
    out = []
    sh = base.summary["sharpe"]
    out.append(f"{'**FLAG** ' if sh > 2 else ''}Sharpe {sh:.2f} (flag if > 2 on daily data)")
    k = max(1, int(len(r) * 0.01))
    top = np.sort(lr)[::-1][:k].sum()
    share = top / tot if tot > 0 else float("nan")
    out.append(f"{'**FLAG** ' if tot > 0 and share > 0.5 else ''}best 1% of days ({k}) = {share:.0%} of total log return"
               + (" (total <= 0)" if tot <= 0 else ""))
    ex = np.delete(r, np.argsort(r)[::-1][:10])
    out.append(f"Sharpe without the 10 best days: {stats.sharpe(ex) * np.sqrt(252):.2f}")
    pn = base.result.name_pnl
    if pn is not None and pn.sum() > 0:
        order = np.argsort(pn)[::-1]
        s5 = pn[order[:5]].sum() / pn.sum()
        names = ", ".join(f"{p.symbols[j]} {pn[j] / pn.sum():.0%}" for j in order[:5])
        out.append(f"{'**FLAG** ' if s5 > 0.5 else ''}top 5 names = {s5:.0%} of gross P&L ({names})")
    d = dly.summary["sharpe"]
    out.append(f"{'**FLAG** ' if sh > 0 and d < 0.5 * sh else ''}delay +1 day Sharpe {d:.2f} vs {sh:.2f}")
    yrs = p.dates[start:].astype("datetime64[Y]").astype(int) + 1970
    pre = lr[yrs < 2018].sum()
    if tot > 0 and (yrs < 2018).any():
        out.append(f"{'**FLAG** ' if pre / tot > 0.6 else ''}pre-2018 share of log return {pre / tot:.0%}")
    return out


def write_report(strategy_id: str, params: dict, base: Trial, x2: Trial, dly: Trial, p: Panel, leak: tuple[bool, str], notes: str = ""):
    fam = REGISTRY[strategy_id].family
    fs = family_stats(fam, base)
    start = int(np.argmax(base.result.exposure > 0))
    spy = spy_returns(p)[start:]
    spy_s = metrics.summary(spy, spy, np.zeros_like(spy), p.dates[start:], spy)
    ci = stats.stationary_bootstrap(base.result.net[start:], lambda r: stats.sharpe(r) * np.sqrt(252), n_boot=1000)
    rows = [
        ("CAGR", "cagr", "{:+.2%}"), ("Sharpe", "sharpe", "{:.2f}"), ("Sortino", "sortino", "{:.2f}"),
        ("Max drawdown", "max_dd", "{:.1%}"), ("Annual turnover", "turnover_ann", "{:.1f}x"),
        ("Cost drag (CAGR)", "cost_drag", "{:.2%}"), ("% positive months", "pct_pos_months", "{:.0%}"),
        ("Corr to SPY", "corr_spy", "{:.2f}"), ("Beta to SPY", "beta_spy", "{:.2f}"),
        ("50/50 blend alpha vs SPY", "blend5050_alpha", "{:+.2%}"), ("50/50 blend Sharpe", "blend5050_sharpe", "{:.2f}"),
    ]
    L = [f"# {strategy_id} {json.dumps(params, sort_keys=True)}", "",
         f"Trials: base `{base.trial_id}`, 2x `{x2.trial_id}`, delay+1 `{dly.trial_id}`  ",
         f"Window: {p.dates[start]} .. {p.dates[-1]} (pre-holdout). Leak check: {leak[1]}", "",
         "| metric | base costs | 2x costs | delay +1 day | SPY same window |", "|---|---|---|---|---|"]
    for name, k, f in rows:
        cells = [f.format(t.summary[k]) if k in t.summary else "" for t in (base, x2, dly)]
        L.append(f"| {name} | {' | '.join(cells)} | {f.format(spy_s[k]) if k in spy_s else ''} |")
    L += ["", f"Sharpe 95% CI (stationary bootstrap, base): [{ci[0]:.2f}, {ci[1]:.2f}]  ",
          f"Deflated Sharpe probability: **{fs['dsr']:.3f}** with family trial count {fs['n_trials']} (SR0 = {fs['sr0_ann']:.2f} ann.)  "]
    if "pbo" in fs:
        L.append(f"PBO (CSCV, {fs['n_combinations']} splits): **{fs['pbo']:.2f}**  ")
    else:
        L.append("PBO: needs >= 4 variants in the family  ")
    L += ["", "| subperiod | CAGR | Sharpe | max DD |", "|---|---|---|---|"]
    for sp in metrics.subperiods(base.result.net[start:], p.dates[start:]):
        L.append(f"| {sp['start']} .. {sp['end']} | {sp['cagr']:+.2%} | {sp['sharpe']:.2f} | {sp['max_dd']:.1%} |")
    L += ["", "Red flags (run-experiment step 4):", ""] + [f"- {x}" for x in red_flags(base, dly, p, start)]
    gate = {
        "DSR >= 0.95": fs["dsr"] >= 0.95,
        "PBO <= 0.30": fs.get("pbo", 1.0) <= 0.30 if "pbo" in fs else None,
        "Sharpe > 0 at 2x costs": x2.summary["sharpe"] > 0,
        ">= 2 of 3 subperiods positive": sum(sp["cagr"] > 0 for sp in metrics.subperiods(base.result.net[start:], p.dates[start:])) >= 2,
    }
    if REGISTRY[strategy_id].stock_universe and p.pit_rank is not None:
        L += ["", "Universe: point-in-time top-N from Massive month-end listings (D20, D21), delisted names included."]
    elif REGISTRY[strategy_id].stock_universe:
        L += ["", "Universe: liquid top-500 inside the survivorship-biased seed (D14). **Until the P3 point-in-time "
              "universe exists, no result here counts as gate evidence.**"]
    else:
        L += ["", "Universe: the fixed ETF list in config/universe.yaml (chosen in advance; no delisting gap)."]
    L += [f"Notes: {notes}"] if notes else []
    L += ["", "P3 gate check:", ""] + [f"- {k}: {'PASS' if v else ('n/a' if v is None else 'FAIL')}" for k, v in gate.items()]
    path = registry.report_dir() / f"{base.trial_id}.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    _equity_png(path.with_suffix(".png"), base, x2, spy, p.dates[start:])
    return path


def _equity_png(path, base: Trial, x2: Trial, spy: np.ndarray, dates: np.ndarray) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    s = int(np.argmax(base.result.exposure > 0))
    fig, ax = plt.subplots(figsize=(9, 4))
    for r, lab in ((base.result.net[s:], "base costs"), (x2.result.net[s:], "2x costs"), (spy, "SPY")):
        ax.plot(dates.astype("O"), np.cumprod(1 + r), label=lab, lw=1)
    ax.set_yscale("log")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
