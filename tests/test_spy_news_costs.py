"""S14 / S15 SPY overlays, the S16 news field builder, and cost calibration from fills (D37, D38)."""

from dataclasses import dataclass
from datetime import date

import numpy as np
import pytest

from trader.backtest import engine
from trader.backtest.panel import Panel
from trader.monitor import costs
from trader.signals.news import NewsTilt, llm_fields
from trader.signals.spy import SpyTrend, SpyVolManaged


def spy_shy_panel(spy_close, sessions=None):
    c = np.array(spy_close, float)
    T = len(c)
    dates = np.array(sessions, dtype="datetime64[D]") if sessions is not None else \
        np.arange(np.datetime64("2020-01-01"), np.datetime64("2020-01-01") + T)
    close = np.column_stack([c, np.full(T, 80.0)])
    return Panel(dates=dates, symbols=["SPY", "SHY"], open=close.copy(), close=close, raw_close=close,
                 dollar_volume=np.full((T, 2), 1e9), is_etf=np.array([True, True]))


def test_s14_switches_to_shy_below_the_moving_average():
    # 5 rising days then a drop below the 3-day SMA
    p = spy_shy_panel([100, 101, 102, 103, 104, 90])
    w = SpyTrend(sma=3, rebalance="daily").targets(p)
    assert np.all(np.isnan(w[:2]))  # warm-up: no decision
    np.testing.assert_allclose(w[2:5], [[1, 0]] * 3)
    np.testing.assert_allclose(w[5], [0, 1])


def test_s15_scales_spy_by_target_over_realized_vol():
    rng = np.random.default_rng(0)
    r = np.r_[rng.normal(0, 0.005, 60), rng.normal(0, 0.03, 60)]  # calm, then ~48% annualized vol
    p = spy_shy_panel(100 * np.cumprod(1 + r))
    w = SpyVolManaged(vol_target=0.12, window=21, rebalance="daily").targets(p)
    assert np.all(np.isnan(w[:21]))
    assert w[59, 0] == pytest.approx(1.0)  # calm: capped at 100% SPY
    assert 0.1 < w[-1, 0] < 0.4 and w[-1, 0] + w[-1, 1] == pytest.approx(1.0)


@pytest.mark.parametrize("strat", [SpyTrend(sma=20, rebalance="weekly"),
                                   SpyVolManaged(vol_target=0.16, window=21, rebalance="monthly")])
def test_overlays_are_truncation_invariant_on_real_sessions(strat):
    from trader import calendar

    sess = calendar.sessions(date(2024, 1, 2), date(2024, 7, 1))
    rng = np.random.default_rng(1)
    p = spy_shy_panel(100 * np.cumprod(1 + rng.normal(0.0004, 0.01, len(sess))), [str(d) for d in sess])
    ok, why = engine.truncation_leak_check(strat, p)
    assert ok, why
    assert (~np.all(np.isnan(strat.targets(p)), axis=1)).sum() > 3  # some calendar rebalances happened


def test_llm_fields_entry_window():
    opens = np.array([0, 10, 20, 30, 40, 50, 60, 70], dtype=np.int64)  # 7 sessions + the next open
    # known at 15 -> enters at session 2 (open 20) -> counts for decision rows 1..5
    sig, cnt = llm_fields(["A", "B"], opens, ["A", "A", "Z"], np.array([15, 65, 5], np.int64), np.array([0.5, -1.0, 9.0]))
    # the second score (known at 65) enters at the next open (70): it counts only for the last decision row
    assert cnt[:, 0].tolist() == [0, 1, 1, 1, 1, 1, 1]
    assert sig[1, 0] == pytest.approx(0.5) and sig[5, 0] == pytest.approx(0.5) and sig[6, 0] == pytest.approx(-1.0)
    assert np.isnan(sig[0, 0]) and cnt[:, 1].sum() == 0  # unknown symbol Z ignored


def test_llm_fields_known_exactly_at_open_waits_for_the_next_open():
    opens = np.array([0, 10, 20, 30], dtype=np.int64)
    _, cnt = llm_fields(["A"], opens, ["A"], np.array([10], np.int64), np.array([1.0]))
    assert cnt[:, 0].tolist() == [0, 1, 1]  # entry = open 20 (session 2): rows 1, 2


