from datetime import date

import numpy as np
import pytest

from trader import calendar
from trader.backtest import engine
from trader.backtest.panel import Panel
from trader.signals import ml


def _panel(N=60, seed=3):
    rng = np.random.default_rng(seed)
    dates = np.array(calendar.sessions(date(2016, 1, 4), date(2020, 7, 1)), dtype="datetime64[D]")
    T = len(dates)
    s = rng.normal(size=(T, N))
    drift = np.zeros((T, N))
    for k in range(2, 7):  # signal at t moves open t+1 .. open t+6
        drift[k:] += 0.003 * s[:-k]
    r = rng.normal(0, 0.01, (T, N)) + drift
    r[:, 0] = rng.normal(0, 0.008, T)  # SPY: no signal
    c = 50 * np.cumprod(1 + r, axis=0)
    o = c.copy()
    dv = 1e7 * np.exp(0.5 * s)
    dv[:, 0] = 1e9
    syms = ["SPY"] + [f"S{i}" for i in range(1, N)]
    return Panel(dates, syms, o, c, c, dv, np.array([True] + [False] * (N - 1)), {}, np.ones((T, N)))


@pytest.mark.slow
def test_s05_learns_planted_signal_without_leaks():
    p = _panel()
    strat = ml.LgbmRanker(features="price", num_leaves=15, n_estimators=100, n=59, k=6)
    w = strat.targets(p)
    lab = ml.label(p)
    rows = [t for t in range(p.T) if np.isfinite(w[t]).all() and w[t].sum() > 0 and np.isfinite(lab[t, 1:]).all()]
    assert len(rows) > 50  # invested weekly from 2019
    picked = np.mean([lab[t][w[t] > 0].mean() for t in rows])
    avg = np.mean([np.nanmean(lab[t, 1:]) for t in rows])
    assert picked > avg + 0.01, (picked, avg)
    assert 2019 in strat.importance and max(strat.importance[2019], key=strat.importance[2019].get) == "vol_surprise"
    ok, msg = engine.truncation_leak_check(ml.LgbmRanker(features="price", num_leaves=15, n_estimators=100, n=59, k=6),
                                           p, cuts=2, warmup=800)
    assert ok, msg
