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
    name_pnl: np.ndarray | None = None  # N: sum of daily gross contributions (weight x return), by name


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


def run(panel: Panel, targets: np.ndarray, costs: CostModel, cons: Constraints, delay: int = 0,
        stop: float | None = None, low: np.ndarray | None = None) -> Result:
    """targets: T x N; a row of all-NaN means "no rebalance decided at this close".
    stop (S08): a held name whose session low reaches entry*(1-stop) exits at min(open, that level)
    and stays out until the next decided target; entry = the open it was bought at."""
    if stop is not None and low is None:
        raise ValueError("stop-loss simulation needs the low matrix (panel.fields['low'])")
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
    # Delisting returns (D24): a name whose final bar is row L < T-1 gets panel.meta["delist_ret"][j]
    # on the overnight leg of row L+1 (default 0 = exit at the last close).
    fin = np.isfinite(c)
    last = np.where(fin.any(axis=0), T - 1 - np.argmax(fin[::-1], axis=0), -1)
    dr = np.zeros(panel.N)
    for j, v in (panel.meta.get("delist_ret") or {}).items():
        dr[j] = v
    name_pnl = np.zeros(panel.N)
    entry = np.full(N, np.nan)
    for d in range(1, T):
        # overnight: close d-1 -> open d, on drifted weights
        g = o[d] / c[d - 1] - 1.0
        missing = ~np.isfinite(g)
        if (missing & (w > 0)).any():
            stale += int((missing & (w > 0)).sum())  # held name stopped trading
        g = np.where(missing, np.where(last == d - 1, dr, 0.0), g)
        pg = float(w @ g)
        name_pnl += w * g
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
            # min-drift skips small RESIZES of an existing position only; entries and exits always trade
            small = (np.abs(trade) < cons.min_drift) & (tgt > 0) & (w_open > 0)
            trade = np.where(small, 0.0, trade)
            cost = float(np.abs(trade) @ side) + float(np.clip(-trade, 0, None).sum()) * SEC_FEE_BPS_ON_SELLS / 1e4
            w_open = w_open + trade
            turn[d] = float(np.abs(trade).sum()) / 2.0
            cst[d] = cost
            day_net *= 1 - cost
            if stop is not None:
                entry = np.where((w_open - trade <= 1e-12) & (w_open > 1e-12), o[d], entry)

        # intraday: open d -> close d, on post-trade weights
        r = c[d] / o[d] - 1.0
        r = np.where(np.isfinite(r), r, 0.0)
        hit = np.zeros(N, dtype=bool)
        if stop is not None:
            lvl = entry * (1 - stop)
            held = (w_open > 1e-12) & np.isfinite(lvl) & np.isfinite(o[d])
            at_open = held & (o[d] <= lvl)
            intra = held & ~at_open & np.isfinite(low[d]) & (low[d] <= lvl)
            r = np.where(at_open, 0.0, np.where(intra, lvl / o[d] - 1.0, r))
            hit = at_open | intra
        pr = float(w_open @ r)
        name_pnl += w_open * r * (1 + pg)
        w = w_open * (1 + r) / (1 + pr) if pr > -1 else w_open
        day_gross *= 1 + pr
        day_net *= 1 + pr
        if hit.any():  # stopped out: sell into cash at the stop fill, pay the exit cost
            out = w * hit
            xc = float(out @ side) + float(out.sum()) * SEC_FEE_BPS_ON_SELLS / 1e4
            day_net *= 1 - xc
            cst[d] += xc
            turn[d] += float(out.sum()) / 2.0
            w = np.where(hit, 0.0, w)
            entry = np.where(hit, np.nan, entry)
        net[d] = day_net - 1
        gross[d] = day_gross - 1
        expo[d] = float(w.sum())
        nn[d] = int((w > 1e-9).sum())
    return Result(panel.dates, net, gross, turn, cst, expo, nn, clipped_days, stale, name_pnl)


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
