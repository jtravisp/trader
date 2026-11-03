"""Alpaca (Benzinga) news ingestion: headlines + summaries (bodies excluded for size).

news: one row per news id. event_ts = known_ts = created_at (first publication time).
`updated_at` is kept; edits after creation are NOT point-in-time safe, so features should use
the headline as of creation (Alpaca returns the latest version; treat summaries as weaker).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import polars as pl

from trader.config import NY
from trader.data import lake
from trader.data.http import RawArchive, alpaca_data, paginate

NEWS = "news"

SCHEMA = {
    "id": pl.Int64, "headline": pl.Utf8, "summary": pl.Utf8, "author": pl.Utf8, "source": pl.Utf8,
    "url": pl.Utf8, "symbols": pl.List(pl.Utf8), "created_at": pl.Utf8, "updated_at": pl.Utf8,
}


def _to_frame(items: list[dict]) -> pl.DataFrame:
    rows = [{k: it.get(k) for k in SCHEMA} for it in items]
    df = pl.DataFrame(rows, schema=SCHEMA)
    return df.with_columns(
        pl.col("created_at").str.to_datetime(time_zone="UTC", time_unit="us"),
        pl.col("updated_at").str.to_datetime(time_zone="UTC", time_unit="us"),
    ).with_columns(event_ts=pl.col("created_at"), known_ts=pl.col("created_at"))


def fetch_window(start: datetime, end: datetime, archive: RawArchive | None = None, symbols: list[str] | None = None) -> pl.DataFrame:
    params: dict = {"start": start.isoformat(), "end": end.isoformat(), "limit": 50, "include_content": "false", "sort": "asc"}
    if symbols:
        params["symbols"] = ",".join(symbols)
    items: list[dict] = []
    for page in paginate(alpaca_data(), "/v1beta1/news", params, archive):
        items.extend(page.get("news", []))
    return _to_frame(items) if items else pl.DataFrame(schema={**SCHEMA, "event_ts": lake.TS, "known_ts": lake.TS})


def ingest(start: date, end: date, log=print, resume: bool = True) -> int:
    """Ingest [start, end) month by month; skips the holdout window."""
    total = 0
    months = []
    for wa, wb in lake.clip_window(start, end):  # never request the holdout year
        m = wa.replace(day=1)
        while m < wb:
            nxt = (m + timedelta(days=32)).replace(day=1)
            months.append((max(m, wa), min(nxt, wb)))
            m = nxt
    done = months_done() if resume else set()
    for a, b in months:
        if resume and f"{a:%Y-%m}" in done and b < max(e for _, e in months):
            continue  # resumable backfill: completed months are skipped (last month always refetched)
        st = datetime.combine(a, datetime.min.time(), NY)
        en = datetime.combine(b, datetime.min.time(), NY) - timedelta(seconds=1)
        df = fetch_window(st, en, RawArchive("alpaca_news", f"{a:%Y%m%d}_{b:%Y%m%d}"))
        df, dropped = lake.drop_holdout(df)
        lake.write(df, NEWS, key=["id"])
        total += df.height
        log(f"  news {a}..{b}: {df.height} items (dropped {dropped})")
    return total


def months_done() -> set[str]:
    """Months already present in the lake (for resumable backfills)."""
    if not lake.exists(NEWS):
        return set()
    lf = lake.scan(NEWS, allow_forward=True)
    return set(lf.select(pl.col("event_ts").dt.convert_time_zone("America/New_York").dt.strftime("%Y-%m").unique()).collect().to_series().to_list())
