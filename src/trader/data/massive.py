"""Massive (ex-Polygon) reference data on the free Basic tier (5 calls/min): point-in-time ticker
lists, splits and dividends. Bars still come from Alpaca. Auth goes in a header, never the URL.

massive_tickers: common stocks (type CS) listed on each month-end session, 2016 -> pre-holdout.
  One row per (snapshot date, ticker), with cik / composite_figi for entity identity.
  event_ts = known_ts = snapshot session open (listing status is known that morning).
massive_splits / massive_dividends: corporate actions, ex-date < holdout_start.
  known_ts = declaration date (dividends) or execution date (splits) at 09:30 New York.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import polars as pl

from trader import calendar
from trader.config import NY, secret, settings
from trader.data import lake
from trader.data.http import Http, RateLimiter, RawArchive

TICKERS, SPLITS, DIVIDENDS = "massive_tickers", "massive_splits", "massive_dividends"
_limiter = RateLimiter(5, 62.0)  # free tier: 5 calls/minute (small margin)


def _http() -> Http:
    return Http("https://api.massive.com", {"Authorization": f"Bearer {secret('MASSIVE_API_KEY')}"}, _limiter, timeout=120)


def _pages(path: str, params: dict, archive: RawArchive):
    http = _http()
    page = http.get_json(path, params)
    archive.append({"path": path, "params": params}, page)
    yield page
    while page.get("next_url"):
        nxt = page["next_url"].replace("https://api.massive.com", "")
        page = http.get_json(nxt)
        archive.append({"path": nxt}, page)
        yield page


def _ts_at(d: date, hh: int, mm: int) -> datetime:
    return datetime.combine(d, datetime.min.time(), NY).replace(hour=hh, minute=mm)


def snapshot_dates() -> list[date]:
    s = settings()
    sess = calendar.sessions(s.history_start, s.holdout_start)
    out = [d for d, n in zip(sess, sess[1:] + [s.holdout_start], strict=True) if n.month != d.month]
    return out


def done_snapshots() -> set[date]:
    if not lake.exists(TICKERS):
        return set()
    return set(lake.scan(TICKERS).select("snapshot").unique().collect()["snapshot"].to_list())


def ingest_tickers(log=print) -> int:
    total = 0
    done = done_snapshots()
    for d in snapshot_dates():
        if d in done:
            continue
        rows = []
        arch = RawArchive("massive_tickers", f"{d}")
        for page in _pages("/v3/reference/tickers", {"market": "stocks", "type": "CS", "date": d.isoformat(),
                                                     "active": "true", "limit": 1000, "sort": "ticker"}, arch):
            for r in page.get("results", []):
                rows.append({k: r.get(k) for k in ("ticker", "name", "primary_exchange", "type", "cik",
                                                   "composite_figi", "share_class_figi", "delisted_utc")})
        df = pl.DataFrame(rows, schema={k: pl.Utf8 for k in ("ticker", "name", "primary_exchange", "type", "cik",
                                                              "composite_figi", "share_class_figi", "delisted_utc")})
        ts = _ts_at(d, 9, 30)
        df = df.with_columns(snapshot=pl.lit(d), event_ts=pl.lit(ts).dt.convert_time_zone("UTC"),
                             known_ts=pl.lit(ts).dt.convert_time_zone("UTC"))
        lake.write(df, TICKERS, key=["snapshot", "ticker"])
        total += df.height
        log(f"  massive tickers {d}: {df.height}")
    return total


def _corp_actions(path: str, date_field: str, dataset: str, key: list[str], fields: list[str], log=print) -> int:
    s = settings()
    start = (s.history_start - timedelta(days=60)).isoformat()
    params = {f"{date_field}.gte": start, f"{date_field}.lt": s.holdout_start.isoformat(), "limit": 1000,
              "order": "asc", "sort": date_field}
    arch = RawArchive(dataset, f"{start}_{s.holdout_start}")
    rows, n = [], 0
    for page in _pages(path, params, arch):
        rows += [{k: r.get(k) for k in fields} for r in page.get("results", [])]
        n += 1
        if n % 25 == 0:
            log(f"  {dataset}: {len(rows)} rows ({n} pages)")
    df = pl.DataFrame(rows, schema={k: (pl.Float64 if k in ("cash_amount", "split_from", "split_to") else pl.Utf8) for k in fields},
                      infer_schema_length=None)
    ev = pl.col(date_field).str.to_date()
    kn = pl.coalesce(pl.col("declaration_date").str.to_date(strict=False), ev) if "declaration_date" in fields else ev
    df = df.with_columns(
        event_ts=ev.cast(pl.Datetime("us")).dt.replace_time_zone("America/New_York").dt.convert_time_zone("UTC"),
        known_ts=(kn.cast(pl.Datetime("us")) + pl.duration(hours=9, minutes=30)).dt.replace_time_zone("America/New_York").dt.convert_time_zone("UTC"),
    )
    df, dropped = lake.drop_holdout(df)
    lake.write(df, dataset, key=key)
    log(f"  {dataset}: {df.height} rows (dropped {dropped} known inside the holdout)")
    return df.height


def ingest_corporate_actions(log=print) -> None:
    _corp_actions("/v3/reference/splits", "execution_date", SPLITS, ["ticker", "execution_date"],
                  ["id", "ticker", "execution_date", "split_from", "split_to"], log)
    _corp_actions("/v3/reference/dividends", "ex_dividend_date", DIVIDENDS, ["ticker", "ex_dividend_date", "id"],
                  ["id", "ticker", "ex_dividend_date", "declaration_date", "pay_date", "record_date", "cash_amount",
                   "currency", "dividend_type", "frequency"], log)
