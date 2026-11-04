"""Price, volume, event and SPY-regime features for the S05 ranker. All T x N, row t = close of t."""

from __future__ import annotations

import warnings

import numpy as np

from trader.backtest.panel import Panel


def _roll(x: np.ndarray, w: int, fn: str, min_periods: int | None = None) -> np.ndarray:
    """Trailing window over rows t-w+1..t, NaN-aware, via cumulative sums (mean/std) or loop (max)."""
    mp = min_periods or w
    T = x.shape[0]
    out = np.full(x.shape, np.nan)
    if fn in ("mean", "std"):
        ok = np.isfinite(x)
        v = np.where(ok, x, 0.0)
        z = np.zeros((1, x.shape[1]))
        cs, cs2, cn = (np.vstack([z, np.cumsum(a, axis=0)]) for a in (v, v * v, ok.astype(float)))
        n = cn[w:] - cn[:-w] if T >= w else None
        if n is None:
            return out
        s1 = cs[w:] - cs[:-w]
        with np.errstate(invalid="ignore", divide="ignore"):
            mu = s1 / n
            if fn == "mean":
                res = mu
            else:
                var = (cs2[w:] - cs2[:-w]) / n - mu * mu
                res = np.sqrt(np.maximum(var, 0) * n / np.maximum(n - 1, 1))
        out[w - 1:] = np.where(n >= mp, res, np.nan)
        return out
    if fn == "max":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN windows
            for t in range(w - 1, T):
                win = x[t - w + 1 : t + 1]
                out[t] = np.where(np.isfinite(win).sum(axis=0) >= mp, np.nanmax(win, axis=0), np.nan)
        return out
    raise ValueError(fn)


def ret(panel: Panel, k: int, skip: int = 0) -> np.ndarray:
    """Return from close t-k to close t-skip."""
    c = panel.close
    out = np.full(c.shape, np.nan)
    if c.shape[0] > k:
        with np.errstate(invalid="ignore", divide="ignore"):
            out[k:] = c[k - skip : c.shape[0] - skip] / c[: c.shape[0] - k] - 1
    return out


def volatility(panel: Panel, w: int = 20) -> np.ndarray:
    return _roll(panel.returns(), w, "std", int(w * 0.8))


