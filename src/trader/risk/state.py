"""Live state: live/orders.db (SQLite). Targets in, orders/halts/equity marks out."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from trader.config import ROOT

DB = ROOT / "live" / "orders.db"
KILL_FILE = ROOT / "KILL"

HUMAN_HALTS = {"peak_drawdown", "reconciliation_mismatch", "duplicate_order"}


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or DB
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS targets(run_id TEXT, created_at TEXT, strategy TEXT, symbol TEXT, weight REAL,
            PRIMARY KEY (run_id, symbol));
        CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, created_at TEXT, strategy TEXT, status TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS orders(client_order_id TEXT PRIMARY KEY, run_id TEXT, symbol TEXT, side TEXT,
            notional REAL, qty REAL, intended_at TEXT, broker_id TEXT, status TEXT, ref_price REAL, detail TEXT);
        CREATE TABLE IF NOT EXISTS halts(kind TEXT, set_at TEXT, session TEXT, reason TEXT, cleared_at TEXT, cleared_by TEXT);
        CREATE TABLE IF NOT EXISTS equity(ts TEXT PRIMARY KEY, equity REAL, peak REAL);
        CREATE TABLE IF NOT EXISTS events(ts TEXT, level TEXT, kind TEXT, detail TEXT);
        """
    )
    return con


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def event(con: sqlite3.Connection, level: str, kind: str, detail: dict | str) -> None:
    con.execute("INSERT INTO events VALUES (?,?,?,?)", (now_iso(), level, kind, json.dumps(detail) if isinstance(detail, dict) else detail))
    con.commit()


def set_halt(con: sqlite3.Connection, kind: str, reason: str, session: str) -> None:
    if not any(h["kind"] == kind for h in active_halts(con, session)):
        con.execute("INSERT INTO halts VALUES (?,?,?,?,?,?)", (kind, now_iso(), session, reason, None, None))
        con.commit()


def active_halts(con: sqlite3.Connection, session: str) -> list[dict]:
    """Human halts stay until cleared; daily-loss halts apply to their own session only (D9)."""
    rows = con.execute("SELECT kind, set_at, session, reason FROM halts WHERE cleared_at IS NULL").fetchall()
    out = []
    for kind, set_at, sess, reason in rows:
        if kind in HUMAN_HALTS or sess == session:
            out.append({"kind": kind, "set_at": set_at, "session": sess, "reason": reason})
    return out


def clear_halt(con: sqlite3.Connection, kind: str, who: str) -> int:
    cur = con.execute("UPDATE halts SET cleared_at=?, cleared_by=? WHERE kind=? AND cleared_at IS NULL", (now_iso(), who, kind))
    con.commit()
    return cur.rowcount


def write_target(con: sqlite3.Connection, run_id: str, strategy: str, weights: dict[str, float]) -> None:
    """Called by the live runner. The gateway re-validates everything it reads."""
    ts = now_iso()
    con.executemany("INSERT INTO targets VALUES (?,?,?,?,?)", [(run_id, ts, strategy, s, w) for s, w in weights.items()])
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?)", (run_id, ts, strategy, "target_written", ""))
    con.commit()


def read_target(con: sqlite3.Connection, run_id: str) -> list[tuple[str, object]]:
    return con.execute("SELECT symbol, weight FROM targets WHERE run_id=?", (run_id,)).fetchall()


def peak_equity(con: sqlite3.Connection, equity: float) -> float:
    row = con.execute("SELECT MAX(peak) FROM equity").fetchone()
    peak = max(float(row[0] or 0.0), equity)
    con.execute("INSERT OR REPLACE INTO equity VALUES (?,?,?)", (now_iso(), equity, peak))
    con.commit()
    return peak
