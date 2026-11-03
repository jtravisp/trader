"""Data-quality checks (CLAUDE.md: every data source gets one here).

Each check returns a list of Issue. An issue is "explained" if it matches an entry in
config/quality_exceptions.yaml (with a written reason). The P1 gate needs zero unexplained
failures. Report: data/quality/<YYYY-MM-DD>.md
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime

import polars as pl
import yaml

from trader import calendar
from trader.config import ROOT, settings
from trader.data import bars, edgar, fred, lake, news

EXCEPTIONS = ROOT / "config" / "quality_exceptions.yaml"


@dataclass
class Issue:
    check: str
    key: str  # e.g. symbol or symbol:date
    detail: str
    severity: str = "fail"  # fail | warn
    explained: str = ""


def _exceptions() -> list[dict]:
    if not EXCEPTIONS.exists():
        return []
    return yaml.safe_load(EXCEPTIONS.read_text(encoding="utf-8")) or []


def _explain(issues: list[Issue]) -> list[Issue]:
    exc = _exceptions()
    for i in issues:
        for e in exc:
            if e["check"] == i.check and (e["key"] == i.key or (e["key"].endswith("*") and i.key.startswith(e["key"][:-1]))):
                i.explained = e["reason"]
    return issues


# ---------------------------------------------------------------- holdout guard
def check_holdout_absent() -> list[Issue]:
    """No dataset on disk may contain holdout-window rows (reads the files directly, counting only)."""
    out = []
    root = settings().lake_dir
    for ds in sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []:
        files = sorted((root / ds).glob("year=*/data.parquet"))
        if not files:
            continue
        n = pl.scan_parquet(files).filter(lake.holdout_mask(pl.col("known_ts"))).select(pl.len()).collect().item()
        if n:
            out.append(Issue("holdout_absent", ds, f"{n} rows inside the holdout window"))
    return out


def _expected_sessions() -> pl.DataFrame:
    """NYSE sessions, minus the holdout year (deliberately absent from the lake, D12)."""
    st = settings()
    f = calendar.sessions_frame()
    return f.filter(~((pl.col("date") >= st.holdout_start) & (pl.col("date") < st.holdout_end)))


# ---------------------------------------------------------------- daily bars
def _daily() -> pl.DataFrame:
    return lake.scan(bars.DAILY, allow_forward=True).collect()


def check_daily_integrity(df: pl.DataFrame) -> list[Issue]:
    out = []
    bad = df.filter(
        (pl.col("low") > pl.min_horizontal("open", "close") + 1e-9)
        | (pl.col("high") < pl.max_horizontal("open", "close") - 1e-9)
        | (pl.col("low") <= 0)
        | pl.col("adj_close").is_null()
        | (pl.col("volume") < 0)
    )
    for r in bad.select("symbol", "date").iter_rows():
        out.append(Issue("daily_integrity", f"{r[0]}:{r[1]}", "OHLC inconsistent, non-positive price, or missing adj_close"))
    dup = df.group_by("symbol", "date").len().filter(pl.col("len") > 1)
    for r in dup.iter_rows():
        out.append(Issue("daily_integrity", f"{r[0]}:{r[1]}", "duplicate bar"))
    nonsess = df.join(calendar.sessions_frame().select("date"), on="date", how="anti")
    for r in nonsess.select("symbol", "date").iter_rows():
        out.append(Issue("daily_integrity", f"{r[0]}:{r[1]}", "bar on a non-session date"))
    return out


def check_daily_gaps(df: pl.DataFrame) -> list[Issue]:
    """Missing sessions between a symbol's first and last bar."""
    sess = _expected_sessions().select("date").with_row_index("si")
    x = df.select("symbol", "date").join(sess, on="date", how="inner").sort("symbol", "date")
    x = x.with_columns((pl.col("si") - pl.col("si").shift(1).over("symbol") - 1).alias("missing"))
    gaps = x.filter(pl.col("missing") > 0)
    out = []
    for sym, g in gaps.group_by("symbol"):
        tot = int(g["missing"].sum())
        first = g.sort("date")["date"][0]
        sev = "fail" if tot > 5 or int(g["missing"].max()) > 3 else "warn"
        out.append(Issue("daily_gaps", str(sym[0]), f"{tot} missing sessions in {g.height} gaps (first gap ends {first})", sev))
    return out


def check_daily_outliers(df: pl.DataFrame, thresh: float = 0.5) -> list[Issue]:
    """Adjusted close-to-close moves beyond +-50%: likely missing split adjustment or bad print."""
    sess = calendar.sessions_frame().select("date").with_row_index("si")  # full calendar: holdout gap is not adjacent
    x = df.join(sess, on="date", how="inner").sort("symbol", "date").with_columns(
        (pl.col("adj_close") / pl.col("adj_close").shift(1).over("symbol") - 1).alias("r"),
        (pl.col("si") - pl.col("si").shift(1).over("symbol")).alias("step"),
    ).filter((pl.col("step") == 1) & (pl.col("r").abs() > thresh))  # adjacent sessions only
    return [
        Issue("daily_outliers", f"{s}:{d}", f"adjusted return {r:+.1%}", "warn")
        for s, d, r in x.select("symbol", "date", "r").iter_rows()
    ]


