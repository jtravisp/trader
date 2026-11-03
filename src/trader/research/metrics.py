"""Performance metrics on daily return series (rf = 0, 252 trading days)."""

from __future__ import annotations

import math

import numpy as np

A = 252


def cagr(r: np.ndarray) -> float:
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return 0.0
    growth = float(np.prod(1 + r))
    return growth ** (A / len(r)) - 1 if growth > 0 else -1.0


def ann_vol(r: np.ndarray) -> float:
    return float(np.nanstd(r, ddof=1) * math.sqrt(A))


def sharpe_ann(r: np.ndarray) -> float:
    sd = np.nanstd(r, ddof=1)
    return float(np.nanmean(r) / sd * math.sqrt(A)) if sd > 0 else 0.0


def sortino(r: np.ndarray) -> float:
    down = np.minimum(r[np.isfinite(r)], 0)
    dd = math.sqrt(float((down**2).mean())) if len(down) else 0.0
    return float(np.nanmean(r) / dd * math.sqrt(A)) if dd > 0 else 0.0


def max_drawdown(r: np.ndarray) -> float:
    eq = np.cumprod(1 + np.nan_to_num(r))
    peak = np.maximum.accumulate(eq)
    return float((eq / peak - 1).min())


def monthly(r: np.ndarray, dates: np.ndarray) -> np.ndarray:
    months = dates.astype("datetime64[M]")
    out = []
    for m in np.unique(months):
        out.append(float(np.prod(1 + np.nan_to_num(r[months == m])) - 1))
    return np.array(out)


def beta_alpha(r: np.ndarray, bench: np.ndarray) -> tuple[float, float]:
    """OLS beta and annualized alpha of r on bench."""
    m = np.isfinite(r) & np.isfinite(bench)
    x, y = bench[m], r[m]
    vx = float(np.var(x, ddof=1))
    beta = float(np.cov(x, y, ddof=1)[0, 1] / vx) if vx > 0 else 0.0
    alpha = float(y.mean() - beta * x.mean()) * A
    return beta, alpha


def summary(net: np.ndarray, gross: np.ndarray, turnover: np.ndarray, dates: np.ndarray, spy: np.ndarray | None) -> dict:
    years = len(net) / A
    out = {
        "cagr": cagr(net),
        "vol": ann_vol(net),
        "sharpe": sharpe_ann(net),
        "sortino": sortino(net),
        "max_dd": max_drawdown(net),
        "turnover_ann": float(turnover.sum() / years) if years else 0.0,
        "cost_drag": cagr(gross) - cagr(net),
        "pct_pos_months": float((monthly(net, dates) > 0).mean()),
        "n_days": int(len(net)),
    }
    if spy is not None:
        m = np.isfinite(spy)
        out["corr_spy"] = float(np.corrcoef(net[m], spy[m])[0, 1]) if net[m].std() > 0 else 0.0
        out["beta_spy"], out["alpha_spy"] = beta_alpha(net, spy)
        blend = 0.5 * net + 0.5 * spy
        _, out["blend5050_alpha"] = beta_alpha(blend, spy)
        out["blend5050_sharpe"] = sharpe_ann(blend)
    return out


def subperiods(net: np.ndarray, dates: np.ndarray, k: int = 3) -> list[dict]:
    out = []
    for idx in np.array_split(np.arange(len(net)), k):
        out.append({"start": str(dates[idx[0]]), "end": str(dates[idx[-1]]), "cagr": cagr(net[idx]),
                    "sharpe": sharpe_ann(net[idx]), "max_dd": max_drawdown(net[idx])})
    return out
