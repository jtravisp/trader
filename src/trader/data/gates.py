"""P1 gate checks."""

from __future__ import annotations

from datetime import date

import polars as pl
import yaml

from trader.config import ROOT
from trader.data import bars, lake


def spy_total_return_check(log=print) -> tuple[bool, pl.DataFrame]:
    ref = yaml.safe_load((ROOT / "config" / "reference_returns.yaml").read_text(encoding="utf-8"))
    tol = float(ref["tolerance_per_year"])
    years = sorted(ref["SPY"])
    df = lake.scan(bars.DAILY).filter(pl.col("symbol") == "SPY").select("date", "adj_close").collect().sort("date")
    if df.is_empty():
        raise RuntimeError("no SPY bars in the lake; run `trader ingest bars` first")
    if df["date"].min() > date(years[0] - 1, 12, 31):
        raise RuntimeError(f"no base bar for {years[0]}: lake starts {df['date'].min()}")
    ye = df.group_by(pl.col("date").dt.year().alias("year")).agg(pl.col("adj_close").last().alias("close")).sort("year")
    ye = ye.with_columns((pl.col("close") / pl.col("close").shift(1) - 1).alias("lake_return"))
    out = (
        pl.DataFrame({"year": years, "published": [float(ref["SPY"][y]) for y in years]}, schema_overrides={"year": pl.Int32})
        .join(ye.with_columns(pl.col("year").cast(pl.Int32)), on="year", how="left")
        .with_columns((pl.col("lake_return") - pl.col("published")).alias("diff"))
        .with_columns((pl.col("diff").abs() <= tol).alias("pass"))
        .select("year", "published", "lake_return", "diff", "pass")
    )
    ok = bool(out["pass"].all()) and out["lake_return"].null_count() == 0
    return ok, out
