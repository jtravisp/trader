"""Research-only factor baselines (declared in docs/STRATEGIES.md 2026-11-05). Kept outside
src/trader/signals so the frozen S03 paper version (pinned paths) is untouched. If one is adopted
it moves into signals/ under a new tag.

S10 quality, S11 low volatility, S12 sector-ETF momentum. Each rebalances every `hold` sessions."""

from __future__ import annotations

import numpy as np

from trader.backtest.panel import Panel
from trader.features import fundamental, price
from trader.research.fast import every_k
from trader.signals.base import Strategy, liquid_universe, only_on, top_k_equal_weight
from trader.signals.library import REGISTRY

SECTORS = ("XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY", "XLC", "XLRE")


def _mean_rank(parts: list[np.ndarray], mask: np.ndarray) -> np.ndarray:
    """Pure: average of per-day cross-sectional percentile ranks; a name needs every part."""
    ranks = [price.cs_rank(p, mask) for p in parts]
    out = np.mean(ranks, axis=0)
    ok = np.all([np.isfinite(r) for r in ranks], axis=0)
    return np.where(ok, out, np.nan)


class Quality(Strategy):
    """S10: top decile of the top 500 by a quality composite. composite 'roa' = ROA rank;
    'roa_acc_lev' = mean rank of ROA, -accruals and -leverage."""

    id, family = "S10", "S10"
    needs_fields = True

    def targets(self, panel: Panel) -> np.ndarray:
        hold, which = int(self.params["hold"]), self.params["composite"]
        n = int(self.params.get("n", 500))
        f = fundamental.build(panel)
        uni = liquid_universe(panel, n=n)
        parts = [f["roa"]] if which == "roa" else [f["roa"], -f["accruals"], -f["leverage"]]
        score = _mean_rank(parts, uni)
        return only_on(every_k(panel.T, hold), top_k_equal_weight(score, uni, n // 10))


class LowVol(Strategy):
    """S11: the lowest-volatility decile of the top 500 (trailing `window`-session daily return std)."""

    id, family = "S11", "S11"

    def targets(self, panel: Panel) -> np.ndarray:
        hold, w = int(self.params["hold"]), int(self.params["window"])
        n = int(self.params.get("n", 500))
        uni = liquid_universe(panel, n=n)
        vol = price.volatility(panel, w)
        return only_on(every_k(panel.T, hold), top_k_equal_weight(-vol, uni, n // 10))


class SectorMomentum(Strategy):
    """S12: the top 3 of the 11 sector SPDRs by return from t-lookback to t-21, equal weight."""

    id, family = "S12", "S12"
    stock_universe = False

    def targets(self, panel: Panel) -> np.ndarray:
        hold, lb = int(self.params["hold"]), int(self.params["lookback"])
        mom = price.ret(panel, lb, skip=21)
        elig = np.zeros((panel.T, panel.N), dtype=bool)
        for s in SECTORS:
            if s in panel.symbols:
                elig[:, panel.col(s)] = True
        elig &= np.isfinite(panel.close)
        return only_on(every_k(panel.T, hold), top_k_equal_weight(mom, elig, 3))


for _cls in (Quality, LowVol, SectorMomentum):
    REGISTRY.setdefault(_cls.id, _cls)
