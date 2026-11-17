"""Broker accounts: paper and live run side by side with separate state (docs/LIVE_PAPER.md, D36).

Each account keeps its own folder `live/<account>/` holding orders.db (targets, orders, halts, equity peak,
events), reports/ and KILL. `use(name)` points the gateway state, the daily report and the alert tag at
that folder. Nothing in one account's folder is read when another account runs, so a problem in one can
never trade, halt or reconcile the other.

Keys (.env): paper uses ALPACA_TRADE_KEY/SECRET (and ALPACA_TRADE_PAPER must not be false); live uses
ALPACA_LIVE_TRADE_KEY/SECRET. The broker for an account always connects to that account's endpoint, so
live keys can't reach paper state or the reverse (Alpaca answers 401).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from trader.config import ROOT, settings

NAMES = ("live", "paper")  # order matters: live always goes first (jobs, kill --all)
DEFAULTS = {
    "paper": {"enabled": True, "key_prefix": "ALPACA_TRADE"},
    "live": {"enabled": False, "key_prefix": "ALPACA_LIVE_TRADE"},
}

current = "paper"


def config(name: str) -> dict:
    if name not in NAMES:
        raise ValueError(f"unknown account {name!r}; expected one of {list(NAMES)}")
    return {**DEFAULTS[name], **settings().raw.get("accounts", {}).get(name, {})}


def enabled() -> list[str]:
    """Enabled accounts, live first."""
    return [n for n in NAMES if config(n)["enabled"]]


def strategy(name: str) -> dict:
    """The account's strategy settings: [accounts.<name>] overrides the shared [live] defaults."""
    live = settings().raw.get("live", {})
    acct = config(name)
    return {k: acct.get(k, live.get(k)) for k in ("strategy", "strategy_tag", "params")}


def folder(name: str, root: Path = ROOT) -> Path:
    return root / "live" / name


def migrate_legacy(root: Path = ROOT, log=print) -> bool:
    """One-time move of the pre-D36 single-account state (live/orders.db, live/reports/, KILL) into
    live/paper/, unchanged, so the P4 record and the equity peak carry over. Idempotent."""
    old_db, dest = root / "live" / "orders.db", folder("paper", root)
    if not old_db.exists():
        return False
    if (dest / "orders.db").exists():
        # never pick one silently: the wrong one would lose the equity peak, halts and order log
        raise RuntimeError(f"both {old_db} and {dest / 'orders.db'} exist; resolve by hand before trading")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old_db), str(dest / "orders.db"))
    if (root / "live" / "reports").exists() and not (dest / "reports").exists():
        shutil.move(str(root / "live" / "reports"), str(dest / "reports"))
    if (root / "KILL").exists():
        shutil.move(str(root / "KILL"), str(dest / "KILL"))
    log(f"accounts: moved legacy live state into {dest}")
    return True


def use(name: str, root: Path = ROOT, log=print) -> None:
    """Point gateway state, reports, kill switch and alert tags at one account."""
    global current
    config(name)  # validates the name
    from trader.monitor import alerts, daily
    from trader.risk import state

    if name == "paper":
        migrate_legacy(root, log=log)
    f = folder(name, root)
    state.DB, state.KILL_FILE = f / "orders.db", f / "KILL"
    daily.REPORTS = f / "reports"
    alerts.ACCOUNT = name
    current = name
