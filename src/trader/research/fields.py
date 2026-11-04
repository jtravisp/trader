"""Attach point-in-time matrices (fundamentals, FRED regime) to a Panel (I/O lives here, not in features).

Alignment rule (same as the S04 events): a value with known_ts k is first usable at row t, the
first session whose close is at or after k. Values are carried forward until superseded. Trading on
row t happens at the next open, so nothing is used before it was published.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import polars as pl

from trader import calendar
from trader.backtest.panel import Panel

FUND_COLS = ["revenue_ttm", "revenue_growth", "net_income_ttm", "net_income_growth", "cfo_ttm", "eps_ttm", "eps_growth",
             "assets", "liabilities", "equity"]
FRED_SERIES = ["VIXCLS", "T10Y2Y", "T10Y3M", "BAA10Y", "AAA10Y", "DGS10"]
MAX_AGE = 400  # sessions; a filing older than this is treated as missing (company stopped filing)


def _rows(p: Panel, known: pl.Series) -> np.ndarray:
    """Row index of the first session whose close >= known_ts (-1 if outside the panel)."""
    sess = calendar.sessions_frame().select("date", "close_ts").sort("close_ts")
    k = pl.DataFrame({"known_ts": known, "i": np.arange(len(known))}).sort("known_ts")
    j = k.join_asof(sess, left_on="known_ts", right_on="close_ts", strategy="forward").sort("i")
    row = {d: i for i, d in enumerate(p.dates.astype("O"))}
    return np.array([row.get(d, -1) if d is not None else -1 for d in j["date"]])


def _ffill(m: np.ndarray) -> np.ndarray:
    T = m.shape[0]
    idx = np.where(np.isfinite(m), np.arange(T)[:, None], 0)
    np.maximum.accumulate(idx, axis=0, out=idx)
    return m[idx, np.arange(m.shape[1])]


def asof_matrices(p: Panel, df: pl.DataFrame, key_cols: dict, cols: list[str]) -> dict[str, np.ndarray]:
    """df: (key, known_ts, *cols) -> {col: T x N}; key_cols maps key -> list of panel columns."""
    df = df.filter(pl.col("key").is_in(list(key_cols))).sort("known_ts")
    r = _rows(p, df["known_ts"])
    out = {}
    age = np.full((p.T, p.N), np.nan)
    keys = df["key"].to_list()
    for c in [*cols, None]:
        m = np.full((p.T, p.N), np.nan)
        vals = df[c].to_numpy().astype(float) if c else np.zeros(len(keys))
        for k, i, v in zip(keys, r, vals, strict=True):
            if i >= 0:
                m[i, key_cols[k]] = v if c else i
        if c is None:
            age = np.arange(p.T)[:, None] - _ffill(m)
        else:
            out[c] = m
    stale = ~(age <= MAX_AGE)
    for c in cols:
        out[c] = np.where(stale, np.nan, _ffill(out[c]))
    out["filing_age"] = np.where(stale, np.nan, age)
    return out


def cik_columns(p: Panel) -> dict[int, list[int]]:
    col = {s: j for j, s in enumerate(p.symbols)}
    out: dict[int, list[int]] = {}
    for lid, sym in p.meta.get("lines", {}).items():
        c = lid.rsplit("-", 1)[0]
        if c.isdigit() and sym in col:
            out.setdefault(int(c), []).append(col[sym])
    return out


def fundamentals(p: Panel, per_filing: pl.DataFrame) -> dict[str, np.ndarray]:
    """Fundamental matrices + `adj_shares` (shares x raw/adjusted price at the filing row) so that
    market cap at t = adj_shares * adjusted close(t), robust to later splits."""
    kc = cik_columns(p)
    df = per_filing.rename({"cik": "key"}).filter(pl.col("known_ts").is_not_null() & pl.col("period_end").is_not_null())
    # an amendment for an older period must not overwrite newer data
    df = df.sort("key", "known_ts").filter(pl.col("period_end") >= pl.col("period_end").cum_max().over("key"))
    m = asof_matrices(p, df, kc, FUND_COLS)
    # shares: scale at the filing row by raw/adjusted close so later splits don't distort market cap
    sh = asof_matrices(p, df.filter(pl.col("shares") > 0), kc, ["shares"])["shares"]
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = p.raw_close / p.close
    # ratio at the filing row, carried forward with the shares value
    rr = np.full((p.T, p.N), np.nan)
    df2 = df.filter(pl.col("shares") > 0)
    rows = _rows(p, df2["known_ts"])
    for k, i in zip(df2["key"].to_list(), rows, strict=True):
        if i >= 0:
            for j in kc.get(k, []):
                rr[i, j] = ratio[i, j]
    m["adj_shares"] = sh * _ffill(rr)
    return m


def fred(p: Panel, series: pl.DataFrame) -> dict[str, np.ndarray]:
    """Market-wide FRED series broadcast to T x N (known_ts aligned like everything else)."""
    out = {}
    for s in FRED_SERIES:
        x = series.filter(pl.col("series") == s).select("known_ts", "value").drop_nulls()
        v = np.full(p.T, np.nan)
        r = _rows(p, x["known_ts"])
        for i, val in zip(r, x["value"].to_numpy(), strict=True):
            if i >= 0:
                v[i] = val
        v = _ffill(v[:, None])[:, 0]
        out[f"fred_{s}"] = np.repeat(v[:, None], p.N, axis=1)
    return out


def attach(p: Panel, log=print) -> Panel:
    """Research loader: fundamentals for PIT-universe columns, FRED regime for all."""
    from trader.data import fred as fred_ds
    from trader.data import fundamentals as fd
    from trader.data import lake

    f = {}
    if p.meta.get("lines"):
        pf = fd.load()
        log(f"fields: fundamentals from {pf.height} filings")
        f.update(fundamentals(p, pf))
    f.update(fred(p, lake.scan(fred_ds.FRED).collect()))
    return replace(p, fields={**p.fields, **f})
