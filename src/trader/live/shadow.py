"""Shadow forward tests (D38, docs/STRATEGIES.md "Shadow forward tests"). No orders, no broker.

`record()` runs in the evening job after the forward step. For each candidate in settings.toml [shadow] it
computes the target with the live runner's code on the in-memory panel (D16) and appends one row to
live/shadow/targets.jsonl: the decision session, when it was computed, the git commit, whether the tree was
dirty, and the weights (None = not a rebalance day). Rows are never rewritten. A row computed at or after the
next session's open is marked late and the evaluator ignores it, so a missed evening can only lose a
decision, never look ahead.

`report()` evaluates the recorded targets with the backtest engine (next-open fills, D37 costs at base and
2x, gateway caps and min-drift) on forward bars, against SPY over the same sessions. Until a candidate's
read date it shows data health only; `force=True` shows performance and is logged as a forced look.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import numpy as np

from trader.config import ROOT, settings

FOLDER = ROOT / "live" / "shadow"
MAX_CANDIDATES = 3


def candidates() -> list[dict]:
    c = settings().raw.get("shadow", {}).get("candidates", [])
    if len(c) > MAX_CANDIDATES:
        raise ValueError(f"{len(c)} shadow candidates configured; at most {MAX_CANDIDATES} (D38)")
    return c


def read_sessions() -> int:
    return int(settings().raw.get("shadow", {}).get("read_sessions", 126))


def read_rows(folder=None) -> list[dict]:
    f = (folder or FOLDER) / "targets.jsonl"
    if not f.exists():
        return []
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]


def usable(rows: list[dict], name: str) -> list[dict]:
    """Pure: a candidate's rows that count (not late, no error), first row per decision date."""
    seen, out = set(), []
    for r in rows:
        if r["name"] != name or r.get("late") or r.get("error") or r["decision_date"] in seen:
            continue
        seen.add(r["decision_date"])
        out.append(r)
    return sorted(out, key=lambda r: r["decision_date"])


def target_matrix(rows: list[dict], dates: list[str], symbols: list[str]) -> np.ndarray:
    """Pure: T x N targets for the engine. A row with weights sets that day's decision (names not listed get
    0); weights None, or no row, means "no rebalance" (NaN). Unknown symbols raise: the panel must cover them."""
    col = {s: j for j, s in enumerate(symbols)}
    idx = {d: i for i, d in enumerate(dates)}
    tg = np.full((len(dates), len(symbols)), np.nan)
    for r in rows:
        i = idx.get(r["decision_date"])
        if i is None or r.get("weights") is None:
            continue
        tg[i] = 0.0
        for s, w in r["weights"].items():
            if s == "$CASH":
                continue
            tg[i, col[s]] = w
    return tg


def perf(net: np.ndarray, spy: np.ndarray) -> dict:
    """Pure: forward-window summary vs SPY over the same sessions."""
    def dd(r):
        eq = np.cumprod(1 + r)
        return float((eq / np.maximum.accumulate(eq) - 1).min()) if len(r) else 0.0

    def sh(r):
        return float(r.mean() / r.std(ddof=1) * np.sqrt(252)) if len(r) > 1 and r.std(ddof=1) > 0 else float("nan")

    act = net - spy
    n = len(net)
    return {"sessions": n, "ret": float(np.prod(1 + net) - 1), "spy_ret": float(np.prod(1 + spy) - 1),
            "sharpe": sh(net), "spy_sharpe": sh(spy), "max_dd": dd(net), "spy_max_dd": dd(spy),
            "active_ann": float(act.mean() * 252) if n else float("nan"), "ir": sh(act)}


# ---------------------------------------------------------------- I/O below


