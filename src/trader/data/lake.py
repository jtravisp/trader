"""Parquet lake with point-in-time columns and the holdout guard.

Layout: data/lake/<dataset>/year=YYYY/data.parquet
Every table carries `event_ts` (what the row describes) and `known_ts` (when it became
knowable), both tz-aware UTC.

Holdout guard (CLAUDE.md rule 2, D7):
- Writes: rows with holdout_start <= known_ts < holdout_end are refused, so the holdout year
  never lands on disk until the P7 run is approved (`unlock_holdout=True`, logged).
- Reads: research reads return known_ts < holdout_start only. Forward data
  (known_ts >= holdout_end) needs `allow_forward=True`. Holdout rows need `unlock_holdout=True`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from trader.config import ROOT, settings

TS = pl.Datetime("us", "UTC")
HOLDOUT_LOG = ROOT / "experiments" / "holdout_access.jsonl"


class HoldoutViolation(RuntimeError):
    pass


_UNLOCKED: str | None = None  # the P7 approval reference while holdout_unlocked() is active


@contextmanager
def holdout_unlocked(reason: str) -> Iterator[None]:
    """P7 only (CLAUDE.md rule 2): inside this block, ingests may write holdout-year rows and scans
    return them (never forward rows unless asked). Every unlock, read and write is logged to
    experiments/holdout_access.jsonl with the approval reference."""
    global _UNLOCKED
    if not reason:
        raise HoldoutViolation("holdout_unlocked requires a reason (P7 approval reference)")
    prev, _UNLOCKED = _UNLOCKED, reason
    _log_unlock("unlock", "*", reason)
    try:
        yield
    finally:
        _UNLOCKED = prev
        _log_unlock("relock", "*", reason)


def horizon() -> date:
    """Exclusive end of the data research may fetch or rank: holdout_start, or holdout_end while unlocked."""
    s = settings()
    return s.holdout_end if _UNLOCKED else s.holdout_start


def _log_unlock(action: str, dataset: str, reason: str) -> None:
    HOLDOUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now(UTC).isoformat(), "action": action, "dataset": dataset, "reason": reason}
    with HOLDOUT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def holdout_mask(known_ts: pl.Expr) -> pl.Expr:
    s = settings()
    return (known_ts >= pl.lit(s.holdout_start_ts, TS)) & (known_ts < pl.lit(s.holdout_end_ts, TS))


def clip_window(start, end) -> list[tuple]:
    """Split a [start, end) date window into the parts outside the holdout year."""
    s = settings()
    if _UNLOCKED:
        return [(start, end)] if start < end else []
    out = []
    if start < s.holdout_start:
        out.append((start, min(end, s.holdout_start)))
    if end > s.holdout_end:
        out.append((max(start, s.holdout_end), end))
    return [(a, b) for a, b in out if a < b]


def drop_holdout(df: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """Ingest-side clamp: remove holdout-window rows before they reach the lake."""
    if df.is_empty() or _UNLOCKED:
        return df, 0
    keep = df.filter(~holdout_mask(pl.col("known_ts")))
    return keep, df.height - keep.height


def _validate(df: pl.DataFrame, dataset: str) -> pl.DataFrame:
    for col in ("event_ts", "known_ts"):
        if col not in df.columns:
            raise ValueError(f"{dataset}: missing required column {col}")
        dt = df.schema[col]
        if not isinstance(dt, pl.Datetime) or dt.time_zone is None:
            raise ValueError(f"{dataset}: {col} must be tz-aware Datetime, got {dt}")
    df = df.with_columns(pl.col("event_ts").cast(TS), pl.col("known_ts").cast(TS))
    if df.filter(pl.col("known_ts").is_null()).height:
        raise ValueError(f"{dataset}: null known_ts")
    return df


def dataset_dir(dataset: str) -> Path:
    return settings().lake_dir / dataset


def write(
    df: pl.DataFrame,
    dataset: str,
    key: Sequence[str],
    *,
    unlock_holdout: bool = False,
    reason: str = "",
) -> int:
    """Upsert rows into the lake, partitioned by year of event_ts. Returns rows written."""
    if df.is_empty():
        return 0
    df = _validate(df, dataset)
    n_hold = df.filter(holdout_mask(pl.col("known_ts"))).height
    if n_hold and _UNLOCKED:
        unlock_holdout, reason = True, reason or _UNLOCKED
    if n_hold:
        if not unlock_holdout:
            raise HoldoutViolation(
                f"{dataset}: refusing to write {n_hold} rows inside the holdout window "
                "(clamp the ingest window or get explicit P7 approval)"
            )
        _log_unlock("write", dataset, reason)
    out = 0
    df = df.with_columns(pl.col("event_ts").dt.year().alias("_year"))
    for (year,), part in df.group_by("_year"):
        part = part.drop("_year")
        path = dataset_dir(dataset) / f"year={year}" / "data.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            old = pl.read_parquet(path)
            part = pl.concat([old, part], how="diagonal_relaxed")
        part = part.unique(subset=list(key), keep="last", maintain_order=True).sort(list(key))
        tmp = path.with_suffix(".tmp")
        part.write_parquet(tmp, compression="zstd")
        tmp.replace(path)
        out += part.height
    return out


DERIVED = {"universe_lines", "universe_pit"}  # rebuilt from other datasets; safe to replace wholesale


def replace(df: pl.DataFrame, dataset: str, key: Sequence[str]) -> int:
    """Drop and rewrite a derived dataset (a rebuild must not leave stale rows behind). Refuses
    source datasets, which are only ever upserted."""
    import shutil

    if dataset not in DERIVED:
        raise ValueError(f"{dataset} is not a derived dataset; use write()")
    d = dataset_dir(dataset)
    if d.exists():
        shutil.rmtree(d)
    return write(df, dataset, key)


def scan(
    dataset: str,
    *,
    allow_forward: bool = False,
    unlock_holdout: bool = False,
    reason: str = "",
) -> pl.LazyFrame:
    """Guarded lazy read of a dataset."""
    files = sorted(dataset_dir(dataset).glob("year=*/data.parquet"))
    if not files:
        raise FileNotFoundError(f"dataset {dataset} is empty")
    lf = pl.scan_parquet(files, hive_partitioning=False)
    if _UNLOCKED:
        unlock_holdout, reason = True, reason or _UNLOCKED
    s = settings()
    pre = pl.col("known_ts") < pl.lit(s.holdout_start_ts, TS)
    fwd = pl.col("known_ts") >= pl.lit(s.holdout_end_ts, TS)
    cond = pre
    if allow_forward:
        cond = cond | fwd
    if unlock_holdout:
        if not reason:
            raise HoldoutViolation("unlock_holdout requires a reason (P7 approval reference)")
        _log_unlock("read", dataset, reason)
        cond = cond | holdout_mask(pl.col("known_ts"))
    return lf.filter(cond)


def exists(dataset: str) -> bool:
    return any(dataset_dir(dataset).glob("year=*/data.parquet"))
