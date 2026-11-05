"""Replay simulator pieces: settlement-aware buying power, cost-adjusted fills, history window."""

import numpy as np
import pytest

from trader.backtest import engine
from trader.research.replay import SimBroker, window
from test_engine_stats import make_panel


def _broker():
    p = make_panel([[100, 50], [110, 50], [120, 50]], [[105, 50], [115, 50], [125, 50]])
    return SimBroker(1000.0, p, engine.CostModel(10, 10)), p


def test_fills_at_open_with_cost_and_settlement():
    b, p = _broker()
    b.new_day(0)
    b.submit("c1", p.symbols[0], "buy", 500.0, None)
    assert b.cash == pytest.approx(1000 - 5 * 100 * 1.001)
    b.new_day(1)
    b.submit("c2", p.symbols[0], "sell", None, 5.0)
    proceeds = 5 * 110 * (1 - 0.001 - engine.SEC_FEE_BPS_ON_SELLS / 1e4)
    acct = b.account()
    assert acct.buying_power == pytest.approx(acct.cash - proceeds)  # unsettled until the next session
    assert b.submit("c3", p.symbols[1], "buy", acct.cash - 1, None).status == "rejected"
    b.new_day(2)
    assert b.account().buying_power == pytest.approx(b.cash)


def test_window_keeps_the_last_rows():
    _, p = _broker()
    w = window(p, 2, n=2)
    assert w.T == 2 and np.array_equal(w.close, p.close[1:3])
