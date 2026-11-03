"""Point-in-time stock universe (P3), from Massive month-end listings (D20: from 2016-10-31).

Identity: a "line" is one security of one company (SEC CIK), stitched across ticker renames
(FB -> META). Share classes that trade at the same time (GOOG/GOOGL) are separate lines.
Tickers that belonged to more than one company over 2016-10..now are dropped entirely
(vendors disagree on whose history they hold, e.g. DOC); see D21.

Datasets:
  universe_lines: line_id, cik, ticker, first_snapshot, last_snapshot
  universe_pit:   snapshot, line_id, ticker, rank, med_dollar_vol, close  (top N by liquidity)
    event_ts = known_ts = snapshot session close; a decision on day t uses the latest
    snapshot <= t.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from trader import calendar
from trader.config import settings
from trader.data import bars, lake, massive

LINES, PIT = "universe_lines", "universe_pit"
START = date(2016, 10, 31)  # D20
EXCHANGES = ["XNYS", "XNAS", "XASE", "ARCX", "BATS", "IEXG"]
TICKER_RE = r"^[A-Z]{1,5}(\.[A-Z])?$"  # excludes when-issued (…w), units, warrants, preferreds


def snapshots() -> pl.DataFrame:
    t = lake.scan(massive.TICKERS).filter(pl.col("snapshot") >= START).collect()
    return t.filter(pl.col("ticker").str.contains(TICKER_RE) & pl.col("primary_exchange").is_in(EXCHANGES))


def reused_tickers(t: pl.DataFrame, current: pl.DataFrame | None = None) -> set[str]:
    """Tickers mapped to >1 CIK across snapshots (or whose current owner differs)."""
    x = t.drop_nulls("cik").select("ticker", "cik")
    if current is not None and current.height:
        x = pl.concat([x, current.drop_nulls("cik").select("ticker", "cik")])
    return set(x.group_by("ticker").agg(pl.col("cik").n_unique().alias("n")).filter(pl.col("n") > 1)["ticker"])


def build_lines(t: pl.DataFrame, drop: set[str]) -> pl.DataFrame:
    t = t.filter(~pl.col("ticker").is_in(list(drop)))
    span = t.group_by("ticker").agg(
        pl.col("cik").drop_nulls().first().alias("cik"),
        pl.col("snapshot").min().alias("first_snapshot"),
        pl.col("snapshot").max().alias("last_snapshot"),
    ).with_columns(pl.coalesce("cik", pl.lit("T:") + pl.col("ticker")).alias("cik")).sort("cik", "first_snapshot")
    rows = []
    for (cik,), g in span.group_by("cik", maintain_order=True):
        lines: list[list[dict]] = []
        for r in g.iter_rows(named=True):
            # join the first line whose last ticker ended before this one started (a rename);
            # otherwise it coexists with every line (another share class): new line
            for ln in lines:
                if ln[-1]["last_snapshot"] < r["first_snapshot"]:
                    ln.append(r)
                    break
            else:
                lines.append([r])
        for k, ln in enumerate(lines):
            for r in ln:
                rows.append({"line_id": f"{cik}-{k}", **r})
    return pl.DataFrame(rows)


def current_listing(log=print) -> pl.DataFrame:
    """Today's Massive listing (forward reference data, not holdout) to catch tickers reused after 2025."""
    from trader.data.http import RawArchive

    arch = RawArchive("massive_tickers", "current")
    rows = []
    for page in massive._pages("/v3/reference/tickers", {"market": "stocks", "type": "CS", "active": "true", "limit": 1000}, arch):
        rows += [{"ticker": r.get("ticker"), "cik": r.get("cik")} for r in page.get("results", [])]
    log(f"  current listing: {len(rows)} tickers")
    return pl.DataFrame(rows, schema={"ticker": pl.Utf8, "cik": pl.Utf8})


def missing_bar_symbols(lines: pl.DataFrame) -> list[str]:
    have = set(lake.scan(bars.DAILY).select("symbol").unique().collect()["symbol"]) if lake.exists(bars.DAILY) else set()
    return sorted(set(lines["ticker"]) - have)


def rank_universe(lines: pl.DataFrame, t: pl.DataFrame, n: int = 1000, window: int = 60, min_price: float = 5.0) -> pl.DataFrame:
    b = lake.scan(bars.DAILY).filter(pl.col("symbol").is_in(lines["ticker"].to_list())).select(
        "symbol", "date", "close", "volume"
    ).collect().with_columns((pl.col("close") * pl.col("volume")).alias("dv"))
    tick_line = dict(zip(lines["ticker"], lines["line_id"], strict=True))
    out = []
    sess = calendar.sessions(START - timedelta(days=120), settings().holdout_start)
    for snap in sorted(t["snapshot"].unique().to_list()):
        listed = set(t.filter(pl.col("snapshot") == snap)["ticker"]) & set(tick_line)
        idx = sess.index(snap) if snap in sess else None
        if idx is None or idx < window:
            continue
        w0 = sess[idx - window + 1]
        win = b.filter(pl.col("symbol").is_in(list(listed)) & pl.col("date").is_between(w0, snap))
        agg = win.group_by("symbol").agg(
            pl.col("dv").median().alias("med_dollar_vol"), pl.len().alias("n_bars"),
            pl.col("close").sort_by("date").last().alias("close"), pl.col("date").max().alias("last_date"),
        ).filter((pl.col("n_bars") >= int(window * 0.67)) & (pl.col("close") >= min_price) & (pl.col("last_date") == snap))
        top = agg.sort("med_dollar_vol", descending=True).head(n).with_row_index("rank", offset=1)
        out.append(top.with_columns(snapshot=pl.lit(snap), line_id=pl.col("symbol").replace_strict(tick_line)).rename({"symbol": "ticker"}))
    df = pl.concat(out).select("snapshot", "line_id", "ticker", "rank", "med_dollar_vol", "close")
    close = calendar.sessions_frame().select(pl.col("date").alias("snapshot"), pl.col("close_ts").alias("known_ts"))
    return df.join(close, on="snapshot").with_columns(event_ts=pl.col("known_ts"))


