"""Alpaca bar ingestion (SIP feed, free tier history).

bars_daily: one row per (symbol, date). Raw (unadjusted) OHLCV plus `adj_*` columns from
Alpaca's adjustment=all (splits + dividends). event_ts = session open, known_ts = session close.
Note: adjusted *levels* are revised retroactively by later corporate actions; adjusted *returns*
are point-in-time safe. Use raw prices for price filters (e.g. > $5).

bars_minute: regular-session minute bars for the ETF list (slippage modelling).
event_ts = bar start, known_ts = bar end (start + 1 min).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta

import polars as pl

from trader import calendar
from trader.config import NY, settings
from trader.data import lake
from trader.data.http import RawArchive, alpaca_data, paginate

DAILY = "bars_daily"
MINUTE = "bars_minute"


def research_end() -> date:
    """Historical ingests stop before the holdout (exclusive end)."""
    return settings().holdout_start


def _iso(d: date | datetime) -> str:
    if isinstance(d, datetime):
        return d.astimezone(NY).isoformat()
    return datetime.combine(d, datetime.min.time(), NY).isoformat()


def _fetch(symbols: Sequence[str], timeframe: str, start: date, end: date, adjustment: str, archive: RawArchive) -> pl.DataFrame:
    rows: list[dict] = []
    params = {
        "symbols": ",".join(symbols),
        "timeframe": timeframe,
        "start": _iso(start),
        # Alpaca's end is inclusive: stop one second before midnight NY of the exclusive end date;
        # the free-tier SIP feed also rejects any window reaching into the last 15 minutes
        "end": min(datetime.combine(end, datetime.min.time(), NY) - timedelta(seconds=1),
                   datetime.now(NY) - timedelta(minutes=16)).isoformat(),
        "limit": 10000,
        "adjustment": adjustment,
        "feed": "sip",
        "sort": "asc",
    }
    for page in paginate(alpaca_data(), "/v2/stocks/bars", params, archive):
        for sym, bars in (page.get("bars") or {}).items():
            for b in bars:
                rows.append({"symbol": sym, **b})
    if not rows:
        return pl.DataFrame()
    # Infer types from ALL rows: Alpaca sends whole-number prices as JSON ints (1 rather than 1.0), and
    # inferring from the first 100 rows typed price columns as integers and truncated later decimals.
    return pl.DataFrame(rows, infer_schema_length=None).with_columns(
        pl.col("t").str.to_datetime(time_zone="UTC", time_unit="us").alias("t")
    )


def _daily_frame(raw: pl.DataFrame, adj: pl.DataFrame) -> pl.DataFrame:
    raw = raw.with_columns(pl.col("t").dt.convert_time_zone("America/New_York").dt.date().alias("date"))
    raw = raw.select(
        "symbol", "date",
        pl.col("o").cast(pl.Float64).alias("open"),
        pl.col("h").cast(pl.Float64).alias("high"),
        pl.col("l").cast(pl.Float64).alias("low"),
        pl.col("c").cast(pl.Float64).alias("close"),
        pl.col("v").cast(pl.Float64).alias("volume"),
        pl.col("n").cast(pl.Int64).alias("trades"),
        pl.col("vw").cast(pl.Float64).alias("vwap"),
    )
    if not adj.is_empty():
        adj = adj.with_columns(pl.col("t").dt.convert_time_zone("America/New_York").dt.date().alias("date")).select(
            "symbol", "date",
            pl.col("o").cast(pl.Float64).alias("adj_open"),
            pl.col("h").cast(pl.Float64).alias("adj_high"),
            pl.col("l").cast(pl.Float64).alias("adj_low"),
            pl.col("c").cast(pl.Float64).alias("adj_close"),
            pl.col("v").cast(pl.Float64).alias("adj_volume"),
        )
        raw = raw.join(adj, on=["symbol", "date"], how="left")
    sess = calendar.sessions_frame().rename({"open_ts": "event_ts", "close_ts": "known_ts"})
    out = raw.join(sess, on="date", how="left")
    # Bars on non-sessions should not exist; keep them visible to quality checks with a conservative known_ts.
    out = out.with_columns(
        pl.col("event_ts").fill_null(pl.col("date").cast(pl.Datetime("us")).dt.replace_time_zone("America/New_York").dt.convert_time_zone("UTC")),
    ).with_columns(pl.col("known_ts").fill_null(pl.col("event_ts") + pl.duration(hours=16)))
    return out


def _chunks(seq: Sequence[str], n: int) -> Iterable[list[str]]:
    for i in range(0, len(seq), n):
        yield list(seq[i : i + n])


def ingest_daily(symbols: Sequence[str], start: date | None = None, end: date | None = None, chunk: int = 100, log=print) -> int:
    s = settings()
    start = start or s.history_start
    end = end or research_end()
    total = 0
    for a, b in lake.clip_window(start, end):  # never fetch into the holdout
        archive = RawArchive("alpaca_bars_daily", f"{a}_{b}")
        for i, syms in enumerate(_chunks(sorted(set(symbols)), chunk)):
            raw = _fetch(syms, "1Day", a, b, "raw", archive)
            if raw.is_empty():
                continue
            adj = _fetch(syms, "1Day", a, b, "all", archive)
            df, _ = lake.drop_holdout(_daily_frame(raw, adj))
            lake.write(df, DAILY, key=["symbol", "date"])
            total += df.height
            log(f"  daily {a}..{b} chunk {i + 1}: {len(syms)} symbols, {df.height} rows")
    return total


def ingest_minute(symbols: Sequence[str], start: date, end: date | None = None, log=print) -> int:
    """Regular-session minute bars, fetched one symbol x one month at a time."""
    end = end or research_end()
    sess = calendar.sessions_frame()
    months = []
    for wa, wb in lake.clip_window(start, end):  # never fetch into the holdout
        m = wa.replace(day=1)
        while m < wb:
            nxt = (m + timedelta(days=32)).replace(day=1)
            months.append((max(m, wa), min(nxt, wb)))
            m = nxt
    total = 0
    for sym in symbols:
        parts: list[pl.DataFrame] = []
        for a, b in months:
            archive = RawArchive("alpaca_bars_minute", f"{sym}_{a:%Y%m}")
            df = _fetch([sym], "1Min", a, b, "raw", archive)
            if not df.is_empty():
                df = (
                    df.rename({"t": "event_ts"})
                    .with_columns(pl.col("event_ts").dt.convert_time_zone("America/New_York").dt.date().alias("date"))
                    .join(sess.select("date", "open_ts", "close_ts"), on="date", how="inner")
                    .filter((pl.col("event_ts") >= pl.col("open_ts")) & (pl.col("event_ts") < pl.col("close_ts")))
                    .select(
                        "symbol", "date", "event_ts",
                        (pl.col("event_ts") + pl.duration(minutes=1)).alias("known_ts"),
                        pl.col("o").cast(pl.Float64).alias("open"),
                        pl.col("h").cast(pl.Float64).alias("high"),
                        pl.col("l").cast(pl.Float64).alias("low"),
                        pl.col("c").cast(pl.Float64).alias("close"),
                        pl.col("v").cast(pl.Float64).alias("volume"),
                        pl.col("n").cast(pl.Int64).alias("trades"),
                        pl.col("vw").cast(pl.Float64).alias("vwap"),
                    )
                )
                parts.append(df)
        if parts:
            df, _ = lake.drop_holdout(pl.concat(parts))
            lake.write(df, MINUTE, key=["symbol", "event_ts"])
            total += df.height
        log(f"  minute {sym}: {sum(p.height for p in parts)} rows")
    return total
