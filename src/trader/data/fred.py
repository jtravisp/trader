"""FRED macro / regime series.

Official FRED API only (free key in .env as FRED_API_KEY, D15).
- Revised series (weekly/monthly macro): ALFRED first-release values (output_type=4);
  known_ts = realtime_start at 16:00 New York -> vintage-correct, point-in-time.
- Daily market series (not revised in practice): standard observations; known_ts = close of the
  first NYSE session after the observation date (conservative). ALFRED queries on these
  multi-thousand-vintage series time out server-side.
"""

from __future__ import annotations

from datetime import timedelta

import polars as pl

from trader import calendar
from trader.config import secret, settings
from trader.data import lake
from trader.data.http import Http, RateLimiter, RawArchive

FRED = "fred"

# series_id: (frequency, release note, revised)
SERIES: dict[str, tuple[str, str, bool]] = {
    "VIXCLS": ("D", "next_session_close", False),
    "DGS10": ("D", "next_session_close", False),
    "DGS2": ("D", "next_session_close", False),
    "DGS3MO": ("D", "next_session_close", False),
    "T10Y2Y": ("D", "next_session_close", False),
    "T10Y3M": ("D", "next_session_close", False),
    # ICE BofA OAS series carry only 3 years on FRED (licensing); Moody's spreads have full history.
    "BAA10Y": ("D", "next_session_close", False),
    "AAA10Y": ("D", "next_session_close", False),
    "DTWEXBGS": ("D", "next_session_close", False),
    "DCOILWTICO": ("D", "next_session_close", False),
    "T5YIE": ("D", "next_session_close", False),
    "ICSA": ("W", "plus_5d", True),        # week ending Sat, released Thu 08:30
    # NFCI dropped: revised weekly; the ALFRED query times out and latest values are not PIT.
    "UNRATE": ("M", "plus_1m_10d", True),  # first Friday of next month
    "CPIAUCSL": ("M", "plus_1m_20d", True),
    "INDPRO": ("M", "plus_1m_20d", True),
}

_limiter = RateLimiter(100, 60.0)


def _fetch_latest(series_id: str, key: str) -> pl.DataFrame:
    http = Http("https://api.stlouisfed.org", {}, _limiter, timeout=180)
    data = http.get_json("/fred/series/observations", {"series_id": series_id, "api_key": key, "file_type": "json"})
    RawArchive("fred", series_id).append({"id": series_id, "output_type": 1}, data)
    df = pl.DataFrame(data["observations"]).select(
        pl.col("date").str.to_date(), pl.col("value").cast(pl.Float64, strict=False)
    ).drop_nulls("value")
    sess = calendar.sessions_frame().select("date", "close_ts").rename({"date": "sess_date"}).sort("sess_date")
    df = df.sort("date").with_columns((pl.col("date") + pl.duration(days=1)).alias("next_day"))
    df = df.join_asof(sess, left_on="next_day", right_on="sess_date", strategy="forward")
    return df.select("date", "value", pl.col("close_ts").alias("known_ts"))


def _fetch_alfred(series_id: str, key: str) -> pl.DataFrame:
    """First-release values. FRED allows <= 2000 vintage dates per request, so walk the
    vintage-date list in windows and keep each observation's earliest release."""
    http = Http("https://api.stlouisfed.org", {}, _limiter, timeout=180)
    vint: list[str] = []
    off = 0
    while True:
        page = http.get_json("/fred/series/vintagedates", {"series_id": series_id, "api_key": key, "file_type": "json", "limit": 10000, "offset": off})
        vint += page["vintage_dates"]
        off += len(page["vintage_dates"])
        if off >= int(page["count"]) or not page["vintage_dates"]:
            break
    frames = []
    for i in range(0, len(vint), 400):
        w = vint[i : i + 400]
        data = http.get_json(
            "/fred/series/observations",
            {"series_id": series_id, "api_key": key, "file_type": "json", "output_type": 4,
             "realtime_start": w[0], "realtime_end": w[-1]},
        )
        RawArchive("fred_alfred", series_id).append({"id": series_id, "output_type": 4, "window": [w[0], w[-1]]}, data)
        if data["observations"]:
            frames.append(pl.DataFrame(data["observations"]).select(
                pl.col("date").str.to_date(), pl.col("value").cast(pl.Float64, strict=False),
                pl.col("realtime_start").str.to_date().alias("vintage"),
            ))
    df = pl.concat(frames).drop_nulls("value").sort("vintage").unique("date", keep="first")
    return df.with_columns(
        known_ts=(pl.col("vintage").cast(pl.Datetime("us")) + pl.duration(hours=16))
        .dt.replace_time_zone("America/New_York").dt.convert_time_zone("UTC")
    ).drop("vintage")


def ingest(log=print) -> int:
    s = settings()
    key = secret("FRED_API_KEY", required=False)
    if not key:
        raise RuntimeError("FRED_API_KEY missing: get a free key at https://fredaccount.stlouisfed.org/apikeys and add it to .env")
    total = 0
    for sid, (freq, _note, _revised) in SERIES.items():
        df = (_fetch_alfred(sid, key) if _revised else _fetch_latest(sid, key)).with_columns(vintage_safe=pl.lit(True))
        df = df.filter(pl.col("date") >= s.history_start - timedelta(days=400)).with_columns(
            series=pl.lit(sid), freq=pl.lit(freq),
            event_ts=pl.col("date").cast(pl.Datetime("us")).dt.replace_time_zone("America/New_York").dt.convert_time_zone("UTC"),
        ).drop_nulls("known_ts")
        df, dropped = lake.drop_holdout(df)
        lake.write(df.select("series", "freq", "date", "value", "vintage_safe", "event_ts", "known_ts"), FRED, key=["series", "date"])
        total += df.height
        log(f"  fred {sid}: {df.height} rows (dropped {dropped} in holdout)")
    return total