def check_adjustment_second_source(df: pl.DataFrame, sample: int, seed: int) -> list[Issue]:
    """Compare Alpaca adjusted calendar-year returns with Yahoo for random names (tolerance 0.5%/yr)."""
    import yfinance as yf

    s = settings()
    syms = sorted(df.filter(pl.col("date") < date(2017, 1, 10))["symbol"].unique().to_list())
    pick = random.Random(seed).sample(syms, min(sample, len(syms)))
    out = []
    end = s.holdout_start
    for sym in pick:
        a = df.filter((pl.col("symbol") == sym) & (pl.col("date") < end)).select("date", "adj_close").sort("date")
        ysym = sym.replace(".", "-")
        y = yf.download(ysym, start="2016-01-01", end=end.isoformat(), auto_adjust=True, progress=False)
        if y is None or y.empty:
            out.append(Issue("adjustment_2nd_source", sym, "no Yahoo data", "warn"))
            continue
        close = y["Close"].iloc[:, 0] if hasattr(y["Close"], "columns") else y["Close"]
        yy = pl.DataFrame({"date": [d.date() for d in close.index], "y_close": close.to_numpy()})
        j = a.join(yy, on="date", how="inner")
        yr = j.group_by(pl.col("date").dt.year().alias("year")).agg(pl.col("adj_close").last(), pl.col("y_close").last()).sort("year")
        yr = yr.with_columns(
            (pl.col("adj_close") / pl.col("adj_close").shift(1) - 1).alias("ra"),
            (pl.col("y_close") / pl.col("y_close").shift(1) - 1).alias("ry"),
        ).drop_nulls()
        bad = yr.filter((pl.col("ra") - pl.col("ry")).abs() > 0.005)
        for yv, ra, ry in bad.select("year", "ra", "ry").iter_rows():
            out.append(Issue("adjustment_2nd_source", f"{sym}:{yv}", f"alpaca {ra:+.2%} vs yahoo {ry:+.2%}"))
        # daily-level sanity: max abs difference of daily returns
        j2 = j.with_columns(
            (pl.col("adj_close") / pl.col("adj_close").shift(1) - 1).alias("ra"),
            (pl.col("y_close") / pl.col("y_close").shift(1) - 1).alias("ry"),
        ).drop_nulls()
        big = j2.filter((pl.col("ra") - pl.col("ry")).abs() > 0.02)
        if big.height:
            out.append(Issue("adjustment_2nd_source", f"{sym}:daily", f"{big.height} days with daily return diff > 2% (first {big['date'][0]})", "warn"))
    return [Issue("adjustment_2nd_source", "_sample", f"checked {', '.join(pick)}", "info")] + out


# ---------------------------------------------------------------- minute bars
def check_minute_coverage() -> list[Issue]:
    if not lake.exists(bars.MINUTE):
        return [Issue("minute_coverage", "_dataset", "bars_minute not ingested yet")]
    m = lake.scan(bars.MINUTE).group_by("symbol", "date").agg(pl.len().alias("n")).collect()
    sess = calendar.sessions_frame().with_columns(
        ((pl.col("close_ts") - pl.col("open_ts")).dt.total_minutes()).alias("expected")
    ).select("date", "expected")
    x = m.join(sess, on="date").with_columns((pl.col("n") / pl.col("expected")).alias("cov"))
    out = []
    for sym, g in x.group_by("symbol"):
        low = g.filter(pl.col("cov") < 0.9)
        if low.height:
            out.append(Issue("minute_coverage", str(sym[0]), f"{low.height} sessions with <90% minute coverage (median cov {g['cov'].median():.2f})", "warn"))
    return out


# ---------------------------------------------------------------- news
def check_news() -> list[Issue]:
    if not lake.exists(news.NEWS):
        return [Issue("news", "_dataset", "news not ingested yet")]
    n = lake.scan(news.NEWS).with_columns(pl.col("known_ts").dt.convert_time_zone("America/New_York").dt.date().alias("d"))
    daily = n.group_by("d").len().collect()
    if daily.is_empty():
        return [Issue("news", "_dataset", "no pre-holdout news in the lake yet")]
    sess = _expected_sessions().filter(
        (pl.col("date") >= daily["d"].min()) & (pl.col("date") <= daily["d"].max())
    ).select(pl.col("date").alias("d"))
    missing = sess.join(daily, on="d", how="anti")
    thin = daily.join(sess, on="d").filter(pl.col("len") < 50)
    out = []
    if missing.height:
        out.append(Issue("news", "missing_days", f"{missing.height} sessions with zero news (first {missing['d'].min()})"))
    if thin.height:
        out.append(Issue("news", "thin_days", f"{thin.height} sessions with <50 items (first {thin['d'].min()})", "warn"))
    nohead = n.filter(pl.col("headline").is_null() | (pl.col("headline") == "")).select(pl.len()).collect().item()
    if nohead:
        out.append(Issue("news", "empty_headline", f"{nohead} items without headline", "warn"))
    return out


