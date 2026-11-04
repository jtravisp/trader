"""Risk gateway: target portfolio -> validated, idempotent orders (D6, D9, limits.yaml).

Planning (validate_target, plan_orders) is pure and fully tested. run() adds the side effects:
kill switch, halts, reconciliation, write-ahead order log, submission.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, time
from zoneinfo import ZoneInfo

from trader.risk import state
from trader.risk.broker import Account, Broker, Position

NY = ZoneInfo("America/New_York")
CASH = "$CASH"


@dataclass
class PlannedOrder:
    symbol: str
    side: str  # buy | sell
    notional: float | None
    qty: float | None  # set for full exits (sell the whole position, no dust)
    chunk: int = 0

    def client_order_id(self, run_id: str) -> str:
        h = hashlib.sha1(f"{run_id}|{self.symbol}|{self.side}|{self.chunk}".encode()).hexdigest()[:24]
        return f"g-{h}"


@dataclass
class Plan:
    orders: list[PlannedOrder] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)  # (symbol, reason) -> push alert
    deferred: list[tuple[str, str]] = field(default_factory=list)  # expected throttling (turnover/count) -> log only
    notes: list[str] = field(default_factory=list)


class TargetRejected(ValueError):
    """The whole target is structurally invalid (corrupted): no orders from it."""


def validate_target(rows: list[tuple[str, object]], allowlist: set[str], lim: dict) -> tuple[dict[str, float], Plan]:
    plan = Plan()
    if not rows:
        raise TargetRejected("empty target (an all-cash target must say {'$CASH': 1.0})")
    seen: dict[str, float] = {}
    for sym, w in rows:
        if not isinstance(sym, str) or not sym.strip():
            raise TargetRejected(f"bad symbol {sym!r}")
        try:
            wf = float(w)  # type: ignore[arg-type]
        except (TypeError, ValueError) as e:
            raise TargetRejected(f"non-numeric weight for {sym}: {w!r}") from e
        if not math.isfinite(wf):
            raise TargetRejected(f"non-finite weight for {sym}")
        if wf < 0 and not lim["account"]["allow_short"]:
            raise TargetRejected(f"negative weight for {sym} (shorts disabled)")
        if sym in seen:
            raise TargetRejected(f"duplicate symbol {sym}")
        seen[sym] = wf
    seen.pop(CASH, None)
    gross = sum(seen.values())
    if gross > lim["account"]["max_gross_exposure"] * 1.05:
        raise TargetRejected(f"gross exposure {gross:.3f} far above the {lim['account']['max_gross_exposure']} limit")
    out: dict[str, float] = {}
    for sym, wf in seen.items():
        if sym not in allowlist:
            plan.refused.append((sym, "symbol not in allowlist"))
            continue
        out[sym] = wf
    return out, plan


def clip_weights(w: dict[str, float], etfs: set[str], lim: dict, plan: Plan) -> dict[str, float]:
    cap_s, cap_e = lim["position"]["max_weight_per_name"], lim["position"]["max_weight_per_etf"]
    bench, cap_b = lim["position"].get("benchmark_symbol"), lim["position"].get("max_weight_benchmark", cap_e)
    out = {}
    for s, x in w.items():
        cap = cap_b if s == bench else (cap_e if s in etfs else cap_s)
        if x > cap + 1e-9:
            plan.notes.append(f"{s}: weight {x:.3f} clipped to cap {cap}")
            x = cap
        out[s] = x
    g = sum(out.values())
    mx = lim["account"]["max_gross_exposure"]
    if g > mx + 1e-9:
        plan.notes.append(f"gross {g:.3f} scaled down to {mx}")
        out = {s: x * mx / g for s, x in out.items()}
    return out


def plan_orders(target: dict[str, float], acct: Account, pos: dict[str, Position], etfs: set[str], lim: dict,
                orders_today: int, notional_today: float, sells_only: bool, plan: Plan | None = None) -> Plan:
    plan = plan or Plan()
    o = lim["orders"]
    eq = acct.equity
    if eq <= 0:
        plan.notes.append("non-positive equity: no orders")
        return plan
    target = clip_weights(target, etfs, lim, plan)
    max_chunk = o["max_notional_per_order_pct"] * eq
    raw: list[PlannedOrder] = []
    for sym in sorted(set(target) | set(pos)):
        cur = pos[sym].market_value / eq if sym in pos else 0.0
        tgt = target.get(sym, 0.0)
        diff = tgt - cur
        if tgt <= 0 and sym in pos and pos[sym].qty > 0:
            raw.append(PlannedOrder(sym, "sell", None, pos[sym].qty))  # full exit by quantity
            continue
        # min drift applies to resizing a held position; a new entry only needs the minimum notional
        if (cur > 0 and abs(diff) < o["min_rebalance_drift"]) or abs(diff) * eq < o["min_order_notional_usd"]:
            continue
        side = "buy" if diff > 0 else "sell"
        if sells_only and side == "buy":
            plan.refused.append((sym, "daily-loss halt: no new buys today"))
            continue
        total = abs(diff) * eq
        k = 0
        while total > 1e-9:  # D18: split into chunks of at most max_chunk
            piece = min(total, max_chunk)
            raw.append(PlannedOrder(sym, side, piece, None, k))
            total -= piece
            k += 1
    # sells first (they free cash); then buys, largest first, within buying power
    sells = [x for x in raw if x.side == "sell"]
    buys = sorted((x for x in raw if x.side == "buy"), key=lambda x: -(x.notional or 0))
    bp = acct.buying_power
    turnover_cap = o["max_daily_turnover"] * eq
    run_cap, day_cap = o["max_orders_per_run"], o["max_orders_per_day"]
    used = notional_today
    for x in sells + buys:
        notional = x.notional if x.notional is not None else pos[x.symbol].market_value
        if len(plan.orders) >= run_cap or orders_today + len(plan.orders) >= day_cap:
            plan.deferred.append((x.symbol, "order-count limit reached"))
            continue
        room = turnover_cap - used
        if x.side == "buy":
            room = min(room, bp)
        if notional > room + 1e-6:
            # shrink a notional order to the remaining room; full-exit (qty) orders can't shrink
            if x.notional is not None and room >= o["min_order_notional_usd"]:
                plan.notes.append(f"{x.symbol}: order shrunk from {notional:.2f} to {room:.2f} (turnover/buying power)")
                x.notional = notional = room
            else:
                if x.side == "buy" and bp < turnover_cap - used:
                    plan.refused.append((x.symbol, "insufficient buying power"))
                else:
                    plan.deferred.append((x.symbol, f"daily turnover limit ({o['max_daily_turnover']:.0%}) reached"))
                continue
        if x.side == "buy":
            bp -= notional
        used += notional
        plan.orders.append(x)
    return plan


def in_window(now: datetime, lim: dict) -> bool:
    a, b = (time.fromisoformat(t) for t in lim["orders"]["trading_window_et"])
    t = now.astimezone(NY).time()
    return a <= t <= b


@dataclass
class RunResult:
    status: str
    submitted: int = 0
    refused: list[tuple[str, str]] = field(default_factory=list)
    deferred: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _alert(con: sqlite3.Connection, level: str, kind: str, msg: str) -> None:
    from trader.monitor.alerts import notify

    state.event(con, level, kind, msg)
    notify(level, f"[gateway] {kind}: {msg}")


def reconcile(con: sqlite3.Connection, broker: Broker, session: str, since_iso: str) -> list[str]:
    """Match the write-ahead order log with the broker. Returns problems (=> human halt)."""
    problems = []
    ours = {r[0]: r for r in con.execute("SELECT client_order_id, status FROM orders")}
    for cid, (_, status) in ours.items():
        if status == "submitting":  # crashed between intent and ack: ask the broker
            bo = broker.order_by_client_id(cid)
            new = (bo.status, bo.broker_id) if bo else ("not_submitted", None)
            con.execute("UPDATE orders SET status=?, broker_id=? WHERE client_order_id=?", (new[0], new[1], cid))
    con.commit()
    seen: dict[str, int] = {}
    for bo in broker.orders_since(since_iso):
        seen[bo.client_order_id] = seen.get(bo.client_order_id, 0) + 1
        if bo.client_order_id not in ours:
            problems.append(f"broker order {bo.broker_id} ({bo.symbol} {bo.side}) not in our log")
    dups = [c for c, n in seen.items() if n > 1]
    if dups:
        state.set_halt(con, "duplicate_order", f"duplicate client ids at broker: {dups[:5]}", session)
        problems.append(f"duplicates: {dups[:5]}")
    return problems


def preview(run_id: str, broker: Broker, con: sqlite3.Connection, allowlist: set[str], etfs: set[str], lim: dict) -> Plan:
    """Read-only plan for a target with the current account (ignores the trading window). Writes nothing,
    submits nothing; the real run re-plans at execution time with live state."""
    target, plan = validate_target(state.read_target(con, run_id), allowlist, lim)
    since = datetime.combine(datetime.now(NY).date(), time(0), NY).isoformat()
    row = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(notional),0) FROM orders WHERE intended_at >= ? AND status NOT IN ('not_submitted','rejected','error','dry_run')",
        (since,),
    ).fetchone()
    return plan_orders(target, broker.account(), broker.positions(), etfs, lim, int(row[0]), float(row[1]), False, plan)


def run(run_id: str, broker: Broker, con: sqlite3.Connection, allowlist: set[str], etfs: set[str], lim: dict,
        now: datetime, dry_run: bool = True) -> RunResult:
    session = now.astimezone(NY).date().isoformat()
    if state.KILL_FILE.exists():
        n = 0 if dry_run else broker.cancel_all()
        _alert(con, "critical", "kill_switch", f"KILL file present: cancelled {n} open orders, no new orders")
        return RunResult("killed")
    halts = [h for h in state.active_halts(con, session) if h["kind"] in state.HUMAN_HALTS]
    if halts:
        return RunResult("halted", notes=[f"{h['kind']}: {h['reason']}" for h in halts])

    since = datetime.combine(now.astimezone(NY).date(), time(0), NY).isoformat()
    problems = reconcile(con, broker, session, since)
    if problems:
        state.set_halt(con, "reconciliation_mismatch", "; ".join(problems)[:500], session)
        _alert(con, "critical", "reconciliation_mismatch", "; ".join(problems)[:500])
        return RunResult("halted", notes=problems)

    done = con.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if done and done[0] == "executed":  # a dry run never blocks the real run
        return RunResult("already_executed")

    try:
        target, plan = validate_target(state.read_target(con, run_id), allowlist, lim)
    except TargetRejected as e:
        con.execute("UPDATE runs SET status=?, detail=? WHERE run_id=?", ("rejected", str(e), run_id))
        con.commit()
        _alert(con, "error", "target_rejected", f"run {run_id}: {e}")
        return RunResult("rejected", notes=[str(e)])

    if not in_window(now, lim) or not broker.market_open():
        return RunResult("outside_window")

    acct, pos = broker.account(), broker.positions()
    peak = state.peak_equity(con, acct.equity)
    dd = acct.equity / peak - 1 if peak > 0 else 0.0
    if dd <= -lim["drawdown"]["peak_drawdown_halt"]:
        state.set_halt(con, "peak_drawdown", f"equity {acct.equity:.2f} is {dd:.1%} below peak {peak:.2f}", session)
        _alert(con, "critical", "peak_drawdown", f"{dd:.1%} from peak: trading halted until a human clears it")
        return RunResult("halted")
    day = acct.equity / acct.last_equity - 1 if acct.last_equity > 0 else 0.0
    sells_only = False
    if day <= -lim["drawdown"]["daily_loss_halt"]:
        state.set_halt(con, "daily_loss", f"down {day:.1%} today", session)
        sells_only = True
        _alert(con, "warning", "daily_loss", f"down {day:.1%}: no new buys until next session")

    row = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(notional),0) FROM orders WHERE intended_at >= ? AND status NOT IN ('not_submitted','rejected','error','dry_run')",
        (since,),
    ).fetchone()
    plan = plan_orders(target, acct, pos, etfs, lim, int(row[0]), float(row[1]), sells_only, plan)
    for sym, why in dict.fromkeys(plan.refused):  # one alert per (symbol, reason)
        _alert(con, "warning", "order_refused", f"{sym}: {why}")
    for sym, why in dict.fromkeys(plan.deferred):
        state.event(con, "info", "order_deferred", f"{sym}: {why}")

    submitted, unknown = 0, 0
    for po in plan.orders:
        cid = po.client_order_id(run_id)
        notional = po.notional if po.notional is not None else pos[po.symbol].market_value
        exists = con.execute("SELECT status FROM orders WHERE client_order_id=?", (cid,)).fetchone()
        if exists and exists[0] not in ("not_submitted", "dry_run"):
            continue  # idempotent: this exact order was already sent
        con.execute("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, run_id, po.symbol, po.side, notional, po.qty, state.now_iso(), None,
                     "dry_run" if dry_run else "submitting", None, ""))
        con.commit()  # write-ahead: intent is durable before the broker call
        if dry_run:
            continue
        try:
            bo = broker.submit(cid, po.symbol, po.side, po.notional, po.qty)
            con.execute("UPDATE orders SET broker_id=?, status=? WHERE client_order_id=?", (bo.broker_id, bo.status, cid))
            submitted += 1
        except Exception as e:
            # Outcome unknown (the broker may have accepted it): leave 'submitting' so reconcile()
            # asks the broker on the next start, and mark the run incomplete so it resumes.
            unknown += 1
            con.execute("UPDATE orders SET detail=? WHERE client_order_id=?", (str(e)[:300], cid))
            _alert(con, "error", "submit_failed", f"{po.symbol} {po.side}: {str(e)[:200]} (will reconcile)")
        con.commit()
    status = "dry_run" if dry_run else ("incomplete" if unknown else "executed")
    if dry_run:  # leave the run pending so the scheduled run still executes it
        state.event(con, "info", "dry_run", f"{run_id}: {len(plan.orders)} orders planned, {len(plan.refused)} refused")
    else:
        con.execute("UPDATE runs SET status=?, detail=? WHERE run_id=?",
                    (status, f"{len(plan.orders)} orders, {len(plan.refused)} refused, {unknown} unconfirmed", run_id))
    con.commit()
    return RunResult(status, submitted, refused=plan.refused, deferred=plan.deferred, notes=plan.notes)


def kill(broker: Broker, con: sqlite3.Connection) -> int:
    state.KILL_FILE.write_text(state.now_iso(), encoding="utf-8")
    n = broker.cancel_all()
    _alert(con, "critical", "kill_switch", f"kill engaged by operator: cancelled {n} open orders")
    return n


def flatten(broker: Broker, con: sqlite3.Connection) -> int:
    n = broker.close_all()
    _alert(con, "critical", "flatten", f"operator flattened all positions ({n} close orders)")
    return n
