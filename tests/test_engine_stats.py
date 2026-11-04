import numpy as np
import pytest

from trader.backtest import engine
from trader.backtest.panel import Panel
from trader.research import stats
from trader.signals.base import Strategy


def make_panel(opens, closes, etf=None):
    o, c = np.array(opens, float), np.array(closes, float)
    T, N = o.shape
    return Panel(
        dates=np.arange(np.datetime64("2020-01-01"), np.datetime64("2020-01-01") + T),
        symbols=[f"S{i}" for i in range(N)], open=o, close=c, raw_close=c,
        dollar_volume=np.full((T, N), 1e9), is_etf=np.array(etf if etf is not None else [False] * N),
    )


NOCOST = engine.CostModel(0, 0)
FREE = engine.Constraints.none()


def test_fills_at_next_open_not_signal_close():
    # decided at close of day 0 (price 100); fills at open of day 1 (110); close day 1 = 121
    p = make_panel([[100], [110], [121]], [[100], [121], [121]])
    tg = np.full((3, 1), np.nan)
    tg[0] = 1.0
    res = engine.run(p, tg, NOCOST, FREE)
    assert res.net[1] == pytest.approx(121 / 110 - 1)  # overnight 100->110 is NOT earned
    assert res.net[2] == pytest.approx(0.0)


def test_delay_shifts_fill_by_one_day():
    p = make_panel([[100], [100], [110], [120]], [[100], [100], [110], [120]])
    tg = np.full((4, 1), np.nan)
    tg[0] = 1.0
    res = engine.run(p, tg, NOCOST, FREE, delay=1)
    assert res.net[1] == 0.0 and res.turnover[2] == pytest.approx(0.5)


def test_costs_per_side_and_sec_fee():
    p = make_panel([[100], [100], [100]], [[100], [100], [100]])
    tg = np.full((3, 1), np.nan)
    tg[0], tg[1] = 1.0, 0.0
    res = engine.run(p, tg, engine.CostModel(5, 2), FREE)
    assert res.cost[1] == pytest.approx(5e-4)  # buy 100% at 5 bps
    assert res.cost[2] == pytest.approx(5e-4 + engine.SEC_FEE_BPS_ON_SELLS / 1e4)  # sell + SEC fee


def test_caps_and_min_drift():
    w, clipped = engine.apply_constraints(np.array([0.5, 0.5]), np.array([False, True]), engine.Constraints(0.10, 0.35, 1.0, 0.02))
    assert clipped and w.tolist() == [0.10, 0.35]
    p = make_panel([[100, 100]] * 3, [[100, 100]] * 3)
    tg = np.full((3, 2), np.nan)
    tg[0] = [0.5, 0.5]
    tg[1] = [0.51, 0.49]  # 1% drift < 2% threshold: no trade
    res = engine.run(p, tg, NOCOST, engine.Constraints(1, 1, 1, 0.02))
    assert res.turnover[2] == 0.0


class _Clean(Strategy):
    def targets(self, panel):
        r = np.full_like(panel.close, np.nan)
        r[1:] = panel.close[1:] / panel.close[:-1]
        return np.where(np.isfinite(r), (r > 1).astype(float) / panel.N, np.nan)


class _Leaky(Strategy):
    def targets(self, panel):
        r = np.full_like(panel.close, np.nan)
        r[:-1] = panel.close[1:] / panel.close[:-1]
        return np.where(np.isfinite(r), (r > 1).astype(float) / panel.N, np.nan)


def test_leak_check():
    rng = np.random.default_rng(1)
    c = 100 * np.cumprod(1 + rng.normal(0, 0.01, (400, 5)), axis=0)
    p = make_panel(c, c)
    assert engine.truncation_leak_check(_Clean(), p, warmup=50)[0]
    ok, msg = engine.truncation_leak_check(_Leaky(), p, warmup=50)
    assert not ok and "LEAK" in msg


def test_psr_and_expected_max():
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.01, 5000)
    assert 0.0 < stats.psr(r) < 1.0
    assert stats.expected_max_sharpe(0.01, 100) > stats.expected_max_sharpe(0.01, 10) > 0
    assert stats.expected_max_sharpe(0.01, 1) == 0.0