# ---------------------------------------------------------------- edgar
def check_edgar() -> list[Issue]:
    out = []
    if not lake.exists(edgar.FILINGS):
        return [Issue("edgar", "_dataset", "edgar_filings not ingested yet")]
    f = lake.scan(edgar.FILINGS).collect()
    early = f.filter(pl.col("known_ts") < pl.col("event_ts") - pl.duration(days=1))
    if early.height:
        out.append(Issue("edgar", "known_before_event", f"{early.height} filings accepted >1 day before their report date", "warn"))
    e8 = f.filter((pl.col("form") == "8-K") & pl.col("items").str.contains("2.02"))
    per = e8.group_by("cik", pl.col("filing_date").dt.year().alias("y")).len()
    many = per.filter(pl.col("len") > 8)
    if many.height:
        out.append(Issue("edgar", "8k_202_count", f"{many.height} cik-years with >8 Item 2.02 8-Ks", "warn"))
    if lake.exists(edgar.FACTS):
        fa = lake.scan(edgar.FACTS).select(pl.len(), pl.col("accession").null_count()).collect()
        out.append(Issue("edgar", "facts_rows", f"{fa.item(0, 0)} fact rows", "info"))
    else:
        out.append(Issue("edgar", "facts_dataset", "edgar_facts not ingested yet"))
    return out


# ---------------------------------------------------------------- fred
def check_fred() -> list[Issue]:
    if not lake.exists(fred.FRED):
        return [Issue("fred", "_dataset", "fred not ingested yet (needs FRED_API_KEY)")]
    f = lake.scan(fred.FRED).group_by("series").agg(pl.col("date").max().alias("last"), pl.len()).collect()
    out = []
    for sid, last, _n in f.iter_rows():
        lag = (settings().holdout_start - last).days
        limit = {"D": 10, "W": 21, "M": 75}[fred.SERIES[sid][0]]
        if lag > limit:
            out.append(Issue("fred", sid, f"last observation {last} is {lag} days before holdout start"))
    return out


# ---------------------------------------------------------------- massive
def check_massive() -> list[Issue]:
    from trader.data import massive

    if not lake.exists(massive.TICKERS):
        return [Issue("massive", "_dataset", "massive_tickers not ingested yet", "warn")]
    t = lake.scan(massive.TICKERS).group_by("snapshot").agg(
        pl.len().alias("n"), (pl.col("primary_exchange") == "XNAS").sum().alias("nasdaq")
    ).collect().sort("snapshot")
    out = []
    for snap, n, nas in t.iter_rows():
        if nas < 1000:
            sev = "warn" if snap < date(2016, 10, 31) else "fail"  # D20: pre-2016-10 snapshots are known-incomplete
            out.append(Issue("massive", f"snapshot:{snap}", f"{n} names but only {nas} on NASDAQ (incomplete listing)", sev,
                             "D20: universe starts 2016-10-31" if sev == "warn" else ""))
    return out


# ---------------------------------------------------------------- runner
def run_all(sample: int = 20, seed: int = 7, log=print) -> bool:
    issues: list[Issue] = []
    log("check: holdout absent")
    issues += check_holdout_absent()
    if lake.exists(bars.DAILY):
        df = _daily()
        log(f"check: daily bars ({df.height} rows, {df['symbol'].n_unique()} symbols)")
        issues += check_daily_integrity(df) + check_daily_gaps(df) + check_daily_outliers(df)
        log(f"check: adjustments vs Yahoo ({sample} names)")
        issues += check_adjustment_second_source(df.filter(pl.col("date") < settings().holdout_start), sample, seed)
    else:
        issues.append(Issue("daily", "_dataset", "bars_daily not ingested"))
    log("check: minute, news, edgar, fred")
    issues += check_minute_coverage() + check_news() + check_edgar() + check_fred() + check_massive()
    issues = _explain(issues)
    unexplained = [i for i in issues if i.severity == "fail" and not i.explained]
    write_report(issues)
    log(f"quality: {len(issues)} issues, {len(unexplained)} unexplained failures")
    for i in unexplained[:40]:
        log(f"  FAIL {i.check} {i.key}: {i.detail}")
    return not unexplained


def write_report(issues: list[Issue]) -> None:
    d = settings().quality_dir
    d.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    lines = [f"# Data quality report {now:%Y-%m-%d %H:%M} UTC", ""]
    for sev in ("fail", "warn", "info"):
        group = [i for i in issues if i.severity == sev]
        lines += [f"## {sev} ({len(group)})", "", "| check | key | detail | explained |", "|---|---|---|---|"]
        lines += [f"| {i.check} | {i.key} | {i.detail} | {i.explained} |" for i in group]
        lines.append("")
    (d / f"{now:%Y-%m-%d}.md").write_text("\n".join(lines), encoding="utf-8")
