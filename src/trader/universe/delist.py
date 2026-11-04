"""Delisting returns for lines that leave the listings for good (D24; fixes the D21 'exit flat' bias).

A line whose last bar is before the end of the research window gets a delisting return, applied
by the engine on the first missing session. It is classified from the company's own EDGAR filings
around the exit (window: 365 days before to 120 days after the last listing):
  spac        blank-check company (SEC SIC 6770, or the name says so): liquidates at trust value; 0
  merger      target-side merger/tender filings (DEFM14A/PREM14A/DEFM14C/PREM14C/SC 14D9/SC 13E3/425)
              or an 8-K with Item 5.01 (change in control); delisting return 0 (the last price
              reflects the deal)
  continuing  the same CIK is still listed under another line after the exit (rename/exchange move
              the stitcher missed); 0
  bankruptcy  an 8-K with Item 1.03; performance-delisting return
  distressed  no merger evidence and last close < $5, or down >= 50% over the last 120 sessions;
              performance-delisting return
  unclassified everything else; 0, counted in reports
Performance-delisting returns: Shumway (1997), NYSE/AMEX -30%; Shumway & Warther (1999),
NASDAQ -55%.
"""

from __future__ import annotations

from datetime import timedelta

import polars as pl

MERGER_FORMS = ["DEFM14A", "PREM14A", "DEFM14C", "PREM14C", "SC 14D9", "SC 13E3", "SC14D9C", "425"]
SPAC_NAME = r"(?i)\bacquisition\b|\bSPAC\b|blank check"
SPAC_SIC = "6770"  # SEC: blank checks
SHUMWAY = {"XNAS": -0.55}
SHUMWAY_DEFAULT = -0.30


def classify(exits: pl.DataFrame, filings: pl.DataFrame, cik_last: pl.DataFrame, sic: pl.DataFrame | None = None) -> pl.DataFrame:
    """exits: (line_id, cik:int|null, ticker, name, exit_date, exchange, last_close, ret_120)
    filings: (cik, form, items, filing_date); cik_last: (cik, cik_last_listed) over all lines;
    sic: (cik, sic) current SEC metadata (a de-SPAC'd company has its new SIC, a liquidated SPAC keeps 6770).
    Returns exits + category, delist_ret."""
    f = filings.select("cik", "form", "items", "filing_date")
    j = exits.filter(pl.col("cik").is_not_null()).join(f, on="cik", how="inner").filter(
        pl.col("filing_date").is_between(pl.col("exit_date") - timedelta(days=365), pl.col("exit_date") + timedelta(days=120))
    )
    flags = j.group_by("line_id").agg(
        (pl.col("form").is_in(MERGER_FORMS) | ((pl.col("form") == "8-K") & pl.col("items").str.contains("5.01", literal=True))).any().alias("m"),
        ((pl.col("form") == "8-K") & pl.col("items").str.contains("1.03", literal=True)).any().alias("b"),
    )
    sic = sic if sic is not None else pl.DataFrame(schema={"cik": pl.Int64, "sic": pl.Utf8})
    x = exits.join(flags, on="line_id", how="left").join(cik_last, on="cik", how="left").join(
        sic.select("cik", "sic"), on="cik", how="left").with_columns(
        pl.col("m").fill_null(False), pl.col("b").fill_null(False))
    distressed = (pl.col("last_close") < 5) | (pl.col("ret_120") <= -0.5)
    cont = (pl.col("cik_last_listed") > pl.col("exit_date") + timedelta(days=40)).fill_null(False)
    cat = (pl.when(cont).then(pl.lit("continuing"))
           .when(pl.col("name").fill_null("").str.contains(SPAC_NAME) | (pl.col("sic") == SPAC_SIC)).then(pl.lit("spac"))
           .when(pl.col("m")).then(pl.lit("merger"))
           .when(pl.col("b")).then(pl.lit("bankruptcy"))
           .when(distressed.fill_null(False)).then(pl.lit("distressed"))
           .otherwise(pl.lit("unclassified")))
    perf = pl.col("exchange").replace_strict(SHUMWAY, default=SHUMWAY_DEFAULT, return_dtype=pl.Float64)
    return x.with_columns(category=cat).with_columns(
        delist_ret=pl.when(pl.col("category").is_in(["bankruptcy", "distressed"])).then(perf).otherwise(0.0)
    ).drop("m", "b", "cik_last_listed", "sic")
