"""Strategy implementations. Each must be declared in docs/STRATEGIES.md before it is run."""

from __future__ import annotations

import numpy as np

from trader.backtest.panel import Panel
from trader.signals.base import Strategy, liquid_universe, only_on, schedule, top_k_equal_weight


class BuyHold(Strategy):
    """SAN01: 100% in one symbol from the first decision day."""

    id, family = "SAN01", "SAN01"
    stock_universe = False

    def targets(self, panel: Panel) -> np.ndarray:
        w = np.zeros((panel.T, panel.N))
        w[:, panel.col(self.params.get("symbol", "SPY"))] = 1.0
        return only_on(schedule(panel, "once"), w)


class EqualWeightUniverse(Strategy):
    """SAN02-EW: equal weight across the liquid universe, weekly (benchmark for SAN02)."""

    id, family = "SAN02-EW", "SAN02"

    def targets(self, panel: Panel) -> np.ndarray:
        uni = liquid_universe(panel, n=int(self.params.get("n", 500)))
        w = uni / np.maximum(uni.sum(axis=1, keepdims=True), 1)
        return only_on(schedule(panel, "weekly"), w.astype(float))


class RandomPicks(Strategy):
    """SAN02: k random names from the liquid universe each week. The draw depends only on
    (seed, decision date), so it is truncation-invariant."""

    id, family = "SAN02", "SAN02"

    def targets(self, panel: Panel) -> np.ndarray:
        k, seed = int(self.params.get("k", 50)), int(self.params.get("seed", 0))
        uni = liquid_universe(panel, n=int(self.params.get("n", 500)))
        mask = schedule(panel, "weekly")
        score = np.full((panel.T, panel.N), np.nan)
        for t in np.where(mask)[0]:
            rng = np.random.default_rng([seed, int(panel.dates[t].astype(int))])
            score[t] = rng.random(panel.N)
        return only_on(mask, top_k_equal_weight(score, uni, k))


class LeakyNextDay(Strategy):
    """SAN03: deliberately leaky - picks tomorrow's top k performers. Must be caught by the leak check."""

    id, family = "SAN03", "SAN03"

    def targets(self, panel: Panel) -> np.ndarray:
        k = int(self.params.get("k", 50))
        fwd = np.full((panel.T, panel.N), np.nan)
        fwd[:-1] = panel.close[1:] / panel.close[:-1] - 1  # the leak
        uni = liquid_universe(panel)
        return only_on(schedule(panel, "weekly"), top_k_equal_weight(fwd, uni, k))


class Momentum(Strategy):
    """S03: cross-sectional momentum. Score = return from t-lookback to t-skip; monthly; top
    decile of the liquid top-500, equal weight."""

    id, family = "S03", "S03"

    def targets(self, panel: Panel) -> np.ndarray:
        lb, skip = int(self.params.get("lookback", 252)), int(self.params.get("skip", 21))
        n = int(self.params.get("n", 500))
        k = int(self.params.get("k", n // 10))
        c = panel.close
        score = np.full_like(c, np.nan)
        score[lb:] = c[lb - skip : c.shape[0] - skip] / c[: c.shape[0] - lb] - 1
        uni = liquid_universe(panel, n=n)
        return only_on(schedule(panel, "monthly"), top_k_equal_weight(score, uni, k))


class ShortTermReversal(Strategy):
    """S02: weekly, buy the worst `lookback`-session performers of the liquid top-500 (see STRATEGIES.md)."""

    id, family = "S02", "S02"

    def targets(self, panel: Panel) -> np.ndarray:
        lb = int(self.params.get("lookback", 5))
        n = int(self.params.get("n", 500))
        frac = {"decile": 0.10, "quintile": 0.20}[self.params.get("bucket", "decile")]
        c = panel.close
        ret = np.full_like(c, np.nan)
        ret[lb:] = c[lb:] / c[:-lb] - 1
        uni = liquid_universe(panel, n=n)
        return only_on(schedule(panel, "weekly"), top_k_equal_weight(-ret, uni, int(n * frac)))


class EtfTrend(Strategy):
    """S01: time-series momentum on the ETF list with portfolio vol targeting (see STRATEGIES.md)."""

    id, family = "S01", "S01"
    stock_universe = False

    def targets(self, panel: Panel) -> np.ndarray:
        from trader.portfolio.sizing import vol_target

        lb = int(self.params.get("lookback", 126))
        tv = float(self.params.get("vol_target", 0.10))
        c = panel.close
        mom = np.full_like(c, np.nan)
        mom[lb:] = c[lb:] / c[:-lb] - 1
        on = (mom > 0) & panel.is_etf[None, :] & np.isfinite(c)
        w = on / np.maximum(on.sum(axis=1, keepdims=True), 1)
        w = only_on(schedule(panel, "weekly"), w.astype(float))
        return vol_target(panel, w, tv, lookback=63)


class EarningsDrift(Strategy):
    """S04: buy names whose SPY-relative reaction to an earnings 8-K (Item 2.02) is >= threshold
    sigma; hold `hold` sessions; weight 1/max(n_active, 10). See STRATEGIES.md."""

    id, family = "S04", "S04"

    def targets(self, panel: Panel) -> np.ndarray:
        if panel.events is None:
            raise ValueError("S04 needs the point-in-time panel (events matrix)")
        th, hold = float(self.params.get("threshold", 1.0)), int(self.params.get("hold", 5))
        n, floor, win = int(self.params.get("n", 1000)), int(self.params.get("min_slots", 10)), 60
        r = panel.returns()
        rel = r - r[:, [panel.col("SPY")]]
        # trailing sigma through t-1 (the reaction day itself is excluded)
        sd = np.full_like(rel, np.nan)
        x = np.where(np.isfinite(rel), rel, 0.0)
        ok = np.isfinite(rel).astype(float)
        cs, cs2, cn = (np.vstack([np.zeros((1, rel.shape[1])), np.cumsum(a, axis=0)]) for a in (x, x * x, ok))
        T = rel.shape[0]
        for t in range(win + 1, T):
            m = cn[t] - cn[t - win]
            mu = (cs[t] - cs[t - win]) / np.maximum(m, 1)
            var = (cs2[t] - cs2[t - win]) / np.maximum(m, 1) - mu * mu
            sd[t] = np.where(m >= 40, np.sqrt(np.maximum(var, 0) * m / np.maximum(m - 1, 1)), np.nan)
        uni = liquid_universe(panel, n=n)
        with np.errstate(invalid="ignore", divide="ignore"):
            z = rel / sd
        sig = panel.events & uni & np.isfinite(z) & (z >= th)
        active = np.zeros(sig.shape, dtype=bool)
        for k in range(hold):
            active[k:] |= sig[: T - k]
        active &= np.isfinite(panel.close)
        cnt = active.sum(axis=1, keepdims=True)
        return np.where(active, 1.0 / np.maximum(cnt, floor), 0.0)


from trader.signals.ml import LgbmRanker  # noqa: E402

REGISTRY: dict[str, type[Strategy]] = {
    "S04": EarningsDrift,
    "S05": LgbmRanker,
    "S01": EtfTrend,
    "S02": ShortTermReversal,
    "SAN01": BuyHold,
    "SAN02": RandomPicks,
    "SAN02-EW": EqualWeightUniverse,
    "SAN03": LeakyNextDay,
    "S03": Momentum,
}
