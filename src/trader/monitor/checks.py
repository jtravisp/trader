"""Live-only checks (docs/LIVE_PAPER.md part 4), run by the daily report for every account. Paper is the
control: its checks should stay quiet, so a warning on live points at something live-specific.

- Unsettled cash: buys deferred "waiting for buying power" are expected the day after a sell in a cash
  account (T+1). A third straight session of them is a warning.
- Equity mark: our close mark of the previous session (cash + qty x official close) vs Alpaca's equity at
  that close (`last_equity`, read the next day, so after-hours prices don't enter). A gap above $0.50
  is a warning: fees, a corporate action, or a fill we didn't see.
- Tax fill log: every fill goes to <account>/fills.jsonl (append-only, one row per broker order) and
  fills_tax.csv is rebuilt from it with FIFO lots (Alpaca's default), realized gain per sell, and a flag on
  losing sells with a purchase of the same symbol within 30 days before or after (a possible wash sale, for
  the 1099-B cross-check and the CPA, PLAN P9).
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path

from trader.config import NY

MARK_GAP_USD = 0.50
SETTLEMENT_STREAK = 3
WASH_DAYS = 30


def settlement_streak(records: list[dict]) -> int:
    """Pure: consecutive latest records (by date) with buying-power deferrals."""
    n = 0
    for r in sorted(records, key=lambda r: r["date"], reverse=True):
        if not r.get("bp_deferrals"):
            break
        n += 1
    return n


def mark_gap(prev: dict | None, last_equity: float) -> float | None:
    """Pure: Alpaca's equity at the previous close minus our mark of it; None without a previous mark."""
    if not prev or prev.get("mark_equity") is None:
        return None
    return last_equity - prev["mark_equity"]


def tax_lots(fills: list[dict]) -> list[dict]:
    """Pure. fills: dicts with ts (ISO), symbol, side, qty, price, order_id, in time order. FIFO lots per
    symbol. Returns one row per fill with lot ids, cost/proceeds, realized gain (sells) and a possible-wash-sale
    flag (losing sell with a buy of the same symbol within 30 days before or after, other than the lots sold)."""
    lots: dict[str, deque] = defaultdict(deque)
    rows = []
    for f in sorted(fills, key=lambda f: f["ts"]):
        d = f["ts"][:10]
        amt = f["qty"] * f["price"]
        if f["side"] == "buy":
            lots[f["symbol"]].append([f["order_id"], f["qty"], f["price"], d])
            rows.append({**f, "date": d, "amount": round(amt, 4), "lots": f["order_id"], "basis": None, "gain": None,
                         "unmatched_qty": 0.0, "wash_sale_check": False})
            continue
        left, basis, used = f["qty"], 0.0, []
        q = lots[f["symbol"]]
        while left > 1e-9 and q:
            lot = q[0]
            take = min(lot[1], left)
            basis += take * lot[2]
            used.append(lot[0])
            lot[1] -= take
            left -= take
            if lot[1] <= 1e-9:
                q.popleft()
        matched = f["qty"] - left
        rows.append({**f, "date": d, "amount": round(amt, 4), "lots": "+".join(used), "basis": round(basis, 4),
                     "gain": round(matched * f["price"] - basis, 4), "unmatched_qty": round(left, 9), "wash_sale_check": False})
    for r in rows:
        if r["side"] != "sell" or not r["gain"] or r["gain"] >= 0:
            continue
        sold = set(r["lots"].split("+"))
        sd = date.fromisoformat(r["date"])
        r["wash_sale_check"] = any(
            b["side"] == "buy" and b["symbol"] == r["symbol"] and b["order_id"] not in sold
            and abs((date.fromisoformat(b["date"]) - sd).days) <= WASH_DAYS for b in rows)
    return rows


def append_fills(path: Path, fills: list) -> int:
    """Append broker fills not yet in the ledger (keyed by broker order id). Returns rows added."""
    seen = set()
    if path.exists():
        seen = {json.loads(x)["order_id"] for x in path.read_text(encoding="utf-8").splitlines() if x.strip()}
    new = []
    for o in fills:
        if o.broker_id in seen or not o.filled_qty or not o.filled_avg_price:
            continue
        new.append({"ts": o.submitted_at or datetime.now(NY).isoformat(), "symbol": o.symbol, "side": o.side,
                    "qty": o.filled_qty, "price": o.filled_avg_price, "order_id": o.broker_id})
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r) + "\n")
    return len(new)


def write_tax_csv(ledger: Path, out: Path) -> int:
    fills = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()] if ledger.exists() else []
    rows = tax_lots(fills)
    cols = ["date", "symbol", "side", "qty", "price", "amount", "order_id", "lots", "basis", "gain", "unmatched_qty", "wash_sale_check"]
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)
