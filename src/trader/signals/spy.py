"""SPY overlays (declared in docs/STRATEGIES.md 2026-11-20, before any run): S14 trend filter and S15
volatility management. Both hold only SPY and SHY (1-3y Treasuries, the "off" asset), so they compete
directly with what the live account already holds (FALLBACK, D34).

Rebalance cadence is calendar-anchored (`schedule`: daily / weekly / monthly), so a decision on day t is
the same in a backtest, in the live runner's 300-session memory panel and in a shadow forward test.
"""

from __future__ import annotations

import numpy as np

from trader.backtest.panel import Panel
from trader.signals.base import Strategy, only_on, schedule

OFF = "SHY"


def _two_asset(panel: Panel, w_spy: np.ndarray) -> np.ndarray:
    """Pure: T x N weights with w_spy in SPY and the rest in SHY; NaN rows (warm-up) hold nothing."""
    w = np.zeros((panel.T, panel.N))
    ok = np.isfinite(w_spy)
    w[:, panel.col("SPY")] = np.where(ok, w_spy, 0.0)
    w[:, panel.col(OFF)] = np.where(ok, 1.0 - w_spy, 0.0)
    w[~ok] = np.nan
    return w


class SpyTrend(Strategy):
    """S14: 100% SPY while SPY's close is above its `sma`-session moving average, else 100% SHY."""

    id, family = "S14", "S14"
    stock_universe = False

    def targets(self, panel: Panel) -> np.ndarray:
        n, freq = int(self.params["sma"]), self.params["rebalance"]
        c = panel.close[:, panel.col("SPY")]
        sma = np.full_like(c, np.nan)
        cs = np.cumsum(np.nan_to_num(c))
        sma[n - 1 :] = (cs[n - 1 :] - np.concatenate([[0.0], cs[: -n]])) / n
        on = np.where(np.isfinite(sma), (c > sma).astype(float), np.nan)
        return only_on(schedule(panel, freq), _two_asset(panel, on))


class SpyVolManaged(Strategy):
    """S15: SPY weight = min(1, target / trailing realized vol), the rest in SHY (Moreira & Muir 2017)."""

    id, family = "S15", "S15"
    stock_universe = False

    def targets(self, panel: Panel) -> np.ndarray:
        tv, win, freq = float(self.params["vol_target"]), int(self.params["window"]), self.params["rebalance"]
        c = panel.close[:, panel.col("SPY")]
        r = np.full_like(c, np.nan)
        r[1:] = c[1:] / c[:-1] - 1
        vol = np.full_like(c, np.nan)
        for t in range(win, len(c)):
            x = r[t - win + 1 : t + 1]
            x = x[np.isfinite(x)]
            if len(x) >= int(win * 0.8):
                vol[t] = x.std(ddof=1) * np.sqrt(252)
        with np.errstate(divide="ignore", invalid="ignore"):
            w = np.where(np.isfinite(vol) & (vol > 0), np.minimum(1.0, tv / vol), np.nan)
        return only_on(schedule(panel, freq), _two_asset(panel, w))
