import numpy as np

from trader.backtest import engine
from trader.backtest.panel import Panel
from trader.signals.library import EarningsDrift


def _panel(T=200, N=6, seed=0):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.01, (T, N))
    c = 100 * np.cumprod(1 + r, axis=0)
    ev = np.zeros((T, N), dtype=bool)
    # name 1: +8 sigma jump on day 100 with an earnings event; name 2: same jump, no event
    c[100:, 1] *= 1.08
    c[100:, 2] *= 1.08
    ev[100, 1] = True
    ev[150, 3] = True  # event with an ordinary (small) reaction
    syms = ["SPY"] + [f"S{i}" for i in range(1, N)]
    dates = np.arange(np.datetime64("2020-01-01"), np.datetime64("2020-01-01") + np.timedelta64(T, "D")).astype("datetime64[D]")
    rank = np.ones((T, N))
    return Panel(dates, syms, c, c, c, np.full((T, N), 1e8), np.array([True] + [False] * (N - 1)), {}, rank, ev)


def test_s04_enters_after_event_holds_and_is_leak_free():
    p = _panel()
    w = EarningsDrift(threshold=2.0, hold=5).targets(p)
    assert w[99, 1] == 0 and w[100, 1] == 0.1 and w[104, 1] == 0.1 and w[105, 1] == 0
    assert w[:, 2].sum() == 0  # no event, no trade
    assert w[150:156, 3].sum() == 0 or abs(np.log(p.close[150, 3] / p.close[149, 3])) > 0.02
    ok, msg = engine.truncation_leak_check(EarningsDrift(threshold=2.0, hold=5), p, cuts=5, warmup=80)
    assert ok, msg


def test_s04_weights_split_when_more_than_ten_slots():
    p = _panel(N=14)
    p.events[100, 1:13] = True
    p.close[100:, 1:13] *= 1.10
    w = EarningsDrift(threshold=1.0, hold=5).targets(p)
    assert np.isclose(w[100].sum(), 1.0) and np.isclose(w[100, 1], 1 / 12)
