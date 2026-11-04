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


def test_fields_asof_alignment_and_split_robust_mcap():
    from datetime import UTC, date, datetime

    import polars as pl

    from trader.backtest.panel import Panel
    from trader.features import fundamental
    from trader.research import fields

    dates = np.array(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"], dtype="datetime64[D]")
    raw = np.array([[100.0], [100.0], [50.0], [50.0], [55.0]])  # 2:1 split before 01-04
    adj = raw.copy()
    adj[:2] /= 2
    p = Panel(dates, ["X"], adj, adj, raw, raw * 1e6, np.array([False]), {"lines": {"5-0": "X"}})
    pf = pl.DataFrame({
        "cik": [5], "accession": ["a"], "period_end": [date(2023, 12, 31)],
        # accepted 2024-01-02 22:00 UTC (after the 21:00 UTC close) -> first usable at the 01-03 close
        "known_ts": [datetime(2024, 1, 2, 22, 0, tzinfo=UTC)], "shares": [1e6], "net_income_ttm": [1e7],
        **{c: [1.0] for c in fields.FUND_COLS if c != "net_income_ttm"},
    })
    m = fields.fundamentals(p, pf)
    assert np.isnan(m["net_income_ttm"][0, 0]) and m["net_income_ttm"][1, 0] == 1e7 and m["net_income_ttm"][4, 0] == 1e7
    p2 = Panel(p.dates, p.symbols, p.open, p.close, p.raw_close, p.dollar_volume, p.is_etf, p.meta, fields=m)
    mcap = np.exp(fundamental.build(p2)["log_mcap"][:, 0])
    # at filing (01-03): 1e6 shares x $100 = 1e8; after the 2:1 split and +10%: 1.1e8
    assert np.isclose(mcap[1], 1e8) and np.isclose(mcap[4], 1.1e8)
