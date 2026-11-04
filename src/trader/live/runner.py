"""Live runner: after the close, compute tomorrow's target portfolio and write it to live/orders.db.
The gateway executes it in the next session's trading window (same timing as the backtest).

D16: strategies get trailing history fetched from Alpaca into memory (never written to the lake).
D19: until a strategy passes P3, the configured strategy is FALLBACK (100% SPY).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from trader import calendar
from trader.config import settings
from trader.risk import state

FALLBACK = "FALLBACK"
# promote-strategy: a promoted strategy runs only from code identical to its frozen tag.
PINNED_PATHS = ("src/trader/signals", "src/trader/backtest/panel.py", "src/trader/live/runner.py",
                "src/trader/universe/build.py", "config/universe_seed.csv", "config/universe.yaml")


def pin_ok(tag: str) -> tuple[bool, str]:
    """True if the working tree's strategy code equals the frozen tag (committed or not)."""
    import subprocess

    from trader.config import ROOT

    if not tag:
        return False, "no strategy_tag configured"
    git = ["git", "-C", str(ROOT)]
    try:
        return _pin_check(git, tag)
    except OSError as e:  # git not on PATH: fail closed
        return False, f"git unavailable: {e}"


def _pin_check(git: list[str], tag: str) -> tuple[bool, str]:
    import subprocess

    if subprocess.run([*git, "rev-parse", "-q", "--verify", f"refs/tags/{tag}"], capture_output=True).returncode:
        return False, f"tag {tag} not found"
    d = subprocess.run([*git, "diff", "--quiet", tag, "--", *PINNED_PATHS], capture_output=True)
    return (d.returncode == 0, "ok" if d.returncode == 0 else f"strategy code differs from {tag}")


def last_closed_session() -> datetime.date:
    cutoff = datetime.now(UTC) - timedelta(minutes=20)
    return calendar.sessions_frame().filter(pl.col("close_ts") <= cutoff)["date"].max()


def memory_panel(symbols: list[str], sessions: int = 300):
    """Trailing daily panel for live use only (D16): fetched, never stored."""
    from trader.backtest.panel import panel_from_frame
    from trader.data.bars import _daily_frame, _fetch
    from trader.data.http import RawArchive

    end = last_closed_session() + timedelta(days=1)
    start = calendar.sessions(end - timedelta(days=int(sessions * 1.6)), end)[-sessions]
    arch = RawArchive("alpaca_bars_live", f"{end}")
    raw = pl.concat([_fetch(symbols[i : i + 100], "1Day", start, end, "raw", arch) for i in range(0, len(symbols), 100)])
    adj = pl.concat([_fetch(symbols[i : i + 100], "1Day", start, end, "all", arch) for i in range(0, len(symbols), 100)])
    return panel_from_frame(_daily_frame(raw, adj))


def compute_target(strategy_id: str, params: dict) -> dict[str, float] | None:
    if strategy_id == FALLBACK:
        return {"SPY": 1.0}
    from trader.signals.library import REGISTRY
    from trader.universe.build import etf_list, load_seed

    strat = REGISTRY[strategy_id](**params)
    syms = etf_list() + ([] if not strat.stock_universe else load_seed()["symbol"].to_list())
    p = memory_panel(syms)
    row = strat.targets(p)[-1]
    if np.all(np.isnan(row)):
        return None  # not a rebalance day for this strategy
    w = {s: float(x) for s, x in zip(p.symbols, np.nan_to_num(row), strict=True) if x > 1e-6}
    return w or {"$CASH": 1.0}


def run(log=print) -> str | None:
    live = settings().raw["live"]
    sid, params = live.get("strategy", FALLBACK), live.get("params", {})
    d = last_closed_session()
    run_id = f"{sid}-{d.isoformat()}"
    con = state.connect()
    if con.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
        log(f"runner: {run_id} already written")
        return run_id
    if sid != FALLBACK:
        ok, why = pin_ok(live.get("strategy_tag", ""))
        if not ok:
            from trader.monitor.alerts import notify

            notify("critical", f"runner: {sid} not run ({why}); no new target, gateway keeps the last one")
            log(f"runner: REFUSED {sid}: {why}")
            return None
    tgt = compute_target(sid, params)
    if tgt is None:
        # Not a rebalance day: carry the latest target forward so the gateway can finish orders it
        # deferred (turnover cap). The backtest reaches the target on rebalance day; min-drift keeps
        # a carried target from trading drifted positions.
        tgt, src = carried_target(con)
        if tgt is None:
            log(f"runner: {sid} does not rebalance after {d} and there is no earlier target")
            return None
        state.write_target(con, run_id, sid, tgt)
        log(f"runner: {sid} does not rebalance after {d}; carried {src} forward as {run_id}")
        return run_id
    state.write_target(con, run_id, sid, tgt)
    log(f"runner: wrote {run_id}: {len(tgt)} names, gross {sum(v for k, v in tgt.items() if k != '$CASH'):.2f}")
    return run_id


def carried_target(con) -> tuple[dict[str, float] | None, str | None]:
    row = con.execute("SELECT run_id FROM runs WHERE status != 'rejected' ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        return None, None
    return {s: float(w) for s, w in state.read_target(con, row[0])}, row[0]
