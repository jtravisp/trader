"""S07 vol scaling and the S08 stop-loss simulation in the engine."""

import numpy as np
import pytest

from trader.backtest import engine
from trader.research.overlays import vol_scale
from test_engine_stats import FREE, NOCOST, make_panel


def _one(opens, closes, lows):
    p = make_panel([[x] for x in opens], [[x] for x in closes])
    tg = np.full((len(opens), 1), np.nan)
    tg[0] = 1.0
    return p, tg, np.array([[x] for x in lows], float)


def test_stop_hit_intraday_exits_at_stop_level_and_stays_out():
    # buy at open d1 = 100; d2 low 85 reaches the 10% stop (90) -> exit at 90; d3 rallies, not held
    p, tg, low = _one([100, 100, 95, 100], [100, 98, 96, 120], [100, 97, 85, 99])
    res = engine.run(p, tg, NOCOST, FREE, stop=0.10, low=low)
    assert res.net[1] == pytest.approx(98 / 100 - 1)
    assert res.net[2] == pytest.approx((95 / 98) * (90 / 95) - 1, abs=5e-5)  # overnight gap, then stop fill
    assert res.net[3] == pytest.approx(0.0) and res.exposure[3] == 0.0


def test_gap_through_stop_fills_at_open():
    p, tg, low = _one([100, 100, 80, 80], [100, 99, 85, 85], [100, 99, 78, 80])
    res = engine.run(p, tg, NOCOST, FREE, stop=0.10, low=low)
    assert res.net[2] == pytest.approx(80 / 99 - 1, abs=5e-5)  # sold at the open of 80, below the 90 stop
    assert res.exposure[2] == 0.0


def test_no_stop_matches_plain_run():
    p, tg, low = _one([100, 100, 95, 100], [100, 98, 96, 120], [100, 97, 85, 99])
    a = engine.run(p, tg, NOCOST, FREE)
    b = engine.run(p, tg, NOCOST, FREE, stop=0.5, low=low)
    np.testing.assert_allclose(a.net, b.net)


def test_stop_exit_pays_cost():
    p, tg, low = _one([100, 100, 95, 100], [100, 98, 96, 120], [100, 97, 85, 99])
    c = engine.CostModel(10, 10)
    a = engine.run(p, tg, c, FREE, stop=0.10, low=low)
    assert a.cost[2] > 0


def test_stop_requires_low():
    p, tg, _ = _one([100, 100], [100, 100], [100, 100])
    with pytest.raises(ValueError):
        engine.run(p, tg, NOCOST, FREE, stop=0.1)


def test_vol_scale_caps_at_one_and_scales_down_volatile_rows():
    T = 100
    rng = np.random.default_rng(0)
    calm = 100 * np.cumprod(1 + rng.normal(0, 0.002, T))  # ~3% ann vol
    wild = 100 * np.cumprod(1 + rng.normal(0, 0.04, T))  # ~63% ann vol
    close = np.column_stack([calm, wild])
    tg = np.full((T, 2), np.nan)
    tg[80] = [1.0, 0.0]
    tg[90] = [0.0, 1.0]
    out = vol_scale(tg, close, 0.20)
    assert out[80].sum() == pytest.approx(1.0)
    assert 0.2 < out[90, 1] < 0.5
    assert np.all(np.isnan(out[85]))
    # point in time: rows <= t do not change when later data is altered
    close2 = close.copy()
    close2[91:] *= 3
    np.testing.assert_allclose(vol_scale(tg, close2, 0.20)[:91], out[:91])


def test_s09_every_k_and_worst_decile():
    from trader.research.fast import every_k

    assert every_k(7, 3).tolist() == [True, False, False, True, False, False, True]
    assert every_k(3, 1).all()
