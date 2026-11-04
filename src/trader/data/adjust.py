"""Second-source adjustment factors from Massive splits + cash dividends (P3 adjustment rebuild).

Alpaca's `adjustment=all` bars carry known vendor defects (config/quality_exceptions.yaml: DOC
double dividends, BDX spin-off, AVGO/BG redomiciles). This module rebuilds total-return factors
from raw Alpaca closes and Massive corporate actions, then compares them with Alpaca's.

Backward adjustment (CRSP-style): for an event with ex/execution date e, every bar dated < e is
multiplied by
    split:     split_from / split_to
    dividend:  1 - cash / raw_close(last session before e)
so adj_close(t) = close(t) * prod(f_e for e > t). The last bar keeps its raw price.
"""

from __future__ import annotations

import polars as pl

from trader.config import ROOT
from trader.data import bars, lake, massive

REPORT = ROOT / "data" / "quality" / "adjustments_massive.md"


def events(raw: pl.DataFrame, splits: pl.DataFrame, divs: pl.DataFrame) -> pl.DataFrame:
    """(symbol, ex_date, f) multiplicative factor per event, from raw (symbol, date, close)."""
    s = splits.select(
        pl.col("ticker").alias("symbol"), pl.col("execution_date").str.to_date().alias("ex_date"),
        (pl.col("split_from") / pl.col("split_to")).alias("f"),
    ).filter(pl.col("f").is_finite() & (pl.col("f") > 0))
    d = divs.filter((pl.col("currency").fill_null("USD") == "USD") & (pl.col("cash_amount") > 0)).select(
        pl.col("ticker").alias("symbol"), pl.col("ex_dividend_date").str.to_date().alias("ex_date"), "cash_amount",
    ).unique(["symbol", "ex_date", "cash_amount"])  # same dividend under several vendor ids
    prev = raw.select("symbol", pl.col("date").alias("prev_date"), pl.col("close").alias("prev_close")).sort("symbol", "prev_date")
    d = d.sort("symbol", "ex_date").join_asof(prev, left_on="ex_date", right_on="prev_date", by="symbol",
                                              strategy="backward", allow_exact_matches=False)
    d = d.filter(pl.col("prev_close") > 0).with_columns(f=1 - pl.col("cash_amount") / pl.col("prev_close"))
    d = d.filter(pl.col("f") > 0).select("symbol", "ex_date", "f")
    return pl.concat([s, d]).group_by("symbol", "ex_date").agg(pl.col("f").product()).sort("symbol", "ex_date")


def adjusted(raw: pl.DataFrame, ev: pl.DataFrame) -> pl.DataFrame:
    """raw (symbol, date, close, ...) -> + factor, m_adj_close."""
    cum = ev.sort("symbol", "ex_date", descending=[False, True]).with_columns(
        cum=pl.col("f").cum_prod().over("symbol")
    ).sort("symbol", "ex_date")
    out = raw.sort("symbol", "date").join_asof(
        cum.select("symbol", "ex_date", "cum"), left_on="date", right_on="ex_date", by="symbol",
        strategy="forward", allow_exact_matches=False,
    )
    return out.with_columns(factor=pl.col("cum").fill_null(1.0)).with_columns(
        m_adj_close=pl.col("close") * pl.col("factor")
    ).drop("ex_date", "cum")


def compare(symbols: list[str] | None = None, log=print) -> pl.DataFrame:
    """Per symbol: max |log(ours/alpaca)| after normalizing both to the last bar."""
    lf = lake.scan(bars.DAILY).select("symbol", "date", "close", "adj_close")
    if symbols:
        lf = lf.filter(pl.col("symbol").is_in(symbols))
    raw = lf.collect()
    ev = events(raw, lake.scan(massive.SPLITS).collect(), lake.scan(massive.DIVIDENDS).collect())
    a = adjusted(raw, ev).filter((pl.col("adj_close") > 0) & (pl.col("m_adj_close") > 0)).with_columns(
        r=(pl.col("m_adj_close") / pl.col("adj_close")).log()
    ).with_columns(r=pl.col("r") - pl.col("r").sort_by("date").last().over("symbol"))
    res = a.group_by("symbol").agg(
        pl.col("r").abs().max().alias("max_abs_log_diff"),
        pl.col("date").sort_by(pl.col("r").abs()).last().alias("worst_date"),
        pl.len().alias("n"),
    ).sort("max_abs_log_diff", descending=True)
    n = res.height
    b1, b5 = (res["max_abs_log_diff"] > 0.01).sum(), (res["max_abs_log_diff"] > 0.05).sum()
    L = ["# Adjustment factors: Massive rebuild vs Alpaca adjustment=all", "",
         f"{n} symbols; max |log diff| > 1%: {b1} ({b1 / max(n, 1):.1%}); > 5%: {b5} ({b5 / max(n, 1):.1%})", "",
         "Disagreement means one vendor missed or doubled an event (or a symbol's history spans two companies).", "",
         "| symbol | max abs log diff | worst date | bars |", "|---|---|---|---|"]
    L += [f"| {s} | {x:.3f} | {d} | {k} |" for s, x, d, k in res.head(60).iter_rows()]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    log(f"adjustments: {n} symbols, >1%: {b1}, >5%: {b5}; report {REPORT}")
    return res