def test_pbo_noise_vs_real_edge():
    # One noise sample's PBO is very variable (the CSCV splits are highly dependent); its
    # expectation is 0.5, so average over independent samples.
    pbos = [stats.pbo_cscv(np.random.default_rng(s).normal(0, 0.01, (1000, 10)), n_blocks=10)["pbo"] for s in range(12)]
    assert 0.35 < float(np.mean(pbos)) < 0.65
    noise = np.random.default_rng(0).normal(0, 0.01, (2000, 10))
    edge = noise.copy()
    edge[:, 0] += 0.002  # one variant genuinely better in every period
    assert stats.pbo_cscv(edge)["pbo"] < 0.05


def test_bootstrap_ci_contains_point():
    rng = np.random.default_rng(0)
    r = rng.normal(0.0005, 0.01, 1500)
    lo, hi = stats.stationary_bootstrap(r, stats.sharpe, n_boot=300)
    assert lo < stats.sharpe(r) < hi


def test_cpcv_purges_and_embargoes():
    for train, test in stats.cpcv_splits(600, n_groups=6, n_test=2, horizon=5, embargo=5):
        assert not set(train) & set(test)
        for t in test:  # no training sample within the label horizon before / embargo after a test sample
            assert not any((train >= t - 5) & (train <= t + 5))
    with pytest.raises(ValueError):
        next(stats.cpcv_splits(100, horizon=5, embargo=2))


def test_vol_target_scales_to_target_and_caps_gross():
    from trader.portfolio.sizing import vol_target

    rng = np.random.default_rng(3)
    daily = 0.20 / np.sqrt(252)  # 20% annual vol asset
    c = 100 * np.cumprod(1 + rng.normal(0, daily, (400, 1)), axis=0)
    p = make_panel(c, c)
    w = np.full((400, 1), np.nan)
    w[300] = 1.0
    out = vol_target(p, w, target_vol=0.10, lookback=63)
    realized = np.std(p.returns()[238:301, 0], ddof=1) * np.sqrt(252)
    assert out[300, 0] == pytest.approx(0.10 / realized, rel=1e-9)
    assert np.isnan(out[299, 0])
    assert vol_target(p, w, target_vol=0.50)[300, 0] == pytest.approx(1.0)  # capped at max_gross
    w[10] = 1.0
    assert np.isnan(vol_target(p, w, 0.10)[10, 0])  # not enough history: decision skipped


def test_delisting_return_applied_once_on_exit():
    nan = np.nan
    o = [[10, 10], [10, 10], [10, 10], [nan, 10], [nan, 10], [nan, 10]]
    c = [[10, 10], [10, 10], [10, 10], [nan, 10], [nan, 10], [nan, 10]]
    p = make_panel(o, c)
    tg = np.full((6, 2), np.nan)
    tg[0] = [0.5, 0.5]
    flat = engine.run(p, tg, NOCOST, FREE)
    assert np.allclose(flat.net, 0)  # default: exit at the last close
    p2 = Panel(p.dates, p.symbols, p.open, p.close, p.raw_close, p.dollar_volume, p.is_etf, {"delist_ret": {0: -0.5}})
    r = engine.run(p2, tg, NOCOST, FREE)
    assert np.isclose(r.net[3], -0.25) and np.allclose(r.net[4:], 0) and np.allclose(r.net[:3], 0)


def test_name_pnl_attributes_gross_returns():
    rng = np.random.default_rng(2)
    c = 100 * np.cumprod(1 + rng.normal(0, 0.001, (60, 3)), axis=0)
    o = c * (1 + rng.normal(0, 0.0005, (60, 3)))
    p = make_panel(o, c)
    tg = np.full((60, 3), np.nan)
    tg[0] = [0.5, 0.3, 0.2]
    r = engine.run(p, tg, NOCOST, FREE)
    assert np.isclose(r.name_pnl.sum(), r.gross.sum(), rtol=1e-3)
    assert abs(r.name_pnl[0]) > abs(r.name_pnl[2]) * 0.5  # larger weight, larger share (same vol)