def bar_consistency(lines: pl.DataFrame) -> pl.DataFrame:
    """Flag tickers whose Alpaca bars extend far outside their Massive listing window
    (a sign Alpaca's symbol history belongs to another company)."""
    b = lake.scan(bars.DAILY).filter(pl.col("symbol").is_in(lines["ticker"].to_list())).group_by("symbol").agg(
        pl.col("date").min().alias("bar_first"), pl.col("date").max().alias("bar_last"), pl.len().alias("n")
    ).collect()
    j = lines.join(b, left_on="ticker", right_on="symbol", how="left")
    hs = settings().holdout_start
    return j.with_columns(
        early=(pl.col("first_snapshot") > START + timedelta(days=40)) & (pl.col("bar_first") < pl.col("first_snapshot") - timedelta(days=60)),
        late=(pl.col("last_snapshot") < hs - timedelta(days=40)) & (pl.col("bar_last") > pl.col("last_snapshot") + timedelta(days=60)),
    )


def build(log=print, fetch: bool = True) -> pl.DataFrame:
    t = snapshots()
    cur = current_listing(log)
    drop = reused_tickers(t, cur)
    lines = build_lines(t, drop)
    log(f"universe: {t['snapshot'].n_unique()} snapshots, {lines.height} tickers in {lines['line_id'].n_unique()} lines; dropped {len(drop)} reused tickers")
    if fetch:
        miss = missing_bar_symbols(lines)
        log(f"fetching Alpaca daily bars for {len(miss)} symbols not yet in the lake")
        bars.ingest_daily(miss, log=log)
    chk = bar_consistency(lines)
    bad = set(chk.filter(pl.col("early") | pl.col("late"))["ticker"])
    nobars = set(chk.filter(pl.col("n").is_null())["ticker"])
    log(f"bar consistency: {len(bad)} tickers with bars far outside their listing window (dropped), {len(nobars)} without Alpaca bars")
    lines = lines.filter(~pl.col("ticker").is_in(list(bad)))
    ev = pl.col("first_snapshot").cast(pl.Datetime("us")).dt.replace_time_zone("America/New_York").dt.convert_time_zone("UTC")
    lake.write(lines.with_columns(event_ts=ev, known_ts=ev), LINES, key=["ticker"])
    pit = rank_universe(lines, t)
    lake.write(pit, PIT, key=["snapshot", "line_id"])
    log(f"universe_pit: {pit['snapshot'].n_unique()} snapshots x top {pit['rank'].max()}")
    return pit


def load_pit_panel(max_rank: int = 1000):
    """Backtest panel over every line that was ever in the top `max_rank`, plus the ETF list.
    Each line's bars are stitched across its tickers (latest ticker wins on overlapping dates);
    the column is named after the line's latest ticker. pit_rank[t, j] = rank from the latest
    snapshot <= date t (inf if not a member)."""
    import numpy as np

    from trader.backtest.panel import Panel, panel_from_frame
    from trader.universe.build import etf_list

    pit = lake.scan(PIT).filter(pl.col("rank") <= max_rank).collect()
    lines = lake.scan(LINES).filter(pl.col("line_id").is_in(pit["line_id"].unique().to_list())).collect()
    order = lines.sort("first_snapshot").group_by("line_id", maintain_order=True).agg(
        pl.col("ticker"), pl.col("ticker").last().alias("name")
    )
    prio = {t: (lid, i) for lid, ts in zip(order["line_id"], order["ticker"], strict=True) for i, t in enumerate(ts)}
    tick = list(prio) + etf_list()
    b = lake.scan(bars.DAILY).filter(pl.col("symbol").is_in(tick)).select(
        "symbol", "date", "adj_open", "adj_close", "close", "volume").collect()
    names = dict(zip(order["line_id"], order["name"], strict=True))
    b = b.with_columns(
        line=pl.col("symbol").replace_strict({t: v[0] for t, v in prio.items()}, default=pl.lit("ETF:") + pl.col("symbol")),
        pri=pl.col("symbol").replace_strict({t: v[1] for t, v in prio.items()}, default=0, return_dtype=pl.Int64),
    ).sort("line", "date", "pri", descending=[False, False, True]).unique(["line", "date"], keep="first", maintain_order=True)
    b = b.with_columns(symbol=pl.col("line").replace_strict(names, default=pl.col("line").str.replace("ETF:", "")))
    p = panel_from_frame(b.drop("line", "pri"))
    # membership matrix
    col = {s: j for j, s in enumerate(p.symbols)}
    snaps = sorted(pit["snapshot"].unique().to_list())
    rank = np.full((p.T, p.N), np.inf)
    dates = p.dates.astype("O")
    pit = pit.with_columns(sym=pl.col("line_id").replace_strict(names))
    by_snap = {s: g for (s,), g in pit.group_by("snapshot")}
    k = -1
    cur = None
    for i, d in enumerate(dates):
        while k + 1 < len(snaps) and snaps[k + 1] <= d:
            k += 1
            g = by_snap[snaps[k]]
            cur = np.full(p.N, np.inf)
            for sym, r in zip(g["sym"], g["rank"], strict=True):
                if sym in col:
                    cur[col[sym]] = r
        if cur is not None:
            rank[i] = cur
    return Panel(p.dates, p.symbols, p.open, p.close, p.raw_close, p.dollar_volume, p.is_etf,
                 {**p.meta, "universe": "pit", "lines": names}, rank)