def _llm_attach(p):
    """Attach S16's news fields to a memory panel (forward scores only: D5 and D13)."""
    import dataclasses

    import polars as pl

    from trader import calendar
    from trader.data import lake
    from trader.llm.scorer import LLM_SCORES
    from trader.signals.news import llm_fields

    s = settings()
    eval_start = datetime.fromisoformat(s.raw["llm"]["scoring_eval_start"]).replace(tzinfo=UTC)
    sc = (lake.scan(LLM_SCORES, allow_forward=True)
          .filter((pl.col("model") == s.scoring_model) & (pl.col("prompt_version") == s.prompt_version)
                  & (pl.col("event_ts") >= eval_start))
          .select("symbol", "known_ts", (pl.col("direction") * pl.col("magnitude") * pl.col("confidence")).alias("s"))
          .collect())
    sess = calendar.sessions_frame().select("date", "open_ts").sort("date")
    dates = [str(d) for d in p.dates.astype("O")]
    after = sess.filter(pl.col("date") > p.dates[-1].astype("O"))["open_ts"][0]
    opens = sess.filter(pl.col("date").cast(pl.Utf8).is_in(dates))["open_ts"].to_list() + [after]
    open_ns = np.array([int(o.timestamp() * 1e9) for o in opens], dtype=np.int64)
    known_ns = np.array([int(t.timestamp() * 1e9) for t in sc["known_ts"].to_list()], dtype=np.int64)
    sig, cnt = llm_fields(p.symbols, open_ns, sc["symbol"].to_list(), known_ns, sc["s"].to_numpy().astype(float))
    return dataclasses.replace(p, fields={**p.fields, "llm_signal": sig, "llm_count": cnt})


def _next_open(d: date) -> datetime:
    import polars as pl

    from trader import calendar

    return calendar.sessions_frame().filter(pl.col("date") > d).sort("date")["open_ts"][0]


def record(log=print, now: datetime | None = None) -> int:
    """Append today's decision for every candidate that has started. Returns rows written."""
    from trader.live import runner
    from trader.research.registry import _git
    from trader.signals.library import REGISTRY
    from trader.universe.build import etf_list, load_seed

    d = runner.last_closed_session()
    now = now or datetime.now(UTC)
    late = now >= _next_open(d)
    done = {(r["name"], r["decision_date"]) for r in read_rows()}
    sha, dirty = _git()
    panels: dict[bool, object] = {}
    out = []
    for c in candidates():
        name, sid, params = c["name"], c["strategy"], dict(c.get("params") or {})
        if date.fromisoformat(str(c["start"])) > d or (name, d.isoformat()) in done:
            continue
        row = {"name": name, "strategy": sid, "params": params, "decision_date": d.isoformat(),
               "computed_at": now.isoformat(), "late": late, "git": sha, "dirty": dirty, "weights": None}
        try:
            strat = REGISTRY[sid](**params)
            if strat.stock_universe not in panels:
                syms = etf_list() + (load_seed()["symbol"].to_list() if strat.stock_universe else [])
                panels[strat.stock_universe] = runner.memory_panel(syms)
            p = panels[strat.stock_universe]
            if getattr(strat, "needs_llm", False):
                p = _llm_attach(p)
            if str(p.dates[-1]) != d.isoformat():
                raise RuntimeError(f"memory panel ends {p.dates[-1]}, expected {d}")
            last = strat.targets(p)[-1]
            if not np.all(np.isnan(last)):
                row["weights"] = {s: round(float(x), 6) for s, x in zip(p.symbols, np.nan_to_num(last), strict=True) if x > 1e-6}
        except Exception as e:  # one candidate's failure never blocks another or the accounts
            row["error"] = f"{type(e).__name__}: {e}"
            from trader.monitor.alerts import notify

            notify("warning", f"shadow {name}: no decision recorded for {d}: {row['error'][:200]}")
        out.append(row)
        w = row["weights"]
        log(f"shadow {name} {d}: " + (f"ERROR {row['error']}" if "error" in row else
                                       "no rebalance" if w is None else f"{len(w)} names, gross {sum(w.values()):.2f}")
            + (" (LATE: ignored by the evaluator)" if late else ""))
    if out:
        FOLDER.mkdir(parents=True, exist_ok=True)
        with (FOLDER / "targets.jsonl").open("a", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r) + "\n")
    return len(out)


