"""Forward LLM news scoring via the Message Batches API (50% off), with a hard budget (D11).

Store: data/llm/llm.db (SQLite)
  scores(model, prompt_version, doc_id, ...)  -- cache: each doc scored once per model+prompt
  batches(batch_id, ...)                       -- submitted batches and their state
Lake: llm_scores (one row per doc x symbol). event_ts = news created_at, known_ts = scored_at,
so a score is only usable from the moment we actually had it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import anthropic
import polars as pl
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from trader.config import secret, settings
from trader.data import lake
from trader.llm import prompt, schema

LLM_SCORES = "llm_scores"
MAX_TOKENS = 600

# USD per million tokens, standard tier (platform.claude.com pricing, fetched 2026-11-03).
PRICES = {"claude-haiku-5-5": (0.10, 0.50)}
BATCH_DISCOUNT = 0.5


def db_path() -> Path:
    p = settings().lake_dir.parent / "llm" / "llm.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(db_path())
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS scores(
            model TEXT, prompt_version TEXT, doc_id INTEGER, batch_id TEXT,
            created_at TEXT, scored_at TEXT, valid INTEGER, result_type TEXT, raw TEXT,
            input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL,
            PRIMARY KEY (model, prompt_version, doc_id));
        CREATE TABLE IF NOT EXISTS batches(
            batch_id TEXT PRIMARY KEY, model TEXT, prompt_version TEXT, submitted_at TEXT,
            n_requests INTEGER, est_cost_usd REAL, status TEXT, ended_at TEXT, cost_usd REAL);
        CREATE TABLE IF NOT EXISTS pending(
            batch_id TEXT, doc_id INTEGER, allowed TEXT, created_at TEXT,
            PRIMARY KEY (batch_id, doc_id));
        """
    )
    return con


def client() -> anthropic.Anthropic:
    # Own key name so it never collides with Claude Code's ANTHROPIC_API_KEY.
    return anthropic.Anthropic(api_key=secret("TRADER_ANTHROPIC_API_KEY"))


def cost(model: str, in_tok: int, out_tok: int, batch: bool = True) -> float:
    pin, pout = PRICES[model]
    c = (in_tok * pin + out_tok * pout) / 1e6
    return c * (BATCH_DISCOUNT if batch else 1.0)


# Calibrated 2026-11-03 with score-smoke: system prompt + schema ~= 1150 input tokens, output ~= 90.
SYS_TOKENS = 1150
OUT_TOKENS = 120  # margin over the measured 86


def estimate_cost(model: str, items: Sequence[dict]) -> float:
    in_tok = sum(SYS_TOKENS + len(prompt.user_message(i)) / 3.0 for i in items)
    return cost(model, int(in_tok), OUT_TOKENS * len(items))


def spent(con: sqlite3.Connection, since: datetime) -> float:
    """Actual cost of ended batches plus estimates for batches still open, since `since`."""
    row = con.execute(
        "SELECT COALESCE(SUM(COALESCE(cost_usd, est_cost_usd)),0) FROM batches WHERE submitted_at >= ?",
        (since.isoformat(),),
    ).fetchone()
    return float(row[0])


def already_scored(con: sqlite3.Connection, model: str, pv: str) -> set[int]:
    done = {r[0] for r in con.execute("SELECT doc_id FROM scores WHERE model=? AND prompt_version=?", (model, pv))}
    done |= {r[0] for r in con.execute(
        "SELECT p.doc_id FROM pending p JOIN batches b USING(batch_id) WHERE b.status != 'ended' AND b.model=? AND b.prompt_version=?",
        (model, pv),
    )}
    return done


def _request(model: str, item: dict) -> Request:
    return Request(
        custom_id=f"n{item['id']}",
        params=MessageCreateParamsNonStreaming(
            model=model,
            max_tokens=MAX_TOKENS,
            # Classification route: thinking off (Haiku 5.5 allows it at effort <= high), low effort.
            thinking={"type": "disabled"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema.json_schema()}},
            system=prompt.SYSTEM,
            messages=[{"role": "user", "content": prompt.user_message(item)}],
        ),
    )


