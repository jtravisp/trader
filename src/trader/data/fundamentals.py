"""Point-in-time fundamentals per filing, from XBRL company facts (edgar_facts).

Everything comes from what a filing itself reports, available at its EDGAR acceptance time
(known_ts). SEC "frame" de-duplication is not used: frames are attached to later re-reported
values, so they are not point-in-time.

Per (cik, accession) of a 10-Q / 10-K (or amendment):
  flows (revenue, net income, operating cash flow, diluted EPS):
    cur   = the filing's longest-duration fact ending at its latest period end (YTD, or FY)
    prior = the same-duration fact one year earlier, from the same filing (the comparative)
    ttm   = cur if cur is a fiscal year, else cur + last fiscal year (known by then) - prior
    growth = cur / prior - 1 (YTD year over year)
  instants (assets, liabilities, equity) at the filing's latest period end
  shares outstanding from the cover page (dei)
"""

from __future__ import annotations

import polars as pl

from trader.data import edgar, lake

FORMS = ["10-K", "10-Q", "10-K/A", "10-Q/A"]
REVENUE = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"]  # priority order
FLOWS = {"revenue": REVENUE, "net_income": ["NetIncomeLoss"], "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
         "eps": ["EarningsPerShareDiluted"]}
INSTANTS = {"assets": ["Assets"], "liabilities": ["Liabilities"], "equity": ["StockholdersEquity"]}
SHARES = "EntityCommonStockSharesOutstanding"


def _flow(f: pl.DataFrame, concepts: list[str], name: str) -> pl.DataFrame:
    x = f.filter(pl.col("concept").is_in(concepts) & pl.col("start").is_not_null()).with_columns(
        dur=(pl.col("end") - pl.col("start")).dt.total_days(),
        prio=pl.col("concept").replace_strict({c: i for i, c in enumerate(concepts)}, return_dtype=pl.Int32),
    )
    # one concept per filing (highest priority present)
    best = x.group_by("cik", "accession").agg(pl.col("prio").min().alias("p"))
    x = x.join(best, on=["cik", "accession"]).filter(pl.col("prio") == pl.col("p")).drop("p")
    cur = (x.sort("end", "dur", descending=True).group_by("cik", "accession", maintain_order=True).first()
           .select("cik", "accession", "known_ts", pl.col("start").alias("c_start"), pl.col("end").alias("c_end"),
                   pl.col("dur").alias("c_dur"), pl.col("val").alias("cur")))
    prior = x.join(cur.select("cik", "accession", "c_end", "c_dur"), on=["cik", "accession"]).filter(
        ((pl.col("c_end") - pl.col("end")).dt.total_days().is_between(350, 380)) & ((pl.col("dur") - pl.col("c_dur")).abs() <= 20)
    ).group_by("cik", "accession").agg(pl.col("val").first().alias("prior"))
    # last fiscal year known at this filing: a ~365-day fact ending just before the YTD start
    fy = x.filter(pl.col("dur").is_between(350, 380)).select(
        "cik", pl.col("end").alias("fy_end"), pl.col("val").alias("fy_prev"), pl.col("known_ts").alias("fy_known"))
    out = cur.join(prior, on=["cik", "accession"], how="left")
    cand = out.filter(pl.col("c_dur") < 350).select("cik", "accession", "c_start", "known_ts").join(fy, on="cik").filter(
        ((pl.col("c_start") - pl.col("fy_end")).dt.total_days().is_between(-5, 10)) & (pl.col("fy_known") <= pl.col("known_ts"))
    ).sort("fy_known").group_by("cik", "accession").agg(pl.col("fy_prev").last())
    out = out.join(cand, on=["cik", "accession"], how="left").with_columns(
        ttm=pl.when(pl.col("c_dur") >= 350).then(pl.col("cur")).otherwise(pl.col("cur") + pl.col("fy_prev") - pl.col("prior")),
        growth=pl.when(pl.col("prior").abs() > 0).then(pl.col("cur") / pl.col("prior") - 1),
    )
    return out.select("cik", "accession", "known_ts", pl.col("c_end").alias("period_end"),
                      pl.col("ttm").alias(f"{name}_ttm"), pl.col("growth").alias(f"{name}_growth"))


def _instant(f: pl.DataFrame, concepts: list[str], name: str) -> pl.DataFrame:
    x = f.filter(pl.col("concept").is_in(concepts))
    return x.sort("end", descending=True).group_by("cik", "accession", maintain_order=True).agg(
        pl.col("val").first().alias(name))


def per_filing(facts: pl.DataFrame) -> pl.DataFrame:
    """facts (edgar_facts rows) -> one row per (cik, accession) with point-in-time fundamentals."""
    f = facts.filter(pl.col("form").is_in(FORMS) & pl.col("accession").is_not_null())
    base = f.group_by("cik", "accession").agg(
        pl.col("known_ts").min(), pl.col("end").filter(pl.col("taxonomy") != "dei").max().alias("period_end"))
    for name, cs in FLOWS.items():
        base = base.join(_flow(f, cs, name).drop("known_ts", "period_end"), on=["cik", "accession"], how="left")
    for name, cs in INSTANTS.items():
        base = base.join(_instant(f, cs, name), on=["cik", "accession"], how="left")
    sh = facts.filter((pl.col("concept") == SHARES) & pl.col("accession").is_not_null()).sort("end", descending=True).group_by(
        "cik", "accession", maintain_order=True).agg(pl.col("val").first().alias("shares"))
    return base.join(sh, on=["cik", "accession"], how="left").sort("cik", "known_ts")


def load() -> pl.DataFrame:
    """Research read (pre-holdout rows only, via lake.scan)."""
    return per_filing(lake.scan(edgar.FACTS).collect())
