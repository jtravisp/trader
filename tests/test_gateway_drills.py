"""P4 failure drills against a fake broker (plus planning rules), using the real limits.yaml."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trader.config import limits
from trader.risk import gateway, state
from trader.risk.broker import Account, BrokerOrder, Position

NY = ZoneInfo("America/New_York")
OPEN_TIME = datetime(2026, 11, 4, 10, 0, tzinfo=NY)
ALLOW = {"SPY", "TLT", "GLD", "AAPL", "MSFT", "XOM"}
ETFS = {"SPY", "TLT", "GLD"}


class FakeBroker:
    def __init__(self, equity=1000.0, last_equity=1000.0, positions=None, is_open=True):
        self.acct = Account(equity, last_equity, equity, equity)
        self.pos = positions or {}
        self.orders: list[BrokerOrder] = []
        self.is_open = is_open
        self.cancelled = 0
        self.fail_after_record = False

    def account(self):
        return self.acct

    def positions(self):
        return self.pos

    def open_orders(self):
        return [o for o in self.orders if o.status == "new"]

    def orders_since(self, iso_ts):
        return list(self.orders)

    def order_by_client_id(self, cid):
        return next((o for o in self.orders if o.client_order_id == cid), None)

    def submit(self, cid, symbol, side, notional, qty):
        o = BrokerOrder(f"b{len(self.orders)}", cid, symbol, side, "new", notional, qty, 0.0, None)
        self.orders.append(o)
        if self.fail_after_record:
            raise ConnectionError("network dropped after the broker accepted the order")
        return o

    def cancel_all(self):
        self.cancelled += 1
        return len(self.open_orders())

    def close_all(self):
        return len(self.pos)

    def market_open(self):
        return self.is_open


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "KILL_FILE", tmp_path / "KILL")
    alerts = []
    import trader.monitor.alerts as al

    monkeypatch.setattr(al, "notify", lambda level, msg: alerts.append((level, msg)))
    con = state.connect(tmp_path / "orders.db")
    return con, alerts


def _run(con, broker, run_id, weights, dry=False, now=OPEN_TIME):
    if weights is not None:
        state.write_target(con, run_id, "TEST", weights)
    return gateway.run(run_id, broker, con, ALLOW, ETFS, limits(), now, dry_run=dry)


# ---------------------------------------------------------------- drills
def test_drill_crash_mid_run_then_restart_no_duplicates(env):
    con, _ = env
    b = FakeBroker()
    b.fail_after_record = True  # broker accepts, we never see the ack (crash/network loss)
    _run(con, b, "r1", {"SPY": 0.30, "TLT": 0.30})
    n_before = len(b.orders)
    assert n_before > 0
    assert con.execute("SELECT status FROM runs WHERE run_id='r1'").fetchone()[0] == "incomplete"
    assert {r[0] for r in con.execute("SELECT status FROM orders")} == {"submitting"}  # outcome unknown
    b.fail_after_record = False
    # restart: the same run id resumes; reconcile finds the orders at the broker
    res = gateway.run("r1", b, con, ALLOW, ETFS, limits(), OPEN_TIME, dry_run=False)
    assert len(b.orders) == n_before, "restart must not resubmit orders the broker already has"
    assert res.status == "executed"
    assert len({o.client_order_id for o in b.orders}) == len(b.orders)
    assert {r[0] for r in con.execute("SELECT status FROM orders")} == {"new"}


def test_drill_order_lost_before_broker_is_resubmitted_once(env):
    con, _ = env
    b = FakeBroker()
    state.write_target(con, "r1", "TEST", {"SPY": 0.10})
    # simulate a hard kill after the write-ahead row but before the broker saw anything
    cid = gateway.PlannedOrder("SPY", "buy", 100.0, None).client_order_id("r1")
    con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?)", (cid, "r1", "SPY", "buy", 100.0, None, state.now_iso(), None, "submitting", None, ""))
    con.commit()
    gateway.run("r1", b, con, ALLOW, ETFS, limits(), OPEN_TIME, dry_run=False)
    assert [o.client_order_id for o in b.orders] == [cid]


def test_drill_corrupted_target_refused(env):
    con, alerts = env
    b = FakeBroker()
    for i, bad in enumerate([{"SPY": float("nan")}, {"SPY": -0.5}, {"SPY": 0.9, "TLT": 0.9}, {}]):
        res = _run(con, b, f"bad{i}", bad)
        assert res.status == "rejected", bad
    con.execute("INSERT INTO targets VALUES ('bad9', 'x', 'TEST', 'SPY', 'abc')")
    con.execute("INSERT INTO runs VALUES ('bad9', 'x', 'TEST', 'target_written', '')")
    con.commit()
    assert gateway.run("bad9", b, con, ALLOW, ETFS, limits(), OPEN_TIME, dry_run=False).status == "rejected"
    assert b.orders == []
    assert sum(1 for lvl, m in alerts if "target_rejected" in m) == 5


def test_drill_daily_loss_halt_blocks_buys_and_auto_resumes(env):
    con, alerts = env
    b = FakeBroker(equity=950, last_equity=1000, positions={"AAPL": Position("AAPL", 1, 200)})
    res = _run(con, b, "r1", {"AAPL": 0.0, "SPY": 0.30})
    assert all(o.side == "sell" for o in b.orders) and b.orders
    assert any("daily_loss" in m for _, m in alerts)
    assert any("no new buys" in why for _, why in res.refused)
    # next session: daily-loss halt no longer applies
    nxt = datetime(2026, 11, 5, 10, 0, tzinfo=NY)
    assert not [h for h in state.active_halts(con, "2026-11-05") if h["kind"] == "daily_loss"]
    b2 = FakeBroker(equity=950, last_equity=950)
    res2 = _run(con, b2, "r2", {"SPY": 0.30}, now=nxt)
    assert res2.status == "executed" and b2.orders


def test_drill_peak_drawdown_needs_human(env):
    con, alerts = env
    _run(con, FakeBroker(equity=1000), "r0", {"$CASH": 1.0})  # establishes peak 1000
    b = FakeBroker(equity=840, last_equity=850)
    assert _run(con, b, "r1", {"SPY": 0.3}).status == "halted"
    assert _run(con, b, "r2", {"SPY": 0.3}, now=datetime(2026, 11, 9, 10, 0, tzinfo=NY)).status == "halted"
    assert b.orders == []
    assert state.clear_halt(con, "peak_drawdown", "test-operator") == 1
    con.execute("DELETE FROM equity")  # operator resets the peak as part of the review
    con.commit()
    assert _run(con, b, "r3", {"SPY": 0.3}, now=datetime(2026, 11, 9, 10, 5, tzinfo=NY)).status == "executed"


def test_drill_kill_switch(env):
    con, alerts = env
    b = FakeBroker()
    gateway.kill(b, con)
    assert state.KILL_FILE.exists() and b.cancelled == 1
    assert _run(con, b, "r1", {"SPY": 0.3}).status == "killed"
    assert b.orders == []


def test_drill_unknown_broker_order_halts(env):
    con, alerts = env
    b = FakeBroker()
    b.orders.append(BrokerOrder("x1", "manual-123", "AAPL", "buy", "filled", 10, None, 0.05, 200))
    assert _run(con, b, "r1", {"SPY": 0.3}).status == "halted"
    assert any(h["kind"] == "reconciliation_mismatch" for h in state.active_halts(con, "2026-11-04"))


# ---------------------------------------------------------------- planning rules
def test_idempotent_rerun_same_run_id(env):
    con, _ = env
    b = FakeBroker()
    _run(con, b, "r1", {"SPY": 0.30})
    n = len(b.orders)
    assert gateway.run("r1", b, con, ALLOW, ETFS, limits(), OPEN_TIME, dry_run=False).status == "already_executed"
    assert len(b.orders) == n


def test_caps_chunks_turnover_allowlist_and_full_exit():
    lim = limits()
    plan = gateway.Plan()
    tgt, plan = gateway.validate_target([("TLT", 0.5), ("AAPL", 0.2), ("ZZZZ", 0.1)], ALLOW, lim)
    assert ("ZZZZ", "symbol not in allowlist") in plan.refused
    acct = Account(1000, 1000, 1000, 1000)
    pos = {"XOM": Position("XOM", 2.0, 50.0)}
    plan = gateway.plan_orders(tgt, acct, pos, ETFS, lim, 0, 0.0, False, plan)
    tlt = [o for o in plan.orders if o.symbol == "TLT"]
    assert sum(o.notional for o in tlt) == pytest.approx(350)  # clipped to the 35% ETF cap
    assert all(o.notional <= 120 + 1e-9 for o in tlt) and len(tlt) == 3  # D18 chunks <= 12% of equity
    assert [o for o in plan.orders if o.symbol == "XOM"][0].qty == 2.0  # full exit by quantity
    assert any("AAPL" in n for n in plan.notes)  # clipped to 10%
    total = sum(o.notional or 50 for o in plan.orders)
    assert total <= 0.60 * 1000 + 1e-6  # max daily turnover


def test_outside_trading_window(env):
    con, _ = env
    b = FakeBroker()
    assert _run(con, b, "r1", {"SPY": 0.3}, now=datetime(2026, 11, 4, 9, 31, tzinfo=NY)).status == "outside_window"
    assert b.orders == []


def test_benchmark_fallback_may_be_fully_invested_over_two_days():
    lim = limits()
    plan = gateway.plan_orders({"SPY": 1.0}, Account(1000, 1000, 1000, 1000), {}, ETFS, lim, 0, 0.0, False)
    assert not any("clipped" in n for n in plan.notes)  # D19: SPY is not clipped to the 35% ETF cap
    assert sum(o.notional for o in plan.orders) == pytest.approx(600)  # day 1 limited by 60% turnover
