"""Replay the LIVE code path (runner.run + gateway.run, unchanged) over past sessions with a simulated
broker, and compare the result with the backtest engine on the same window.

What is real: runner.run (pin check, entry, carry-forward, target writing), the CLI's pending-run
selection, gateway.run (validation, caps, chunking, turnover/order limits, min drift, reconciliation,
halts, write-ahead order log). What is simulated:
  * runner.memory_panel / last_closed_session -> the lake panel truncated at the replay day
    (pre-holdout data only; the window must end before HOLDOUT_START);
  * the broker -> SimBroker: market orders fill at the session open +/- the modeled cost (the
    backtest's fill), positions marked at the close, and sale proceeds settle the next session
    (cash-account buying power);
  * notify() -> collected, never sent; live/orders.db -> a temporary database.

This is a parity test of the machinery, not a strategy trial: it doesn't select or tune anything,
so it is not recorded in the experiment registry (the strategy's own trials already are)."""

from __future__ import annotations

import dataclasses
import tempfile
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from trader.backtest import engine
from trader.backtest.panel import Panel
from trader.config import settings
from trader.risk.broker import Account, BrokerOrder, Position

NY = ZoneInfo("America/New_York")
HISTORY = 300  # sessions the live runner fetches


def window(p: Panel, t: int, n: int = HISTORY) -> Panel:
    """Rows max(0, t-n+1)..t: what memory_panel would return at the close of day t."""
    s = slice(max(0, t - n + 1), t + 1)
    return dataclasses.replace(
        p, dates=p.dates[s], open=p.open[s], close=p.close[s], raw_close=p.raw_close[s],
        dollar_volume=p.dollar_volume[s],
        pit_rank=None if p.pit_rank is None else p.pit_rank[s],
        events=None if p.events is None else p.events[s],
        fields={k: v[s] for k, v in p.fields.items()},
    )


class SimBroker:
    """Fills market orders at the open of the current session (+/- modeled cost per side)."""

    def __init__(self, cash: float, panel: Panel, cost: engine.CostModel):
        self.p, self.cost = panel, cost
        self.cash, self.unsettled = cash, 0.0
        self.qty: dict[str, float] = {}
        self.orders: list[BrokerOrder] = []
        self.t = 0
        self.last_equity = cash
        self.is_open = True

    def _px(self, sym: str, which: str = "close") -> float:
        arr = self.p.open if which == "open" else self.p.close
        v = arr[self.t, self.p.col(sym)]
        if not np.isfinite(v):  # not trading today: last known close
            col = self.p.close[: self.t + 1, self.p.col(sym)]
            v = col[np.isfinite(col)][-1]
        return float(v)

    def equity(self) -> float:
        return self.cash + sum(q * self._px(s) for s, q in self.qty.items())

    def new_day(self, t: int) -> None:
        self.last_equity = self.equity()  # marks at the previous close
        self.t = t
        self.unsettled = 0.0  # T+1: yesterday's sale proceeds have settled

    def account(self) -> Account:
        # mark at the open (the gateway runs shortly after it)
        eq = self.cash + sum(q * self._px(s, "open") for s, q in self.qty.items())
        return Account(eq, self.last_equity, self.cash, max(self.cash - self.unsettled, 0.0))

    def positions(self) -> dict[str, Position]:
        return {s: Position(s, q, q * self._px(s, "open")) for s, q in self.qty.items() if q > 1e-12}

    def open_orders(self):
        return []

    def orders_since(self, iso_ts):
        return [o for o in self.orders if o.client_order_id.endswith(f"@{iso_ts[:10]}")] or \
            [o for o in self.orders if getattr(o, "_day", None) == iso_ts[:10]]

    def order_by_client_id(self, cid):
        return next((o for o in self.orders if o.client_order_id == cid), None)

    def submit(self, cid, symbol, side, notional, qty):
        open_px = self._px(symbol, "open")
        c = float(self.cost.per_side(np.array([self.p.is_etf[self.p.col(symbol)]]))[0])
        px = open_px * (1 + c) if side == "buy" else open_px * (1 - c - engine.SEC_FEE_BPS_ON_SELLS / 1e4)
        q = qty if qty is not None else notional / open_px
        held = self.qty.get(symbol, 0.0)
        if side == "sell":
            q = min(q, held)
            self.cash += q * px
            self.unsettled += q * px
            self.qty[symbol] = held - q
            if self.qty[symbol] <= 1e-9:
                self.qty.pop(symbol)
        else:
            if q * px > self.cash - self.unsettled + 1e-6:
                o = BrokerOrder(f"s{len(self.orders)}", cid, symbol, side, "rejected", notional, qty, 0.0, None)
                self.orders.append(o)
                return o
            self.cash -= q * px
            self.qty[symbol] = held + q
        o = BrokerOrder(f"s{len(self.orders)}", cid, symbol, side, "filled", notional, qty, q, px)
        self.orders.append(o)
        return o

    def cancel_all(self):
        return 0

    def close_all(self):
        return 0

    def market_open(self):
        return self.is_open


