"""Position sizing (pure functions). The engine/gateway then apply the hard caps in limits.yaml."""

from __future__ import annotations

import numpy as np

from trader.backtest.panel import Panel


def vol_target(panel: Panel, w: np.ndarray, target_vol: float, lookback: int = 63, max_gross: float = 1.0) -> np.ndarray:
    """Scale each decided row of w so the ex-ante portfolio vol (trailing `lookback`-day sample
    covariance, annualized) equals target_vol, never levering above max_gross.
    Uses returns through row t only, so it is point-in-time. NaN rows pass through."""
    r = panel.returns()
    out = w.copy()
    for t in np.where(~np.all(np.isnan(w), axis=1))[0]:
        row = np.nan_to_num(w[t])
        held = row > 0
        if not held.any():
            continue
        if t < lookback:
            out[t] = np.nan  # not enough history to size: skip this decision
            continue
        R = r[t - lookback + 1 : t + 1][:, held]
        R = np.where(np.isfinite(R), R, 0.0)
        cov = np.cov(R, rowvar=False, ddof=1) * 252
        cov = np.atleast_2d(cov)
        vol = float(np.sqrt(max(row[held] @ cov @ row[held], 0.0)))
        if vol <= 0:
            continue
        scaled = row * (target_vol / vol)
        g = scaled.sum()
        if g > max_gross:
            scaled *= max_gross / g
        out[t] = scaled
    return out