def test_news_tilt_top_k_and_spy_remainder(monkeypatch):
    import trader.signals.news as news

    T, syms = 3, ["SPY", "A", "B", "C"]
    c = np.full((T, 4), 50.0)
    p = Panel(dates=np.arange(np.datetime64("2020-01-01"), np.datetime64("2020-01-01") + T), symbols=syms, open=c,
              close=c, raw_close=c, dollar_volume=np.full((T, 4), 1e9), is_etf=np.array([True, False, False, False]),
              fields={"llm_signal": np.array([[np.nan, 0.9, 0.2, -0.5]] * T), "llm_count": np.array([[0, 3, 1, 5]] * T, float)})
    # liquid_universe needs 48 sessions of history and schedule needs real sessions: stub both on this tiny panel
    monkeypatch.setattr(news, "liquid_universe", lambda panel, n=500: np.tile(~panel.is_etf, (panel.T, 1)))
    monkeypatch.setattr(news, "schedule", lambda panel, freq: np.ones(panel.T, bool))
    out = NewsTilt(k=2, min_docs=2).targets(p)
    np.testing.assert_allclose(out[0], [0.5, 0.5, 0.0, 0.0])  # only A qualifies (B has 1 doc, C is negative)


@dataclass
class F:
    symbol: str
    side: str
    filled_qty: float
    filled_avg_price: float
    submitted_at: str


def test_fill_rows_sign_and_reference():
    fills = [F("SPY", "buy", 1, 100.1, "2026-11-18T14:40:02Z"), F("AAA", "sell", 2, 49.9, "2026-11-18T14:40:03Z"),
             F("BBB", "buy", 1, 10.0, "2026-11-18T14:40:03Z")]  # BBB: no reference -> dropped
    ref = {(date(2026, 11, 18), "SPY"): 100.0, (date(2026, 11, 18), "AAA"): 50.0}
    rows = costs.fill_rows(fills, ref, {"SPY"})
    assert [r["cls"] for r in rows] == ["etf", "stock"]
    assert rows[0]["slip_bps"] == pytest.approx(10.0)  # paid 10 bps more on a buy
    assert rows[1]["slip_bps"] == pytest.approx(20.0)  # received 20 bps less on a sell


def test_summarize_groups_and_clustered_se():
    rows = [{"date": d, "symbol": "X", "side": "buy", "cls": "stock", "notional": n, "slip_bps": s}
            for d, n, s in [("a", 10, 10.0), ("a", 30, 20.0), ("b", 10, 0.0)]]
    out = costs.summarize(rows)
    assert set(out) == {"stock", "stock/buy"}
    st = out["stock"]
    assert st["n"] == 3 and st["days"] == 2 and st["mean"] == pytest.approx(10.0) and st["median"] == pytest.approx(10.0)
    assert st["wmean"] == pytest.approx((100 + 600) / 50)
    assert st["se"] == pytest.approx(np.sqrt((10.0**2 + 10.0**2) * 2) / 3)  # day sums of deviations: +10, -10


def test_engine_benchmark_cap_matches_gateway():
    """D19/D38: SPY may be held at 100% in the backtest, like the gateway; other ETFs stay at the ETF cap."""
    p = spy_shy_panel([100, 101, 102])
    cons = engine.Constraints(0.10, 0.35, 1.0, 0.0, "SPY", 1.0)
    w, clipped = engine.apply_constraints(np.array([1.0, 0.0]), p.is_etf, cons, engine.caps(p.is_etf, cons, p.symbols))
    assert w.tolist() == [1.0, 0.0] and not clipped
    w, clipped = engine.apply_constraints(np.array([0.0, 1.0]), p.is_etf, cons, engine.caps(p.is_etf, cons, p.symbols))
    assert w.tolist() == [0.0, 0.35] and clipped
    tg = np.full((3, 2), np.nan)
    tg[0] = [1.0, 0.0]
    res = engine.run(p, tg, engine.CostModel(0, 0), cons)
    assert res.exposure[1] == pytest.approx(1.0)
