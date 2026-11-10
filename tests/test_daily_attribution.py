"""Live-vs-model attribution (P4 tracking-error bound) and the P4 gate."""

from types import SimpleNamespace as F

import pytest

from trader.monitor.daily import attribution, gate

PX = {"SPY": {"prev_close": 760.0, "open": 765.0, "close": 770.0}}


def test_buildup_day_from_cash_at_open_fills_has_zero_exec_error():
    # start $200 cash; buy 0.15 SPY exactly at the open, no cost -> live == model
    q = 0.15
    fills = [F(symbol="SPY", side="buy", filled_qty=q, filled_avg_price=765.0)]
    cash = 200 - q * 765.0
    a = attribution({"SPY": q}, fills, PX, cash, 200.0, {"SPY": 1.0}, {"SPY"}, 0.0, 0.0)
    assert a["exec_te_bps"] == pytest.approx(0.0, abs=1e-9)
    assert a["live_ret"] == pytest.approx(q * 5 / 200)
    assert a["build_up"] is True  # started 100% away from a 100% SPY target
    assert a["strategy_gap_bps"] < 0  # live missed the overnight gain the target had


def test_slippage_and_costs_show_up_as_exec_error():
    q = 0.15
    fills = [F(symbol="SPY", side="buy", filled_qty=q, filled_avg_price=766.0)]  # paid $1 over the open
    cash = 200 - q * 766.0
    a = attribution({"SPY": q}, fills, PX, cash, 200.0, {"SPY": 1.0}, {"SPY"}, 2.0, 5.0)
    slip = -q * 1.0 / 200 * 1e4
    cost = q * 765.0 * 2.0 / 1e4 / 200 * 1e4
    assert a["exec_te_bps"] == pytest.approx(slip + cost)


def test_fill_at_execution_price_is_timing_not_execution_error():
    # the price drifted from the open (765) to 768 by 09:40; we bought exactly at 768 with no cost
    q = 0.15
    fills = [F(symbol="SPY", side="buy", filled_qty=q, filled_avg_price=768.0, submitted_at=None)]
    cash = 200 - q * 768.0
    a = attribution({"SPY": q}, fills, PX, cash, 200.0, {"SPY": 1.0}, {"SPY"}, 0.0, 0.0, {"SPY": 768.0})
    assert a["exec_te_bps"] == pytest.approx(0.0, abs=1e-9)
    assert a["timing_bps"] == pytest.approx(q * 3.0 / 200 * 1e4)  # the open would have been $3/share better
    assert a["exec_ref_missing"] == []
    # without an execution price the open is used, and the drift lands in the execution error
    b = attribution({"SPY": q}, fills, PX, cash, 200.0, {"SPY": 1.0}, {"SPY"}, 0.0, 0.0, {})
    assert b["exec_ref_missing"] == ["SPY"] and b["timing_bps"] == pytest.approx(0.0, abs=1e-9)
    assert b["exec_te_bps"] == pytest.approx(-q * 3.0 / 200 * 1e4)


def test_held_position_no_trades_matches_target_replay():
    q = 200 / 760.0
    a = attribution({"SPY": q}, [], PX, 0.0, 200.0, {"SPY": 1.0}, {"SPY"}, 2.0, 5.0)
    assert a["exec_te_bps"] == pytest.approx(0.0, abs=1e-9)
    assert a["strategy_gap_bps"] == pytest.approx(0.0, abs=1e-9)
    assert a["build_up"] is False


def test_missing_prices_skip():
    a = attribution({"ABC": 1.0}, [], PX, 0.0, 200.0, None, set(), 2.0, 5.0)
    assert a["missing_prices"] == ["ABC"] and "exec_te_bps" not in a


def _r(d, te, build_up=False, halts=(), errors=(), n_fills=5, te_ref="exec"):
    return {"date": f"2026-11-{d:02d}", "exec_te_bps": te, "build_up": build_up, "halts": list(halts),
            "errors": list(errors), "n_fills": n_fills, "te_ref": te_ref, "timing_bps": 1.0}


ALL = lambda d: True  # noqa: E731


def test_gate_skips_non_sessions():
    recs = [_r(d, 2.0) for d in (3, 4, 5, 6)] + [{"date": "2026-11-07", "exec_te_bps": None, "halts": [], "errors": []}]
    assert gate(recs, is_session=lambda d: d.weekday() < 5)["clean_streak"] == 4


def test_gate_counts_only_fill_days_with_exec_reference():
    # no-fill days extend the streak but say nothing about execution; open-based records don't count
    recs = [_r(d, 0.0, n_fills=0) for d in range(1, 11)]
    g = gate(recs, is_session=ALL)
    assert g["clean_streak"] == 10 and g["te_days"] == 0 and not g["pass"]
    recs[3] = _r(4, 30.0, te_ref=None)  # an old open-based record: counts toward the streak only
    recs[4] = _r(5, 3.0)
    assert gate(recs, is_session=ALL)["te_days"] == 1 and not gate(recs, is_session=ALL)["pass"]  # needs 2
    recs[5] = _r(6, 4.0, build_up=True)  # build-up days count for execution
    g = gate(recs, is_session=ALL)
    assert g["te_days"] == 2 and g["pass"] and g["mean_timing_bps"] == pytest.approx(1.0)


def test_gate_streak_resets():
    recs = [_r(1, 1.0, errors=["submit_failed"])] + [_r(d, 2.0, build_up=d < 4) for d in range(2, 12)]
    g = gate(recs, is_session=ALL)
    assert g["clean_streak"] == 10 and g["te_days"] == 10 and g["pass"]
    recs.append(_r(12, 50.0))
    recs.append(_r(12, 1.0))  # a rerun of the same date replaces the earlier record
    assert gate(recs, is_session=ALL)["clean_streak"] == 11
    recs.append(_r(13, 3.0, halts=["daily_loss"]))
    assert gate(recs, is_session=ALL)["clean_streak"] == 0 and not gate(recs, is_session=ALL)["pass"]


def test_gate_te_share():
    recs = [_r(d, 20.0 if d <= 2 else 1.0) for d in range(1, 11)]  # 8/10 within
    assert not gate(recs, is_session=ALL)["pass"]
