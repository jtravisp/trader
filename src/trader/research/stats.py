"""Overfitting statistics: PSR / deflated Sharpe, PBO via CSCV, bootstrap CIs, CPCV splits.

References:
- Bailey & Lopez de Prado (2012) "The Sharpe Ratio Efficient Frontier" (PSR);
  (2014) "The Deflated Sharpe Ratio".
- Bailey, Borwein, Lopez de Prado & Zhu (2015) "The Probability of Backtest Overfitting" (CSCV).
- Politis & Romano (1994) stationary bootstrap.
- Lopez de Prado (2018) "Advances in Financial ML", ch. 7 (purging, embargo, CPCV).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterator

import numpy as np
from scipy import stats as sst

EULER_GAMMA = 0.5772156649015329


def sharpe(r: np.ndarray) -> float:
    """Per-period Sharpe (rf = 0)."""
    r = r[np.isfinite(r)]
    sd = r.std(ddof=1)
    return float(r.mean() / sd) if sd > 0 else 0.0


def psr(r: np.ndarray, sr_star: float = 0.0) -> float:
    """Probability that the true per-period Sharpe exceeds sr_star, given skew/kurtosis."""
    r = r[np.isfinite(r)]
    n = len(r)
    sr = sharpe(r)
    g3 = float(sst.skew(r))
    g4 = float(sst.kurtosis(r, fisher=False))
    denom = math.sqrt(max(1 - g3 * sr + (g4 - 1) / 4 * sr * sr, 1e-12))
    return float(sst.norm.cdf((sr - sr_star) * math.sqrt(n - 1) / denom))


def expected_max_sharpe(sr_variance: float, n_trials: int) -> float:
    """Expected maximum per-period Sharpe among n_trials skill-less strategies."""
    if n_trials <= 1 or sr_variance <= 0:
        return 0.0
    z1 = sst.norm.ppf(1 - 1 / n_trials)
    z2 = sst.norm.ppf(1 - 1 / (n_trials * math.e))
    return math.sqrt(sr_variance) * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def deflated_sharpe(r: np.ndarray, trial_sharpes: list[float]) -> tuple[float, float]:
    """(DSR probability, SR0). trial_sharpes: per-period Sharpes of every trial in the family
    (including this one). With fewer than 2 trials the variance is unknown -> SR0 = 0 (= PSR)."""
    n = max(len(trial_sharpes), 1)
    var = float(np.var(trial_sharpes, ddof=1)) if len(trial_sharpes) >= 2 else 0.0
    sr0 = expected_max_sharpe(var, n)
    return psr(r, sr0), sr0


def pbo_cscv(returns: np.ndarray, n_blocks: int = 16) -> dict:
    """Probability of Backtest Overfitting. returns: T x K matrix (K strategy variants)."""
    T, K = returns.shape
    if K < 2:
        raise ValueError("PBO needs >= 2 variants")
    n_blocks -= n_blocks % 2
    usable = T - T % n_blocks
    blocks = np.array_split(np.arange(usable), n_blocks)
    # per-block sufficient statistics so each combination is cheap
    sums = np.stack([returns[b].sum(0) for b in blocks])
    sq = np.stack([(returns[b] ** 2).sum(0) for b in blocks])
    cnt = np.array([len(b) for b in blocks], dtype=float)

    def sr(idx: tuple[int, ...]) -> np.ndarray:
        s, q, n = sums[list(idx)].sum(0), sq[list(idx)].sum(0), cnt[list(idx)].sum()
        mu = s / n
        sd = np.sqrt(np.maximum(q / n - mu * mu, 1e-18))
        return mu / sd

    logits = []
    all_b = set(range(n_blocks))
    for is_idx in itertools.combinations(range(n_blocks), n_blocks // 2):
        oos_idx = tuple(sorted(all_b - set(is_idx)))
        is_sr, oos_sr = sr(is_idx), sr(oos_idx)
        best = int(np.argmax(is_sr))
        rank = float((oos_sr < oos_sr[best]).sum() + 1)  # 1..K, K = best OOS
        w = rank / (K + 1)
        logits.append(math.log(w / (1 - w)))
    lg = np.array(logits)
    return {"pbo": float((lg <= 0).mean()), "n_combinations": len(lg), "median_logit": float(np.median(lg))}


def stationary_bootstrap(r: np.ndarray, stat, n_boot: int = 2000, mean_block: int = 20, seed: int = 0) -> tuple[float, float]:
    """95% CI of stat(r) under the Politis-Romano stationary bootstrap."""
    r = r[np.isfinite(r)]
    n = len(r)
    rng = np.random.default_rng(seed)
    restart = rng.random((n_boot, n)) < 1.0 / mean_block
    restart[:, 0] = True
    starts = rng.integers(n, size=(n_boot, n))
    k = np.arange(n)
    last = np.maximum.accumulate(np.where(restart, k, 0), axis=1)  # index of the current block start
    idx = (np.take_along_axis(starts, last, axis=1) + (k - last)) % n
    out = np.array([stat(r[i]) for i in idx])
    lo, hi = np.percentile(out, [2.5, 97.5])
    return float(lo), float(hi)


def cpcv_splits(n: int, n_groups: int = 6, n_test: int = 2, horizon: int = 5, embargo: int = 5) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Combinatorial purged CV over n time-ordered samples. Training samples whose label window
    [i, i+horizon] overlaps a test group are purged; `embargo` samples after each test group are
    also removed. embargo must be >= the label horizon (run-experiment skill)."""
    if embargo < horizon:
        raise ValueError("embargo must be >= label horizon")
    groups = np.array_split(np.arange(n), n_groups)
    for test_g in itertools.combinations(range(n_groups), n_test):
        test = np.concatenate([groups[g] for g in test_g])
        bad = np.zeros(n, dtype=bool)
        for g in test_g:
            a, b = groups[g][0], groups[g][-1]
            bad[max(0, a - horizon) : min(n, b + 1 + embargo)] = True  # purge before, embargo after
        train = np.where(~bad)[0]
        yield train, test
