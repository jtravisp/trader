"""LLM spend and forward-run reporting."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from trader.config import settings
from trader.live.forward import RUN_LOG
from trader.llm.scorer import connect, spent


def report(log=print) -> None:
    s = settings()
    con = connect()
    now = datetime.now(UTC)
    mtd = spent(con, now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    log(f"model {s.scoring_model} prompt {s.prompt_version}; month-to-date ${mtd:.4f} of ${s.llm_monthly_budget_usd:.2f}")
    for row in con.execute(
        "SELECT substr(scored_at,1,10) d, COUNT(*), SUM(valid), SUM(cost_usd), SUM(input_tokens), SUM(output_tokens) "
        "FROM scores GROUP BY d ORDER BY d DESC LIMIT 14"
    ):
        d, n, v, c, it, ot = row
        log(f"  {d}: {n} docs, invalid {n - v} ({(n - v) / n:.1%}), ${c:.4f}, tokens in {it} out {ot}")
    if RUN_LOG.exists():
        runs = [json.loads(line) for line in RUN_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]
        ok_days = sorted({r["day"] for r in runs if r["ok"]})
        log(f"forward runs: {len(runs)} total; successful days: {', '.join(ok_days[-10:]) or 'none'}")
        log(f"consecutive successful days (gate needs 5): {consecutive(ok_days)}")


def consecutive(days: list[str]) -> int:
    """Longest run of consecutive calendar days ending at the most recent successful day."""
    from datetime import date, timedelta

    if not days:
        return 0
    ds = sorted(date.fromisoformat(d) for d in days)
    n = 1
    for a, b in zip(reversed(ds[:-1]), reversed(ds[1:]), strict=True):
        if b - a == timedelta(days=1):
            n += 1
        else:
            break
    return n
