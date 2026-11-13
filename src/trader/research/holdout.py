"""P7 holdout run (docs/PLAN.md P7, D7). Runs each frozen candidate ONCE on the locked holdout year.

Approved by the user in conversation on 2026-11-13 (D31). Everything that touches the holdout goes
through lake.holdout_unlocked(), which logs every unlock, read and write to
experiments/holdout_access.jsonl.

Steps:
1. Ingest the holdout-year Massive month-end listings and Alpaca daily bars (source datasets; research
   reads still filter them out, because lake.scan() only returns holdout rows while unlocked).
2. Build the point-in-time universe through holdout_end IN MEMORY. The research datasets
   universe_lines / universe_pit are not rewritten, so pre-holdout research stays reproducible.
3. Run each candidate with the unchanged engine over 2016 -> holdout_end. Only the holdout sessions are
   scored, so positions carry in from the last pre-holdout rebalance exactly as they would have live.
4. Register base, 2x-cost and +1-day-delay trials with holdout_touched=1 and write a report.

Pass (PLAN.md P7): holdout Sharpe > 0.5, >= 50% of the in-sample Sharpe, and max drawdown within
1.5x the in-sample max drawdown. If it fails, do not tweak and rerun: the holdout is spent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import polars as pl

from trader.config import ROOT, settings

REASON = "P7 holdout run, approved by the user in conversation 2026-11-13 (D31)"
CANDIDATES = [("S03", {"lookback": 126, "skip": 21})]  # S13 did not qualify (STRATEGIES.md, 2026-11-13)


def _in_sample(strategy_id: str, params: dict) -> dict:
    """The registered pre-holdout P3 result this candidate was promoted on (pit universe, base costs)."""
    from trader.research import registry

    con = registry.connect()
    rows = con.execute(
        "SELECT trial_id, params, metrics FROM trials WHERE strategy_id=? AND cost_mult=1.0 AND delay=0 "
        "AND status='ok' AND holdout_touched=0 ORDER BY trial_id", (strategy_id,)).fetchall()
    for tid, ps, m in reversed(rows):
        ps = json.loads(ps)
        if ps.get("universe") == "pit" and all(ps.get(k) == v for k, v in params.items()):
            return {"trial_id": tid, **json.loads(m)}
    raise RuntimeError(f"no registered in-sample pit trial for {strategy_id} {params}")


def prepare(log=print):
    """Steps 1-2 (inside the unlock): returns the PIT panel through holdout_end."""
    from trader.data import bars, lake, massive
    from trader.universe import pit
    from trader.universe.build import etf_list

    s = settings()
    with lake.holdout_unlocked(REASON):
        log("massive: month-end listings through the holdout year")
        massive.ingest_tickers(log=log)
        t = pit.snapshots()
        cur = pit.current_listing(log)
        drop = pit.reused_tickers(t, cur)
        lines = pit.build_lines(t, drop)
        log(f"universe: {t['snapshot'].n_unique()} snapshots ({t['snapshot'].max()} last), {lines.height} tickers, "
            f"{lines['line_id'].n_unique()} lines; dropped {len(drop)} reused tickers")
        miss = pit.missing_bar_symbols(lines)
        log(f"bars: full history for {len(miss)} tickers new to the lake")
        bars.ingest_daily(miss, log=log)
        live = lines.filter(pl.col("last_snapshot") >= s.holdout_start - pit.MARGIN)["ticker"].to_list()
        syms = sorted((set(live) - set(miss)) | set(etf_list()))
        log(f"bars: holdout year for {len(syms)} tickers")
        bars.ingest_daily(syms, start=s.holdout_start, end=s.holdout_end, log=log)
        ev = pl.col("first_snapshot").cast(pl.Datetime("us")).dt.replace_time_zone("America/New_York").dt.convert_time_zone("UTC")
        lines = lines.with_columns(event_ts=ev, known_ts=ev)
        ranks = pit.rank_universe(lines, t)
        log(f"universe_pit (in memory): {ranks['snapshot'].n_unique()} snapshots, last {ranks['snapshot'].max()}")
        return pit.load_pit_panel(pit=ranks, lines=lines)


def evaluate(p, log=print) -> list[str]:
    """Steps 3-4. Returns the report paths."""
    from trader.backtest import engine
    from trader.live.runner import pin_ok
    from trader.research import metrics, registry, stats
    from trader.research.runner import spy_returns
    from trader.signals.library import REGISTRY

    s = settings()
    tag = s.raw["live"].get("strategy_tag", "")
    ok, why = pin_ok(tag)
    if not ok:
        raise RuntimeError(f"P7 needs the frozen strategy code ({tag}): {why}")
    hs = np.datetime64(s.holdout_start)
    first = int(np.argmax(p.dates >= hs))
    if p.dates[first] < hs:
        raise RuntimeError("panel has no holdout sessions")
    sl = slice(first, None)
    spy = spy_returns(p)
    paths = []
    for sid, params in CANDIDATES:
        ins = _in_sample(sid, params)
        tg = REGISTRY[sid](**params).targets(p)
        res = {}
        for label, cm, dly in (("base", 1.0, 0), ("2x", 2.0, 0), ("delay", 1.0, 1)):
            r = engine.run(p, tg, engine.CostModel.from_settings(cm), engine.Constraints.from_limits(), delay=dly)
            summ = metrics.summary(r.net[sl], r.gross[sl], r.turnover[sl], p.dates[sl], spy[sl])
            tid = registry.record(
                family=REGISTRY[sid].family, strategy_id=sid,
                params={**params, "constraints": "limits", "universe": "pit", "window": "holdout"},
                cost_mult=cm, delay=dly, data_start=str(p.dates[first]), data_end=str(p.dates[-1]),
                leak_check="n/a (frozen code, P7)", status="ok", metrics=summ, dates=p.dates[sl], net=r.net[sl],
                sharpe_daily=stats.sharpe(r.net[sl]), notes=f"P7 holdout run ({REASON})", holdout_touched=True)
            res[label] = (tid, summ, r)
            log(f"{sid} holdout {label}: Sharpe {summ['sharpe']:.2f}, CAGR {summ['cagr']:+.1%}, max DD {summ['max_dd']:.1%} ({tid})")
        paths.append(_report(sid, params, ins, res, p, sl, spy))
    return paths


def _report(sid, params, ins, res, p, sl, spy) -> str:
    from trader.research import metrics

    base = res["base"][1]
    spy_m = metrics.summary(spy[sl], spy[sl], np.zeros_like(spy[sl]), p.dates[sl], spy[sl])
    checks = [
        ("holdout Sharpe > 0.5", base["sharpe"] > 0.5, f"{base['sharpe']:.2f}"),
        ("holdout Sharpe >= 50% of in-sample", base["sharpe"] >= 0.5 * ins["sharpe"],
         f"{base['sharpe']:.2f} vs 50% x {ins['sharpe']:.2f} = {0.5 * ins['sharpe']:.2f}"),
        ("max DD within 1.5x in-sample", base["max_dd"] >= 1.5 * ins["max_dd"],
         f"{base['max_dd']:.1%} vs 1.5 x {ins['max_dd']:.1%} = {1.5 * ins['max_dd']:.1%}"),
    ]
    passed = all(c[1] for c in checks)
    rows = [("CAGR", "cagr", "{:+.1%}"), ("Sharpe", "sharpe", "{:.2f}"), ("Sortino", "sortino", "{:.2f}"),
            ("Max drawdown", "max_dd", "{:.1%}"), ("Annual turnover", "turnover_ann", "{:.1f}x"),
            ("% positive months", "pct_pos_months", "{:.0%}"), ("Beta to SPY", "beta_spy", "{:.2f}")]

    def cell(m, k, f):
        v = m.get(k)
        return f.format(v) if isinstance(v, (int, float)) and np.isfinite(v) else "n/a"

    lines = [
        f"# P7 holdout: {sid} {json.dumps(params)}", "",
        f"**{'PASS' if passed else 'FAIL'}.** Run once on the locked holdout year, {p.dates[sl][0]} .. {p.dates[-1]} "
        f"({len(p.dates[sl])} sessions), with the frozen code (`{settings().raw['live'].get('strategy_tag')}`), point-in-time universe and "
        f"gateway constraints. Approval: {REASON}. Run at {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.", "",
        f"Trials (holdout_touched=1): base `{res['base'][0]}`, 2x `{res['2x'][0]}`, delay+1 `{res['delay'][0]}`. "
        f"In-sample reference: `{ins['trial_id']}`.", "",
        "| metric | holdout base | holdout 2x | holdout delay+1 | SPY holdout | in-sample base |",
        "|---|---|---|---|---|---|",
        *[f"| {n} | {cell(res['base'][1], k, f)} | {cell(res['2x'][1], k, f)} | {cell(res['delay'][1], k, f)} | "
          f"{cell(spy_m, k, f) if k not in ('turnover_ann',) else '0.0x'} | {cell(ins, k, f)} |" for n, k, f in rows],
        "", "P7 gate (docs/PLAN.md):", "",
        *[f"- {name}: **{'PASS' if ok else 'FAIL'}** ({detail})" for name, ok, detail in checks],
        "", "One year of daily returns gives a Sharpe standard error of about 1.0, so this is a sanity check that the "
        "strategy still behaves like its backtest, not a measurement of its edge.",
    ]
    path = ROOT / "experiments" / "reports" / f"{res['base'][0]}-P7.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)
