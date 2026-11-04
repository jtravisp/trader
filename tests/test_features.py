import numpy as np

from trader.backtest.panel import Panel
from trader.features import price


def _panel(T=320, N=5, seed=1):
    rng = np.random.default_rng(seed)
    c = 50 * np.cumprod(1 + rng.normal(0.0003, 0.012, (T, N)), axis=0)
    o = c * (1 + rng.normal(0, 0.003, (T, N)))
    c[:30, 3] = np.nan  # late listing
    o[:30, 3] = np.nan
    ev = np.zeros((T, N), dtype=bool)
    ev[[100, 163, 226], 1] = True
    dates = (np.datetime64("2020-01-01") + np.arange(T)).astype("datetime64[D]")
    syms = ["SPY"] + [f"S{i}" for i in range(1, N)]
    dv = c * rng.uniform(1e5, 1e6, (T, N))
    return Panel(dates, syms, o, c, c, dv, np.array([True] + [False] * (N - 1)), {}, np.ones((T, N)), ev)


def test_features_are_point_in_time():
    p = _panel()
    full = price.build(p)
    for cut in (150, 260, 301):
        part = price.build(p.truncate(cut))
        for k, v in full.items():
            a, b = v[: cut + 1], part[k]
            assert np.array_equal(np.isnan(a), np.isnan(b)), k
            assert np.allclose(np.nan_to_num(a), np.nan_to_num(b), atol=1e-10), k


def test_feature_values():
    p = _panel()
    r5 = price.ret(p, 5)
    assert np.isclose(r5[50, 2], p.close[50, 2] / p.close[45, 2] - 1)
    m = price.ret(p, 252, 21)
    assert np.isclose(m[300, 2], p.close[279, 2] / p.close[48, 2] - 1)
    v = price.volatility(p, 20)
    assert np.isclose(v[100, 2], np.std(p.returns()[81:101, 2], ddof=1))
    d = price.days_since_event(p)
    assert np.isnan(d[99, 1]) and d[100, 1] == 0 and d[110, 1] == 10 and d[163, 1] == 0
    b = price.beta_to_spy(p)
    assert np.isclose(b[200, 0], 1.0)
    r = price.cs_rank(np.array([[3.0, np.nan, 1.0, 2.0]]))
    assert np.allclose(r, [[1.0, np.nan, 1 / 3, 2 / 3]], equal_nan=True)