def run(start: date, end: date, capital: float = 200.0, auto_clear: bool = False, log=print) -> dict:
    """Replay sessions in [start, end). Returns daily replay vs engine returns and every alert/event.
    auto_clear=True clears human halts the next morning (as if reviewed at once), to measure tracking
    of the machinery separately from the halt policy."""
    from trader.backtest.panel import load_panel
    from trader.config import limits
    from trader.live import runner
    from trader.monitor import alerts
    from trader.risk import gateway, state
    from trader.signals.library import REGISTRY
    from trader.universe.build import etf_list, load_seed

    if end > settings().holdout_start:
        raise ValueError(f"replay must end before the holdout ({settings().holdout_start})")
    live = settings().raw["live"]
    sid, params = live.get("strategy"), live.get("params", {})
    syms = etf_list() + ([] if not REGISTRY[sid].stock_universe else load_seed()["symbol"].to_list())
    p = load_panel(syms, start=date(start.year - 2, 1, 1), end=end)
    days = [i for i, d in enumerate(p.dates.astype("O")) if d >= start]
    lim = limits()
    allow, etfs = set(etf_list()) | set(load_seed()["symbol"].to_list()), set(etf_list())

    tmp = Path(tempfile.mkdtemp(prefix="replay-"))
    sent: list[tuple[str, str]] = []
    saved = (state.DB, state.KILL_FILE, runner.memory_panel, runner.last_closed_session, alerts.notify, state.now_iso)
    state.DB, state.KILL_FILE = tmp / "orders.db", tmp / "KILL"
    alerts.notify = lambda level, msg: sent.append((level, msg))
    broker = SimBroker(capital, p, engine.CostModel.from_settings())
    eq: list[float] = []
    statuses: list[str] = []
    try:
        con = state.connect()
        for k, t in enumerate(days):
            d = p.dates[t].astype("O")
            broker.new_day(t)
            # morning: the gateway at 09:40 ET on the latest pending target (as the CLI selects it)
            if auto_clear:
                for kind in state.HUMAN_HALTS:
                    state.clear_halt(con, kind, "replay-auto")
            row = con.execute("SELECT run_id FROM runs WHERE status IN ('target_written','incomplete') ORDER BY created_at DESC LIMIT 1").fetchone()
            state.now_iso = (lambda d=d: datetime.combine(d, time(9, 40), NY).isoformat())
            if row:
                con.execute("UPDATE runs SET status='superseded' WHERE status IN ('target_written','incomplete') AND run_id != ?", (row[0],))
                con.commit()
                res = gateway.run(row[0], broker, con, allow, etfs, lim, datetime.combine(d, time(9, 40), NY), dry_run=False)
                statuses.append(f"{d} {row[0]}: {res.status} {res.submitted} orders, {len(res.deferred)} deferred, {len(res.refused)} refused"
                                + (f" {sorted(set(res.deferred))[:3]}" if res.deferred else ""))
            eq.append(broker.equity())
            # evening: the runner after the close of d (created_at must order runs: patch the clock)
            runner.last_closed_session = lambda d=d: d
            runner.memory_panel = lambda symbols, sessions=HISTORY, t=t: window(p, t, sessions)
            state.now_iso = (lambda d=d: datetime.combine(d, time(19, 5), NY).isoformat())
            runner.run(log=lambda *_: None)
        events = con.execute("SELECT ts, level, kind, detail FROM events WHERE level IN ('warning','error','critical')").fetchall()
        halts = con.execute("SELECT kind, session, reason FROM halts").fetchall()
    finally:
        state.DB, state.KILL_FILE, runner.memory_panel, runner.last_closed_session, alerts.notify, state.now_iso = saved

    # engine on the same window: the strategy's targets from its first decision on or after start - 1
    strat = REGISTRY[sid](**params)
    full = strat.targets(p)  # point-in-time row by row (leak-checked), so no truncation needed
    first = days[0]  # the first runner run is after the close of the first replay day
    tg = np.full_like(full, np.nan)
    tg[first:] = full[first:]
    if np.all(np.isnan(tg[first])):  # D28 entry: adopt the latest scheduled decision, as the runner does
        prev = np.where(~np.all(np.isnan(full[: first + 1]), axis=1))[0]
        if len(prev):
            tg[first] = full[prev[-1]]
    res = engine.run(p, tg, engine.CostModel.from_settings(), engine.Constraints.from_limits())
    eng = res.net[days]
    rep = np.diff(np.r_[capital, eq]) / np.r_[capital, eq[:-1]]
    return {"dates": p.dates[days], "replay": rep, "engine": eng, "equity": np.array(eq), "statuses": statuses,
            "alerts": sent, "events": events, "halts": halts, "orders": len(broker.orders),
            "rejected": [o for o in broker.orders if o.status == "rejected"]}
