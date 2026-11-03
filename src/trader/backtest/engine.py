"""Daily backtest engine.

Timing: a strategy's target row t is decided with data through the CLOSE of day t and filled
at the OPEN of day t+1+delay. Overnight returns accrue to the pre-trade (drifted) weights,
intraday returns to the post-trade weights. Nothing fills at a price we couldn't have traded.

Gateway parity (config/limits.yaml): per-name caps (stocks/ETFs), gross <= max_gross_exposure,
long only, and trades smaller than min_rebalance_drift are skipped unless closing a position.
Cash earns 0 (Alpaca cash is not swept), so Sharpe uses rf = 0 for strategy and SPY alike.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from trader.backtest.panel import Panel
from trader.config import limits, settings

SEC_FEE_BPS_ON_SELLS = 0.28  # SEC Section 31 fee (~$27.80 per $1M sold); FINRA TAF negligible at our size


@dataclass(frozen=True)
class CostModel:
    stock_bps: float
    etf_bps: float
    multiplier: float = 1.0

    @classmethod
    def from_settings(cls, multiplier: float = 1.0) -> CostModel:
        s = settings()
        return cls(s.stock_bps, s.etf_bps, multiplier)

    def per_side(self, is_etf: np.ndarray) -> np.ndarray:
        return np.where(is_etf, self.etf_bps, self.stock_bps) * self.multiplier / 1e4


@dataclass(frozen=True)
class Constraints:
    max_weight_stock: float
    max_weight_etf: float
    max_gross: float
    min_drift: float

    @classmethod
    def from_limits(cls) -> Constraints:
        lim = limits()
        return cls(
            lim["position"]["max_weight_per_name"],
            lim["position"]["max_weight_per_etf"],
            lim["account"]["max_gross_exposure"],
            lim["orders"]["min_rebalance_drift"],
        )

    @classmethod
    def none(cls) -> Constraints:
        return cls(1.0, 1.0, 1.0, 0.0)


@dataclass
class Result:
    dates: np.ndarray
    net: np.ndarray  # daily net returns
    gross: np.ndarray  # daily returns before costs
    turnover: np.ndarray  # one-way traded fraction of equity per day
    cost: np.ndarray  # cost as fraction of equity per day
    exposure: np.ndarray  # gross exposure at close
    n_names: np.ndarray
    clipped_days: int
    stale_exits: int


def apply_constraints(w: np.ndarray, is_etf: np.ndarray, c: Constraints) -> tuple[np.ndarray, bool]:
    w = np.where(np.isfinite(w), np.maximum(w, 0.0), 0.0)  # long only
    cap = np.where(is_etf, c.max_weight_etf, c.max_weight_stock)
    clipped = bool((w > cap + 1e-12).any())
    w = np.minimum(w, cap)
    g = w.sum()
    if g > c.max_gross + 1e-12:
        w = w * (c.max_gross / g)
        clipped = True
    return w, clipped


def run(panel: Panel, targets: np.ndarray, costs: CostModel, cons: Constraints, delay: int = 0) -> Result:
    """targets: T x N; a row of all-NaN means "no rebalance decided at this close"."""
    T, N = panel.T, panel.N
    assert targets.shape == (T, N)
    o, c = panel.open, panel.close
    side = costs.per_side(panel.is_etf)
    decided = ~np.all(np.isnan(targets), axis=1)

    w = np.zeros(N)
    net = np.zeros(T)
    gross = np.zeros(T)
    turn = np.zeros(T)
    cst = np.zeros(T)
    expo = np.zeros(T)
    nn = np.zeros(T, dtype=int)
    clipped_days = 0
    stale = 0
    for d in range(1, T):
        # overnight: close d-1 -> open d, on drifted weights
        g = o[d] / c[d - 1] - 1.0
        missing = ~np.isfinite(g)
        if (missing & (w > 0)).any():
            stale += int((missing & (w > 0)).sum())  # held name stopped trading: exit flat at last close
        g = np.where(missing, 0.0, g)
        pg = float(w @ g)
        w_open = w * (1 + g) / (1 + pg) if pg > -1 else w
        day_gross = 1 + pg
        day_net = 1 + pg

        # trade at the open of d if a target was decided at close d-1-delay
        src = d - 1 - delay
        if src >= 0 and decided[src]:
            tgt, clipped = apply_constraints(targets[src], panel.is_etf, cons)
            clipped_days += clipped
            # No open print today: a halted name can't be traded (keep it); a name gone since
            # yesterday too is written off flat at its last close.
            tradable = np.isfinite(o[d])
            gone = ~tradable & ~np.isfinite(c[d - 1])
            tgt = np.where(tradable, tgt, np.where(gone, 0.0, w_open))
            trade = tgt - w_open
            small = (np.abs(trade) < cons.min_drift) & (tgt > 0)
            trade = np.where(small, 0.0, trade)
            cost = float(np.abs(trade) @ side) + float(np.clip(-trade, 0, None).sum()) * SEC_FEE_BPS_ON_SELLS / 1e4
            w_open = w_open + trade
            turn[d] = float(np.abs(trade).sum()) / 2.0
            cst[d] = cost
            day_net *= 1 - cost

        # intraday: open d -> close d, on post-trade weights
        r = c[d] / o[d] - 1.0
        r = np.where(np.isfinite(r), r, 0.0)
        pr = float(w_open @ r)
        w = w_open * (1 + r) / (1 + pr) if pr > -1 else w_open
        day_gross *= 1 + pr
        day_net *= 1 + pr
        net[d] = day_net - 1
        gross[d] = day_gross - 1
        expo[d] = float(w.sum())
        nn[d] = int((w > 1e-9).sum())
    return Result(panel.dates, net, gross, turn, cst, expo, nn, clipped_days, stale)


def truncation_leak_check(strategy, panel: Panel, cuts: int = 3, seed: int = 0, warmup: int = 300) -> tuple[bool, str]:
    """Recompute targets on data truncated at random days; rows <= cut must be identical.
    A difference means some target used data after its decision close (look-ahead)."""
    full = strategy.targets(panel)
    rng = np.random.default_rng(seed)
    lo = min(warmup, panel.T - 2)
    for t in sorted(rng.integers(lo, panel.T - 1, size=cuts)):
        part = strategy.targets(panel.truncate(int(t)))
        a, b = full[: t + 1], part
        same = np.array_equal(np.isnan(a), np.isnan(b)) and np.allclose(np.nan_to_num(a), np.nan_to_num(b), atol=1e-12)
        if not same:
            bad = np.where(~np.isclose(np.nan_to_num(a), np.nan_to_num(b), atol=1e-12) | (np.isnan(a) != np.isnan(b)))[0]
            return False, f"LEAK: targets at day index {int(bad.min())} change when data after {int(t)} is removed"
    return True, f"ok ({cuts} cuts)"
