"""Research universe seed (P1).

Seed = top N US common stocks by median daily dollar volume over the `rank_window_days`
sessions ending just before the holdout, among Alpaca's currently active, tradable assets.
This seed is SURVIVORSHIP-BIASED (names delisted before today are missing); see
`trader.universe.survivorship` and D3. The point-in-time monthly universe is built in P3.

Output: config/universe_seed.csv (committed, so research is reproducible).
"""

from __future__ import annotations

import re
from datetime import timedelta

import polars as pl

from trader import calendar
from trader.config import ROOT, settings, universe_config
from trader.data import edgar
from trader.data.bars import _fetch
from trader.data.http import RawArchive, alpaca_paper_readonly

SEED_PATH = ROOT / "config" / "universe_seed.csv"
FUND_NAME = re.compile(
    r"\b(ETF|ETN|ETP|FUND|TRUST UNITS?|INDEX|ISHARES|SPDR|PROSHARES|INVESCO|VANECK|DIREXION|"
    r"WISDOMTREE|GLOBAL X|SPROTT|GRAYSCALE|LEVERAGED|INVERSE|ACQUISITION CORP|ACQUISITION CORPORATION|"
    r"WARRANTS?|RIGHTS?|UNITS?|PREFERRED|DEPOSITARY SHARES REPRESENTING|NOTES DUE|BOND)\b",
    re.I,
)
# SIC codes to exclude: 6221 commodity contracts, 6770 blank checks (SPACs), 6726 investment trusts.
EXCLUDE_SIC = {"6221", "6770", "6726", "6722"}


def active_assets() -> pl.DataFrame:
    http = alpaca_paper_readonly()
    data = http.get_json("/v2/assets", {"status": "active", "asset_class": "us_equity"})
    RawArchive("alpaca_assets", "active").append({"status": "active"}, data)
    return pl.DataFrame(data, infer_schema_length=None)


def inactive_assets() -> pl.DataFrame:
    http = alpaca_paper_readonly()
    data = http.get_json("/v2/assets", {"status": "inactive", "asset_class": "us_equity"})
    RawArchive("alpaca_assets", "inactive").append({"status": "inactive"}, data)
    return pl.DataFrame(data, infer_schema_length=None)


def build_seed(log=print) -> pl.DataFrame:
    cfg = universe_config()["stocks"]
    s = settings()
    etfs = set(universe_config()["etfs"])
    a = active_assets()
    a = a.filter(
        pl.col("tradable")
        & pl.col("exchange").is_in(cfg["exchanges"])
        & pl.col("symbol").str.contains(r"^[A-Z]{1,5}(\.[A-Z])?$")
        & ~pl.col("symbol").is_in(list(etfs))
        & ~pl.col("name").map_elements(lambda n: bool(FUND_NAME.search(n or "")), return_dtype=pl.Boolean)
    )
    log(f"candidates after asset filters: {a.height}")

    end = s.holdout_start
    sess = calendar.sessions(end - timedelta(days=cfg["rank_window_days"] * 2), end)[-cfg["rank_window_days"] :]
    start = sess[0]
    archive = RawArchive("alpaca_bars_daily", f"universe_rank_{start}_{end}")
    syms = a["symbol"].to_list()
    frames = []
    for i in range(0, len(syms), 200):
        df = _fetch(syms[i : i + 200], "1Day", start, end, "raw", archive)
        if not df.is_empty():
            frames.append(df.select("symbol", pl.col("c").cast(pl.Float64), pl.col("v").cast(pl.Float64)))
    bars = pl.concat(frames)
    rank = (
        bars.group_by("symbol")
        .agg(
            (pl.col("c") * pl.col("v")).median().alias("med_dollar_vol"),
            pl.col("c").last().alias("last_close"),
            pl.len().alias("n_bars"),
        )
        .filter((pl.col("n_bars") >= int(0.9 * len(sess))) & (pl.col("last_close") >= cfg["min_price"]))
        .sort("med_dollar_vol", descending=True)
        .head(cfg["candidate_count"])
    )
    log(f"ranked candidates: {rank.height}")

    # SEC filter: must be an operating company filer with an allowed SIC.
    tick = edgar.ticker_map()
    rank = rank.join(tick, left_on="symbol", right_on="ticker", how="left")
    meta = edgar.company_meta(rank.drop_nulls("cik")["cik"].unique().to_list(), log=log)
    rank = rank.join(meta, on="cik", how="left")
    keep = rank.filter(
        pl.col("cik").is_not_null()
        & (pl.col("entity_type").fill_null("") == "operating")
        & ~pl.col("sic").fill_null("").is_in(list(EXCLUDE_SIC))
    )
    log(f"dropped by SEC filter: {rank.height - keep.height} (no CIK / non-operating / fund SIC)")
    seed = (
        keep.head(cfg["target_count"])
        .with_row_index("rank", offset=1)
        .join(a.select("symbol", "name", "exchange"), on="symbol", how="left")
        .select("rank", "symbol", "cik", "name", "exchange", "sic", "med_dollar_vol", "last_close")
    )
    seed.write_csv(SEED_PATH)
    log(f"wrote {seed.height} names to {SEED_PATH.relative_to(ROOT)} (rank window {start}..{sess[-1]})")
    return seed


def load_seed() -> pl.DataFrame:
    return pl.read_csv(SEED_PATH, schema_overrides={"cik": pl.Int64, "sic": pl.Utf8})


def etf_list() -> list[str]:
    return list(universe_config()["etfs"])
