"""Experiment registry (SQLite, experiments/registry.db). One row per trial, failures included.

Daily returns of every trial are kept in experiments/returns/<trial_id>.parquet so the family's
deflated Sharpe and PBO can be recomputed over all variants actually tried.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from trader.config import ROOT

DB = ROOT / "experiments" / "registry.db"
RETURNS = ROOT / "experiments" / "returns"


def _git() -> tuple[str, bool]:
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "src"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
        return h or "nogit", dirty
    except OSError:
        return "nogit", True


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS trials(
            trial_id TEXT PRIMARY KEY, created_at TEXT, family TEXT, strategy_id TEXT, params TEXT,
            cost_mult REAL, delay INTEGER, git_hash TEXT, git_dirty INTEGER, data_start TEXT, data_end TEXT,
            holdout_touched INTEGER, leak_check TEXT, status TEXT, metrics TEXT, sharpe_daily REAL, notes TEXT)"""
    )
    return con


def record(*, family: str, strategy_id: str, params: dict, cost_mult: float, delay: int, data_start: str,
           data_end: str, leak_check: str, status: str, metrics: dict, dates: np.ndarray, net: np.ndarray,
           sharpe_daily: float, notes: str = "", holdout_touched: bool = False) -> str:
    now = datetime.now(UTC)
    key = json.dumps([strategy_id, params, cost_mult, delay, now.isoformat()], sort_keys=True)
    trial_id = f"{now:%Y%m%d-%H%M%S}-{hashlib.sha1(key.encode()).hexdigest()[:6]}"
    gh, dirty = _git()
    con = connect()
    con.execute(
        "INSERT INTO trials VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trial_id, now.isoformat(), family, strategy_id, json.dumps(params, sort_keys=True), cost_mult, delay,
         gh, int(dirty), data_start, data_end, int(holdout_touched), leak_check, status, json.dumps(metrics), sharpe_daily, notes),
    )
    con.commit()
    RETURNS.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"date": dates, "net": net}).write_parquet(RETURNS / f"{trial_id}.parquet")
    export_csv(con)
    return trial_id


def export_csv(con: sqlite3.Connection | None = None) -> Path:
    """Rewrite experiments/trials.csv (all trials, sorted by trial_id) so the full trial count is
    published in git alongside the reports. The DB stays the source of truth."""
    con = con or connect()
    cur = con.execute("SELECT * FROM trials ORDER BY trial_id")
    cols = [d[0] for d in cur.description]
    df = pl.DataFrame(cur.fetchall(), schema=cols, orient="row", infer_schema_length=None)
    out = DB.with_name("trials.csv")
    df.write_csv(out)
    return out


def family_trials(family: str, cost_mult: float = 1.0, delay: int = 0) -> pl.DataFrame:
    """Every pre-holdout trial of a family at a given cost/delay setting (variants count toward DSR/PBO)."""
    con = connect()
    rows = con.execute(
        "SELECT trial_id, strategy_id, params, sharpe_daily, status FROM trials WHERE family=? AND cost_mult=? AND delay=? "
        "AND holdout_touched=0",  # P7 holdout trials never feed research DSR/PBO
        (family, cost_mult, delay),
    ).fetchall()
    return pl.DataFrame(rows, schema=["trial_id", "strategy_id", "params", "sharpe_daily", "status"], orient="row")


def returns_matrix(trial_ids: list[str]) -> tuple[np.ndarray, list[str]]:
    """Aligned T x K matrix of daily net returns for the given trials (inner join on date)."""
    frames = [pl.read_parquet(RETURNS / f"{t}.parquet").rename({"net": t}) for t in trial_ids]
    df = frames[0]
    for f in frames[1:]:
        df = df.join(f, on="date", how="inner")
    return df.drop("date").to_numpy(), trial_ids


def read_trial(trial_id: str) -> dict:
    con = connect()
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM trials WHERE trial_id=?", (trial_id,)).fetchone()
    return dict(row) if row else {}


def report_dir() -> Path:
    d = ROOT / "experiments" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d
