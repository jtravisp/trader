"""S06 stage 1: forward event study of LLM news scores (D5: post-cutoff documents only).

Declared in docs/STRATEGIES.md before any score was evaluated:
  signal   s = direction x magnitude x confidence, per (doc, symbol) score
  entry    first session whose OPEN is after known_ts (when we actually had the score)
  outcome  SPY-relative open(entry) -> open(entry + h) return, h in {1, 5}
  primary  mean daily cross-sectional Spearman IC of s vs the h=5 outcome, grouped by entry
           day, with a HAC (Newey-West, lag 5) t-stat
  read     the outcome statistics are computed only once MIN_SESSIONS entry days have a full
           h=5 outcome, so there is no repeated peeking (optional stopping). Before that the report
           shows data health only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import polars as pl

from trader import calendar
from trader.config import ROOT
from trader.data import bars, lake
from trader.llm.scorer import LLM_SCORES

MIN_SESSIONS = 60
HORIZONS = (1, 5)
LOG = ROOT / "experiments" / "llm_eval.jsonl"


def events() -> pl.DataFrame:
    s = lake.scan(LLM_SCORES, allow_forward=True).collect()
    if s.is_empty():
        return s
    s = s.with_columns(signal=pl.col("direction") * pl.col("magnitude") * pl.col("confidence"))
    sess = calendar.sessions_frame().select("date", "open_ts").sort("open_ts")
    s = s.sort("known_ts").join_asof(sess, left_on="known_ts", right_on="open_ts", strategy="forward",
                                     allow_exact_matches=False).rename({"date": "entry"})
    syms = [*s["symbol"].unique().to_list(), "SPY"]
    b = lake.scan(bars.DAILY, allow_forward=True).filter(
        pl.col("symbol").is_in(syms) & (pl.col("date") >= s["entry"].min())).select("symbol", "date", "adj_open").collect()
    b = b.sort("symbol", "date").with_columns(
        **{f"fwd{h}": pl.col("adj_open").shift(-h).over("symbol") / pl.col("adj_open") - 1 for h in HORIZONS})
    spy = b.filter(pl.col("symbol") == "SPY").select("date", *[pl.col(f"fwd{h}").alias(f"spy{h}") for h in HORIZONS])
    x = s.join(b.drop("adj_open"), left_on=["symbol", "entry"], right_on=["symbol", "date"], how="left").join(
        spy, left_on="entry", right_on="date", how="left")
    return x.with_columns(**{f"rel{h}": pl.col(f"fwd{h}") - pl.col(f"spy{h}") for h in HORIZONS})


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 5 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    ra, rb = a.argsort().argsort(), b.argsort().argsort()
    return float(np.corrcoef(ra, rb)[0, 1])


def _hac_t(x: np.ndarray, lag: int) -> float:
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 10:
        return float("nan")
    e = x - x.mean()
    v = e @ e / n
    for k in range(1, lag + 1):
        v += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / np.sqrt(v / n)) if v > 0 else float("nan")


def daily_ic(x: pl.DataFrame, h: int) -> pl.DataFrame:
    d = x.drop_nulls([f"rel{h}", "signal"])
    rows = [{"entry": k[0], "n": g.height, "ic": _spearman(g["signal"].to_numpy(), g[f"rel{h}"].to_numpy())}
            for k, g in d.group_by("entry")]
    return pl.DataFrame(rows, schema={"entry": pl.Date, "n": pl.Int64, "ic": pl.Float64}).sort("entry") if rows else pl.DataFrame()


def report(force: bool = False, log=print) -> dict:
    x = events()
    out: dict = {"run_at": datetime.now(UTC).isoformat(), "scores": x.height}
    if x.is_empty():
        log("llm eval: no scores yet")
        return out
    lat = (x["known_ts"] - x["event_ts"]).dt.total_minutes()
    full = x.drop_nulls("rel5")
    out |= {"docs": x["doc_id"].n_unique(), "symbols": x["symbol"].n_unique(),
            "entry_days": x["entry"].n_unique(), "entry_days_with_h5": full["entry"].n_unique(),
            "latency_min_median": float(lat.median()), "new_info_share": float(x["is_new_information"].mean())}
    ready = out["entry_days_with_h5"] >= MIN_SESSIONS
    out["read"] = bool(ready or force)
    if out["read"]:
        for h in HORIZONS:
            ic = daily_ic(x, h)
            v = ic["ic"].to_numpy() if ic.height else np.array([])
            out[f"ic{h}_mean"] = float(np.nanmean(v)) if len(v) else float("nan")
            out[f"ic{h}_t"] = _hac_t(v, lag=h)
            out[f"ic{h}_days"] = int(np.isfinite(v).sum())
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({**out, "forced": force}) + "\n")
    msg = (f"llm eval: {out['scores']} scores / {out['docs']} docs / {out['symbols']} symbols; entry days "
           f"{out['entry_days']} ({out['entry_days_with_h5']} with 5-day outcome; read at {MIN_SESSIONS}); "
           f"median latency {out['latency_min_median']:.0f} min")
    log(msg)
    if out["read"]:
        log("  " + ", ".join(f"h={h}: IC {out[f'ic{h}_mean']:+.4f} (t {out[f'ic{h}_t']:+.2f}, {out[f'ic{h}_days']} days)" for h in HORIZONS))
    return out
