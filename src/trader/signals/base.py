"""Strategy interface and shared pure helpers (no I/O, no wall clock).

A strategy maps a Panel to a T x N matrix of target weights. Row t may use only rows <= t
(enforced by the engine's truncation leak check). All-NaN rows mean "no rebalance".
Rebalance schedules come from the NYSE calendar, which is known in advance.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import polars as pl

from trader import calendar
from trader.backtest.panel import Panel


class Strategy:
    id: str = ""
    family: str = ""
    stock_universe: bool = True  # picks from the (survivorship-biased until P3) stock seed

    def __init__(self, **params) -> None:
        self.params = params

    def targets(self, panel: Panel) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError


@lru_cache(maxsize=4)
def _session_flags() -> tuple[dict, dict]:
    f = calendar.sessions_frame().select("date").with_columns(
        pl.col("date").shift(-1).alias("nxt")
    ).drop_nulls()
    month_end = {d: (n.month != d.month) for d, n in f.iter_rows()}
    week_end = {d: (n.isocalendar()[1] != d.isocalendar()[1]) for d, n in f.iter_rows()}
    return month_end, week_end


def schedule(panel: Panel, freq: str) -> np.ndarray:
    """Bool mask of rebalance decision days: 'daily', 'weekly' (last session of ISO week),
    'monthly' (last session of month), 'once' (first row)."""
    if freq == "daily":
        return np.ones(panel.T, dtype=bool)
    if freq == "once":
        m = np.zeros(panel.T, dtype=bool)
        m[0] = True
        return m
    me, we = _session_flags()
    table = me if freq == "monthly" else we
    return np.array([table.get(d, False) for d in panel.dates.astype("O")])


def rolling_median(x: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    df = pl.DataFrame(x, schema=[f"c{i}" for i in range(x.shape[1])], orient="row")
    out = df.select(pl.all().rolling_median(window_size=window, min_samples=min_periods))
    return out.to_numpy().astype(float)


_UNI_CACHE: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}


def liquid_universe(panel: Panel, n: int = 500, window: int = 60, min_price: float = 5.0, stocks_only: bool = True) -> np.ndarray:
    """T x N bool: top-n names by trailing median dollar volume with raw close >= min_price.
    Cached per (panel array, args); the cache holds the array itself so a recycled id() can't
    produce a false hit."""
    key = (id(panel.dollar_volume), panel.T, n, window, min_price, stocks_only)
    hit = _UNI_CACHE.get(key)
    if hit is not None and hit[0] is panel.dollar_volume:
        return hit[1]
    if len(_UNI_CACHE) > 8:
        _UNI_CACHE.clear()
    res = _liquid_universe(panel, n, window, min_price, stocks_only)
    _UNI_CACHE[key] = (panel.dollar_volume, res)
    return res


def _liquid_universe(panel: Panel, n: int, window: int, min_price: float, stocks_only: bool) -> np.ndarray:
    if panel.pit_rank is not None:
        # point-in-time membership from Massive listings (P3): rank from the latest month-end
        # snapshot <= t, still requiring a tradable close today
        return (panel.pit_rank <= n) & np.isfinite(panel.close) & ~panel.is_etf[None, :]
    dv = rolling_median(np.where(np.isfinite(panel.dollar_volume), panel.dollar_volume, np.nan), window, int(window * 0.8))
    ok = np.isfinite(dv) & (np.nan_to_num(panel.raw_close) >= min_price) & np.isfinite(panel.close)
    if stocks_only:
        ok &= ~panel.is_etf[None, :]
    dv = np.where(ok, dv, -np.inf)
    rank = (-dv).argsort(axis=1).argsort(axis=1)
    return ok & (rank < n)


def top_k_equal_weight(score: np.ndarray, eligible: np.ndarray, k: int) -> np.ndarray:
    """Per row: equal weight on the k highest finite scores among eligible names."""
    s = np.where(eligible & np.isfinite(score), score, -np.inf)
    order = (-s).argsort(axis=1)
    w = np.zeros_like(score)
    rows = np.arange(score.shape[0])[:, None]
    top = order[:, :k]
    valid = np.take_along_axis(s, top, axis=1) > -np.inf
    cnt = valid.sum(axis=1, keepdims=True)
    w[rows, top] = np.where(valid, 1.0 / np.maximum(cnt, 1), 0.0)
    return w


def only_on(mask: np.ndarray, w: np.ndarray) -> np.ndarray:
    out = np.full_like(w, np.nan)
    out[mask] = w[mask]
    return out