def submit(items: list[dict], log=print) -> str | None:
    """Submit unscored items within budget. Items: dicts with id, created_at, symbols, headline, summary, source."""
    s = settings()
    model, pv = s.scoring_model, s.prompt_version
    if pv != prompt.PROMPT_VERSION:
        raise RuntimeError(f"settings prompt_version {pv} != code PROMPT_VERSION {prompt.PROMPT_VERSION}")
    con = connect()
    done = already_scored(con, model, pv)
    todo = [i for i in items if i["id"] not in done]
    if not todo:
        log("llm: nothing new to score")
        return None
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    room = min(
        s.llm_monthly_budget_usd - spent(con, month_start),
        float(s.raw["llm"].get("daily_budget_usd", 0.14)) - spent(con, day_start),
    )
    per_item = estimate_cost(model, todo[:200]) / min(len(todo), 200)
    n_fit = int(max(room, 0) / per_item) if per_item > 0 else 0
    if n_fit <= 0:
        log(f"llm: BUDGET EXHAUSTED (room ${room:.4f}); {len(todo)} items left unscored")
        return None
    if n_fit < len(todo):
        log(f"llm: budget allows {n_fit}/{len(todo)} items; scoring newest first")
        todo = sorted(todo, key=lambda i: i["created_at"], reverse=True)[:n_fit]
    est = estimate_cost(model, todo)
    batch = client().messages.batches.create(requests=[_request(model, i) for i in todo])
    con.execute(
        "INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?)",
        (batch.id, model, pv, now.isoformat(), len(todo), est, batch.processing_status, None, None),
    )
    con.executemany(
        "INSERT OR REPLACE INTO pending VALUES (?,?,?,?)",
        [(batch.id, i["id"], json.dumps(i["symbols"]), i["created_at"]) for i in todo],
    )
    con.commit()
    log(f"llm: submitted batch {batch.id} with {len(todo)} items (est ${est:.4f})")
    return batch.id


def collect(log=print) -> int:
    """Collect every ended batch; write valid scores to the lake. Returns docs collected."""
    con = connect()
    cl = client()
    total = 0
    for (bid, model, pv) in con.execute("SELECT batch_id, model, prompt_version FROM batches WHERE status != 'ended'").fetchall():
        b = cl.messages.batches.retrieve(bid)
        if b.processing_status != "ended":
            log(f"llm: batch {bid} still {b.processing_status}")
            continue
        pend = {r[0]: (set(json.loads(r[1])), r[2]) for r in con.execute("SELECT doc_id, allowed, created_at FROM pending WHERE batch_id=?", (bid,))}
        scored_at = (b.ended_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        rows, lake_rows, batch_cost, n_valid = [], [], 0.0, 0
        for res in cl.messages.batches.results(bid):
            doc_id = int(res.custom_id[1:])
            allowed, created_at = pend.get(doc_id, (set(), None))
            raw, valid, it, ot, c = None, 0, 0, 0, 0.0
            if res.result.type == "succeeded":
                msg = res.result.message
                it, ot = msg.usage.input_tokens, msg.usage.output_tokens
                c = cost(model, it, ot)
                raw = next((blk.text for blk in msg.content if blk.type == "text"), None)
                parsed = schema.parse(raw, allowed) if (raw and msg.stop_reason == "end_turn") else None
                if parsed is not None:
                    valid = 1
                    n_valid += 1
                    for sc in parsed.scores:
                        lake_rows.append({
                            "doc_id": doc_id, "symbol": sc.symbol, "model": model, "prompt_version": pv,
                            "event_type": parsed.event_type.value, "is_new_information": parsed.is_new_information,
                            "horizon_days": parsed.horizon_days, "risk_flags": [r.value for r in parsed.risk_flags],
                            "direction": sc.direction, "magnitude": sc.magnitude, "confidence": sc.confidence,
                            "event_ts": created_at, "known_ts": scored_at,
                        })
            batch_cost += c
            rows.append((model, pv, doc_id, bid, created_at, scored_at, valid, res.result.type, raw, it, ot, c))
        con.executemany("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.execute("UPDATE batches SET status='ended', ended_at=?, cost_usd=? WHERE batch_id=?", (scored_at, batch_cost, bid))
        con.commit()
        if lake_rows:
            df = pl.DataFrame(lake_rows, infer_schema_length=None).with_columns(
                pl.col("event_ts").str.to_datetime(time_zone="UTC", time_unit="us"),
                pl.col("known_ts").str.to_datetime(time_zone="UTC", time_unit="us"),
            )
            lake.write(df, LLM_SCORES, key=["model", "prompt_version", "doc_id", "symbol"])
        total += len(rows)
        log(f"llm: batch {bid} ended: {len(rows)} docs, {n_valid} valid, ${batch_cost:.4f}")
    return total


def smoke_test(log=print) -> bool:
    """One synchronous request (~$0.0001) to validate request shape, schema and parsing."""
    s = settings()
    item = {"id": 0, "created_at": "2026-11-03T13:00:00+00:00", "symbols": ["AAPL"], "source": "test",
            "headline": "Apple raises quarterly dividend 4% and authorizes $110 billion buyback", "summary": ""}
    p = dict(_request(s.scoring_model, item)["params"])
    msg = client().messages.create(**p)
    raw = next((b.text for b in msg.content if b.type == "text"), "")
    parsed = schema.parse(raw, {"AAPL"})
    log(f"stop_reason={msg.stop_reason} usage in={msg.usage.input_tokens} out={msg.usage.output_tokens} "
        f"cost=${cost(s.scoring_model, msg.usage.input_tokens, msg.usage.output_tokens, batch=False):.6f}")
    log(f"raw: {raw}")
    log("parsed OK" if parsed else "PARSE FAILED")
    return parsed is not None
