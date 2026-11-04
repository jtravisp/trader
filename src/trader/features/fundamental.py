"""Fundamental and macro-regime features from Panel.fields (pure; see research/fields.py)."""

from __future__ import annotations

import numpy as np

from trader.backtest.panel import Panel


def _div(a: np.ndarray, b: np.ndarray, positive: bool = True) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        out = a / b
    if positive:
        out = np.where(b > 0, out, np.nan)
    return np.where(np.isfinite(out), out, np.nan)


def build(panel: Panel) -> dict[str, np.ndarray]:
    f = panel.fields
    out: dict[str, np.ndarray] = {}
    if "adj_shares" in f:
        mcap = f["adj_shares"] * panel.close
        out |= {
            "log_mcap": np.log(np.where(mcap > 0, mcap, np.nan)),
            "earnings_yield": _div(f["net_income_ttm"], mcap),
            "book_to_market": _div(f["equity"], mcap),
            "sales_to_price": _div(f["revenue_ttm"], mcap),
            "roa": _div(f["net_income_ttm"], f["assets"]),
            "accruals": _div(f["net_income_ttm"] - f["cfo_ttm"], f["assets"]),
            "leverage": _div(f["liabilities"], f["assets"]),
            "revenue_growth": np.clip(f["revenue_growth"], -1, 5),
            "eps_growth": np.clip(f["eps_growth"], -5, 5),
            "filing_age": f["filing_age"],
        }
    if "fred_VIXCLS" in f:
        out |= {
            "vix": f["fred_VIXCLS"], "curve_10y2y": f["fred_T10Y2Y"], "curve_10y3m": f["fred_T10Y3M"],
            "credit_baa_aaa": f["fred_BAA10Y"] - f["fred_AAA10Y"], "rate_10y": f["fred_DGS10"],
        }
    return out
