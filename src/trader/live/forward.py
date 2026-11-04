"""Daily forward-collection job (P1): news + bars for the last few days, then LLM scoring.

Runs every day (news arrives on weekends too). Only touches forward data (>= holdout_end):
the ingest helpers clip every window away from the holdout year.
Each run appends a record to data/llm/forward_runs.jsonl (the P1 gate needs 5 consecutive days).
"""

from __future__ import annotations

import json
import traceback
from datetime import UTC, date, datetime, timedelta

import polars as pl

from trader.config import settings
from trader.data import bars, lake, news

RUN_LOG = settings().lake_dir.parent / "llm" / "forward_runs.jsonl"
LOOKBACK_DAYS = 4
MAX_SYMBOLS_PER_ITEM = 8


def universe_symbols() -> set[str]:
    from trader.universe.build import etf_list, load_seed

    return set(etf_list()) | set(load_seed()["symbol"].to_list())


def items_to_score(since: datetime, universe: set[str]) -> list[dict]:
    if not lake.exists(news.NEWS):
        return []
    df = (
        lake.scan(news.NEWS, allow_forward=True)
        .filter(pl.col("known_ts") >= since)
        .select("id", "created_at", "symbols", "headline", "summary", "source")
        .collect()
    )
    out = []
    for r in df.iter_rows(named=True):
        syms = [s for s in (r["symbols"] or []) if s in universe]
        if not syms or len(r["symbols"]) > 3 * MAX_SYMBOLS_PER_ITEM:
            continue  # no universe names, or a market-wide list item
        out.append({**r, "symbols": syms[:MAX_SYMBOLS_PER_ITEM], "created_at": r["created_at"].isoformat()})
    return out


def last_closed_session_end() -> date:
    """Exclusive end date covering only sessions closed > 20 min ago (bars final; free-tier SIP
    rejects requests inside the last 15 minutes)."""
    from trader import calendar

    cutoff = datetime.now(UTC) - timedelta(minutes=20)
    sess = calendar.sessions_frame().filter(pl.col("close_ts") <= cutoff)
    return sess["date"].max() + timedelta(days=1)


def run(day: date, log=print) -> bool:
    from trader.llm import scorer

    rec: dict = {"day": day.isoformat(), "started": datetime.now(UTC).isoformat(), "data_ok": False, "llm_ok": False}
    s = settings()
    start = max(day - timedelta(days=LOOKBACK_DAYS), s.holdout_end)
    uni: set[str] = set()
    try:
        rec["news"] = news.ingest(start, day + timedelta(days=1), log=log, resume=False)
        uni = universe_symbols()
        rec["bars"] = bars.ingest_daily(sorted(uni), start, last_closed_session_end(), log=log)
        rec["data_ok"] = True
    except Exception as e:
        rec["data_error"] = f"{type(e).__name__}: {e}"
        log(traceback.format_exc())
    try:
        rec["collected"] = scorer.collect(log=log)
        # Every forward item not yet scored is a candidate (backlog included); budget caps the batch.
        since = datetime.combine(s.holdout_end, datetime.min.time(), UTC)
        items = items_to_score(since, uni or universe_symbols())
        rec["candidates"] = len(items)
        rec["batch"] = scorer.submit(items, log=log)
        rec["llm_ok"] = True
    except Exception as e:
        rec["llm_error"] = f"{type(e).__name__}: {str(e)[:300]}"
        log(f"llm step failed: {rec['llm_error']}")
    try:  # S06 stage-1 data health (outcome stats stay hidden until the declared read)
        from trader.llm import evaluate

        h = evaluate.report(log=log)
        rec["eval_entry_days_h5"] = h.get("entry_days_with_h5")
    except Exception as e:  # diagnostics never fail the run
        log(f"llm eval skipped: {type(e).__name__}: {str(e)[:200]}")
    rec["ok"] = rec["data_ok"] and rec["llm_ok"]
    rec["finished"] = datetime.now(UTC).isoformat()
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    log(f"forward run {day}: data {'OK' if rec['data_ok'] else 'FAILED'}, llm {'OK' if rec['llm_ok'] else 'FAILED'}")
    if not rec["ok"]:
        from trader.monitor.alerts import notify

        notify("error", f"[forward] {day}: data {'OK' if rec['data_ok'] else 'FAILED'}, llm {'OK' if rec['llm_ok'] else 'FAILED'}: "
               f"{rec.get('data_error') or rec.get('llm_error')}"[:300])
    return rec["ok"]
