"""Declared parameter grids (copied from docs/STRATEGIES.md; change both together, and a change
after results counts as new trials). `trader grid --strategy X` runs every point that has not
been run yet on the chosen universe, then prints the family summary (DSR, PBO, vs SPY)."""

from __future__ import annotations

import itertools
import json

from trader.research import factors, fast, overlays, registry, runner  # noqa: F401  (registers S07-S12)

GRIDS: dict[str, dict[str, list]] = {
    "S01": {"lookback": [63, 126, 252], "vol_target": [0.10, 0.15]},
    "S02": {"lookback": [5, 10], "bucket": ["decile", "quintile"]},
    "S03": {"lookback": [63, 126, 189, 252], "skip": [21]},  # 63/189 added 2026-11-04 (PBO)
    "S07": {"vol_target": [0.15, 0.20]},
    "S08": {"stop": [0.10, 0.20]},
    "S13": {"hold": [5, 10]},  # declared 2026-11-13, before P7
    "S09": {"lookback": [1, 3], "hold": [1, 3]},
    "S10": {"composite": ["roa", "roa_acc_lev"], "hold": [1, 5, 20]},
    "S11": {"window": [60, 252], "hold": [1, 5, 20]},
    "S12": {"lookback": [126, 252], "hold": [1, 5, 20]},
    "S04": {"threshold": [1.0, 2.0], "hold": [5, 20]},
    "S05": {"features": ["price", "price+fund", "all"], "num_leaves": [15, 63], "n_estimators": [300, 800]},
}


def points(strategy_id: str) -> list[dict]:
    g = GRIDS[strategy_id]
    return [dict(zip(g, v, strict=True)) for v in itertools.product(*g.values())]


def done(strategy_id: str, universe: str) -> list[dict]:
    from trader.signals.library import REGISTRY

    ft = registry.family_trials(strategy_id, 1.0, 0)
    if ft.height:
        ft = ft.filter(ft["status"].is_in(["ok", "leak_detected"]))  # superseded runs don't count as done
    any_universe = not REGISTRY[strategy_id].stock_universe  # ETF strategies: universe is irrelevant
    out = []
    for p in ft["params"].to_list() if ft.height else []:
        d = json.loads(p) if isinstance(p, str) else p
        if (any_universe or d.get("universe", "seed") == universe) and d.get("constraints", "limits") == "limits":
            out.append({k: d[k] for k in GRIDS[strategy_id] if k in d})
    return out


def run(strategy_id: str, universe: str = "pit", notes: str = "", log=print) -> list[str]:
    todo = [p for p in points(strategy_id) if p not in done(strategy_id, universe)]
    log(f"{strategy_id}: {len(points(strategy_id))} declared points, {len(todo)} to run on universe={universe}")
    paths = []
    for p in todo:
        paths.append(runner.full_experiment(strategy_id, p, notes=notes or f"declared grid ({universe})", log=log, universe=universe))
    return paths