def overnight_intraday(panel: Panel, w: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """Trailing mean overnight (close t-1 -> open t) and intraday (open t -> close t) returns."""
    o, c = panel.open, panel.close
    on = np.full(c.shape, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        on[1:] = o[1:] / c[:-1] - 1
        intra = c / o - 1
    return _roll(on, w, "mean", int(w * 0.8)), _roll(intra, w, "mean", int(w * 0.8))


def gap(panel: Panel) -> np.ndarray:
    """Today's open vs yesterday's close."""
    out = np.full(panel.close.shape, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[1:] = panel.open[1:] / panel.close[:-1] - 1
    return out


def dist_from_high(panel: Panel, w: int = 252) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return panel.close / _roll(panel.close, w, "max", int(w * 0.8)) - 1


def volume_surprise(panel: Panel, w: int = 60) -> np.ndarray:
    """log(dollar volume today / trailing mean through t-1)."""
    dv = np.where(panel.dollar_volume > 0, panel.dollar_volume, np.nan)
    base = np.full(dv.shape, np.nan)
    base[1:] = _roll(dv, w, "mean", int(w * 0.8))[:-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.log(dv / base)


def log_dollar_volume(panel: Panel, w: int = 60) -> np.ndarray:
    dv = np.where(panel.dollar_volume > 0, panel.dollar_volume, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.log(_roll(dv, w, "mean", int(w * 0.8)))


def beta_to_spy(panel: Panel, w: int = 120) -> np.ndarray:
    """Rolling OLS beta on SPY, population moments over the days both returns exist."""
    r = panel.returns()
    m = np.repeat(r[:, [panel.col("SPY")]], r.shape[1], axis=1)
    both = np.isfinite(r) & np.isfinite(m)
    r, m = np.where(both, r, np.nan), np.where(both, m, np.nan)
    mp = int(w * 0.8)
    mu_r, mu_m = _roll(r, w, "mean", mp), _roll(m, w, "mean", mp)
    cov = _roll(r * m, w, "mean", mp) - mu_r * mu_m
    var_m = _roll(m * m, w, "mean", mp) - mu_m * mu_m
    with np.errstate(invalid="ignore", divide="ignore"):
        return cov / var_m


def days_since_event(panel: Panel, cap: int = 120) -> np.ndarray:
    """Sessions since the last earnings reaction session (0 on the day), capped; NaN before any."""
    if panel.events is None:
        return np.full(panel.close.shape, np.nan)
    out = np.full(panel.close.shape, np.nan)
    last = np.full(panel.N, np.nan)
    for t in range(panel.T):
        last = np.where(panel.events[t], t, last)
        out[t] = np.minimum(t - last, cap)
    return out


def last_event_reaction(panel: Panel, w: int = 60, decay_after: int = 63) -> np.ndarray:
    """SPY-relative reaction z of the latest earnings event (as in S04), carried forward until
    `decay_after` sessions have passed; NaN otherwise."""
    if panel.events is None:
        return np.full(panel.close.shape, np.nan)
    r = panel.returns()
    rel = r - r[:, [panel.col("SPY")]]
    sd = np.full(rel.shape, np.nan)
    sd[1:] = _roll(rel, w, "std", 40)[:-1]  # through t-1
    with np.errstate(invalid="ignore", divide="ignore"):
        z = rel / sd
    out = np.full(rel.shape, np.nan)
    cur, age = np.full(panel.N, np.nan), np.full(panel.N, np.inf)
    for t in range(panel.T):
        hit = panel.events[t] & np.isfinite(z[t])
        cur = np.where(hit, z[t], cur)
        age = np.where(hit, 0, age + 1)
        out[t] = np.where(age <= decay_after, cur, np.nan)
    return out


def spy_regime(panel: Panel) -> dict[str, np.ndarray]:
    """Market-wide columns broadcast to T x N: SPY 200-day trend and 20-day realized vol."""
    j = panel.col("SPY")
    c = panel.close[:, [j]]
    with np.errstate(invalid="ignore", divide="ignore"):
        trend = c / _roll(c, 200, "mean", 160) - 1
    vol = _roll(panel.returns()[:, [j]], 20, "std", 16) * np.sqrt(252)
    return {"spy_trend200": np.repeat(trend, panel.N, axis=1), "spy_vol20": np.repeat(vol, panel.N, axis=1)}


def cs_rank(x: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    """Cross-sectional percentile rank in (0, 1] per row among finite (and masked) entries."""
    out = np.full(x.shape, np.nan)
    for t in range(x.shape[0]):
        ok = np.isfinite(x[t]) if mask is None else (np.isfinite(x[t]) & mask[t])
        n = ok.sum()
        if n:
            v = x[t, ok]
            order = v.argsort(kind="stable")
            rk = np.empty(n)
            rk[order] = np.arange(1, n + 1)
            out[t, ok] = rk / n
    return out


def build(panel: Panel) -> dict[str, np.ndarray]:
    """The S05 base feature set (raw values; the model applies cs_rank per day where declared)."""
    on, intra = overnight_intraday(panel)
    f = {
        "ret_1": ret(panel, 1), "ret_5": ret(panel, 5), "ret_20": ret(panel, 20), "ret_60": ret(panel, 60),
        "ret_252_21": ret(panel, 252, 21), "vol_20": volatility(panel, 20), "vol_60": volatility(panel, 60),
        "overnight_20": on, "intraday_20": intra, "gap": gap(panel), "dist_high_252": dist_from_high(panel),
        "vol_surprise": volume_surprise(panel), "log_dv_60": log_dollar_volume(panel), "beta_120": beta_to_spy(panel),
        "days_since_earn": days_since_event(panel), "earn_reaction": last_event_reaction(panel),
    }
    f.update(spy_regime(panel))
    return f
