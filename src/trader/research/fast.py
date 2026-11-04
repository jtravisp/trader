"""Research-only fast strategies (declared in docs/STRATEGIES.md 2026-11-04). Kept outside
src/trader/signals so the frozen S03 paper version (pinned paths) is untouched. If one is adopted
it moves into signals/ under a new tag.

S09: daily short-term reversal - buy the worst performers of the last `lookback` sessions among the
liquid top 500, hold `hold` sessions, then rebalance."""

from __future__ import annotations

import numpy as np

from trader.backtest.panel import Panel
from trader.signals.base import Strategy, liquid_universe, only_on, top_k_equal_weight
from trader.signals.library import REGISTRY


def every_k(T: int, k: int) -> np.ndarray:
    """Pure: decide on rows 0, k, 2k, ... (a fixed session cadence, known in advance)."""
    m = np.zeros(T, dtype=bool)
    m[::k] = True
    return m


class DailyReversal(Strategy):
    id, family = "S09", "S09"

    def targets(self, panel: Panel) -> np.ndarray:
        lb, hold = int(self.params["lookback"]), int(self.params["hold"])
        n = int(self.params.get("n", 500))
        k = int(n * 0.10)
        c = panel.close
        ret = np.full_like(c, np.nan)
        ret[lb:] = c[lb:] / c[:-lb] - 1
        uni = liquid_universe(panel, n=n)
        return only_on(every_k(panel.T, hold), top_k_equal_weight(-ret, uni, k))


REGISTRY.setdefault("S09", DailyReversal)
