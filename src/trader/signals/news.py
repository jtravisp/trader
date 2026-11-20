"""S16 news tilt: S06 stage 2 (declared in docs/STRATEGIES.md 2026-11-20, D38). Forward data only (D5).

A score counts for the decision at the close of session t if it was known before the next session's open
and after the open four sessions earlier: five sessions of news, the same entry rule as S06 stage 1 (a
score enters at the first open after its known_ts). The matrix is built by the pure `llm_fields`; the
caller (shadow runner) loads the score rows and attaches the result as panel.fields.
"""

from __future__ import annotations

import numpy as np

from trader.backtest.panel import Panel
from trader.signals.base import Strategy, liquid_universe, only_on, schedule

WINDOW = 5  # sessions of news per decision


def llm_fields(symbols: list[str], open_ns: np.ndarray, score_sym: list[str], score_known_ns: np.ndarray,
               score_signal: np.ndarray, window: int = WINDOW) -> tuple[np.ndarray, np.ndarray]:
    """Pure. open_ns: int64 UTC nanoseconds of the opens of the T panel sessions followed by the next
    session's open (length T+1). Returns (mean signal, count), each T x N; mean is NaN with no scores.
    A score enters at e = first open strictly after known_ts; it counts for rows t with e-1 <= t <= e+window-2
    (entry sessions t-window+2 .. t+1)."""
    T, col = len(open_ns) - 1, {s: j for j, s in enumerate(symbols)}
    tot, cnt = np.zeros((T, len(symbols))), np.zeros((T, len(symbols)))
    e = np.searchsorted(open_ns, score_known_ns, side="right")
    j = np.array([col.get(s, -1) for s in score_sym], dtype=int)
    ok = (j >= 0) & np.isfinite(score_signal)
    for off in range(-1, window - 1):
        t = e + off
        m = ok & (t >= 0) & (t < T)
        np.add.at(tot, (t[m], j[m]), score_signal[m])
        np.add.at(cnt, (t[m], j[m]), 1.0)
    with np.errstate(invalid="ignore"):
        return np.where(cnt > 0, tot / cnt, np.nan), cnt


class NewsTilt(Strategy):
    """S16: weekly; the top `k` names of the liquid top 500 by mean LLM signal (>= `min_docs` scores, signal
    > 0) at 1/k each; unfilled slots stay in SPY."""

    id, family = "S16", "S16"
    needs_llm = True

    def targets(self, panel: Panel) -> np.ndarray:
        if "llm_signal" not in panel.fields:
            raise ValueError("S16 needs panel.fields['llm_signal'] / ['llm_count'] (forward scores, D5)")
        k, min_docs = int(self.params.get("k", 20)), int(self.params.get("min_docs", 2))
        sig, cnt = panel.fields["llm_signal"], panel.fields["llm_count"]
        ok = liquid_universe(panel, n=500) & (cnt >= min_docs) & np.isfinite(sig) & (sig > 0)
        s = np.where(ok, sig, -np.inf)
        order = (-s).argsort(axis=1, kind="stable")[:, :k]
        w = np.zeros((panel.T, panel.N))
        rows = np.arange(panel.T)[:, None]
        picked = np.take_along_axis(s, order, axis=1) > -np.inf
        w[rows, order] = np.where(picked, 1.0 / k, 0.0)
        w[:, panel.col("SPY")] += 1.0 - picked.sum(axis=1) / k
        return only_on(schedule(panel, "weekly"), w)