def _evaluate(rows: list[dict]) -> dict | None:
    from trader.backtest import engine
    from trader.live import runner

    first = rows[0]["decision_date"]
    syms = sorted({"SPY", "SHY", *(s for r in rows for s in (r.get("weights") or {}) if s != "$CASH")})
    d_last = runner.last_closed_session()
    from trader import calendar

    n = len(calendar.sessions(date.fromisoformat(first), d_last + timedelta(days=1)))  # first .. d_last inclusive
    p = runner.memory_panel(syms, sessions=max(n, 2))
    dates = [str(x) for x in p.dates]
    if first not in dates:
        return None
    i0 = dates.index(first)
    tg = target_matrix(rows, dates, p.symbols)
    spy = np.nan_to_num(p.returns()[:, p.col("SPY")])[i0 + 1 :]
    out = {}
    for m in (1.0, 2.0):
        res = engine.run(p, tg, engine.CostModel.from_settings(m), engine.Constraints.from_limits())
        out["base" if m == 1.0 else "2x"] = perf(res.net[i0 + 1 :], spy)
    return out


def report(force: bool = False, log=print) -> str:
    rows = read_rows()
    need = read_sessions()
    L = ["# Shadow forward tests (D38)", "",
         f"Read once at {need} forward sessions per candidate. Before that only data health is shown "
         "(`--force` shows performance and is logged as a forced look).", ""]
    looks = []
    for c in candidates():
        name = c["name"]
        mine = [r for r in rows if r["name"] == name]
        use = usable(rows, name)
        late = sum(1 for r in mine if r.get("late"))
        errs = sum(1 for r in mine if r.get("error"))
        decided = [r for r in use if r.get("weights") is not None]
        L += [f"## {name}: {c['strategy']} {json.dumps(c.get('params') or {}, sort_keys=True)}", "",
              f"Start {c['start']}; {len(mine)} rows recorded ({len(use)} usable, {late} late, {errs} errors); "
              f"{len(decided)} rebalance decisions"]
        if not use:
            L += ["", "No usable decisions yet.", ""]
            continue
        from trader import calendar
        from trader.live import runner

        # sessions after the first decision, through the last closed one (the first has no return yet)
        elapsed = len(calendar.sessions(date.fromisoformat(use[0]["decision_date"]),
                                        runner.last_closed_session() + timedelta(days=1))) - 1
        L.append(f"Forward sessions since the first decision: {elapsed} of {need}"
                 + (" -> **read due**" if elapsed >= need else ""))
        if decided:
            w = decided[-1]["weights"]
            top = sorted(w.items(), key=lambda kv: -kv[1])[:8]
            L.append(f"Latest decision {decided[-1]['decision_date']}: " + ", ".join(f"{s} {x:.0%}" for s, x in top)
                     + (f" (+{len(w) - 8} more)" if len(w) > 8 else ""))
        dirty = sum(1 for r in use if r.get("dirty"))
        commits = sorted({r["git"][:8] for r in use})
        L.append(f"Code: {len(commits)} commit(s) {', '.join(commits[-3:])}; {dirty} rows from a dirty tree")
        if elapsed >= need or force:
            ev = _evaluate(use)
            looks.append({"name": name, "at": datetime.now(UTC).isoformat(), "sessions": elapsed, "forced": elapsed < need,
                          **({"result": ev} if ev else {})})
            if ev:
                L += ["", "| | sessions | return | SPY | Sharpe | SPY Sharpe | max DD | SPY max DD | active/yr | IR |",
                      "|---|---|---|---|---|---|---|---|---|---|"]
                for k, v in ev.items():
                    L.append(f"| {k} | {v['sessions']} | {v['ret']:+.2%} | {v['spy_ret']:+.2%} | {v['sharpe']:.2f} | "
                             f"{v['spy_sharpe']:.2f} | {v['max_dd']:.1%} | {v['spy_max_dd']:.1%} | {v['active_ann']:+.1%} | {v['ir']:.2f} |")
                if elapsed < need:
                    L.append(f"\n**Forced look at {elapsed} of {need} sessions: not the read; logged.**")
        L.append("")
    FOLDER.mkdir(parents=True, exist_ok=True)
    if looks:
        with (FOLDER / "looks.jsonl").open("a", encoding="utf-8") as f:
            for x in looks:
                f.write(json.dumps(x) + "\n")
    path = FOLDER / "report.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    log(f"shadow report: {path}")
    return str(path)
