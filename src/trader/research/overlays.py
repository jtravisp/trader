"""Research-only S03 overlays (declared in docs/STRATEGIES.md 2026-11-04). They live outside
src/trader/signals so the frozen S03 paper version (tag strategy/S03/v1, pinned paths) is untouched.
If one is adopted it moves into signals/ under a new tag.

S07: vol-managed momentum; S08: momentum with a per-position stop (simulated by the engine).
Both are family S03, with S03 lookback 126, skip 21 fixed."""

from __future__ import annotations

import numpy as np

from trader.backtest.panel import Panel
from trader.signals.library import REGISTRY, Momentum

VOL_WINDOW = 63


def vol_scale(targets: np.ndarray, close: np.ndarray, vol_target: float, window: int = VOL_WINDOW) -> np.ndarray:
    """Pure: scale each decided row by min(1, vol_target / sigma), sigma = annualized std of the row's
    weights applied to the trailing `window` close-to-close returns ending at that row (known at t)."""
    out = targets.copy()
    r = np.full_like(close, np.nan)
    r[1:] = close[1:] / close[:-1] - 1
    r = np.nan_to_num(r)
    for t in np.where(~np.all(np.isnan(targets), axis=1))[0]:
        if t < window:
            continue
        w = np.nan_to_num(targets[t])
        port = r[t - window + 1 : t + 1] @ w
        sig = float(np.std(port, ddof=1) * np.sqrt(252))
        if sig > 0:
            out[t] = targets[t] * min(1.0, vol_target / sig)
    return out


class VolManagedMomentum(Momentum):
    id, family = "S07", "S03"

    def targets(self, panel: Panel) -> np.ndarray:
        base = Momentum(lookback=126, skip=21).targets(panel)
        return vol_scale(base, panel.close, float(self.params["vol_target"]))


class StopMomentum(Momentum):
    id, family = "S08", "S03"

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self.stop_loss = float(params["stop"])  # read by the research runner -> engine.run(stop=...)

    def targets(self, panel: Panel) -> np.ndarray:
        return Momentum(lookback=126, skip=21).targets(panel)


class FastMomentum(Momentum):
    """S13 (declared 2026-11-13): S03's score and universe, decided every `hold` sessions."""

    id, family = "S13", "S03"

    def targets(self, panel: Panel) -> np.ndarray:
        from trader.research.fast import every_k
        from trader.signals.base import liquid_universe, only_on, top_k_equal_weight

        lb, skip, n = 126, 21, 500
        c = panel.close
        score = np.full_like(c, np.nan)
        score[lb:] = c[lb - skip : c.shape[0] - skip] / c[: c.shape[0] - lb] - 1
        return only_on(every_k(panel.T, int(self.params["hold"])),
                       top_k_equal_weight(score, liquid_universe(panel, n=n), n // 10))


REGISTRY.setdefault("S07", VolManagedMomentum)
REGISTRY.setdefault("S08", StopMomentum)
REGISTRY.setdefault("S13", FastMomentum)
