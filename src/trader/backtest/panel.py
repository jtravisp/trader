"""Dense date x symbol price panel for the backtester (research data only, pre-holdout)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import polars as pl

from trader.data import bars, lake


@dataclass(frozen=True)
class Panel:
    dates: np.ndarray  # datetime64[D], length T
    symbols: list[str]  # length N
    open: np.ndarray  # T x N adjusted open, NaN where not trading
    close: np.ndarray  # T x N adjusted close
    raw_close: np.ndarray  # T x N unadjusted close (price filters)
    dollar_volume: np.ndarray  # T x N raw close * volume
    is_etf: np.ndarray  # N bool
    meta: dict = field(default_factory=dict)

    @property
    def T(self) -> int:
        return len(self.dates)

    @property
    def N(self) -> int:
        return len(self.symbols)

    def truncate(self, t: int) -> Panel:
        """Rows 0..t inclusive: everything knowable at the close of day t."""
        s = slice(0, t + 1)
        return Panel(self.dates[s], self.symbols, self.open[s], self.close[s], self.raw_close[s],
                     self.dollar_volume[s], self.is_etf, self.meta)

    def col(self, symbol: str) -> int:
        return self.symbols.index(symbol)

    def returns(self) -> np.ndarray:
        """Close-to-close adjusted returns; row 0 is NaN."""
        r = np.full_like(self.close, np.nan)
        r[1:] = self.close[1:] / self.close[:-1] - 1.0
        return r


def load_panel(symbols: Sequence[str] | None = None, start: date | None = None, end: date | None = None) -> Panel:
    lf = lake.scan(bars.DAILY)  # research read: pre-holdout only
    if symbols is not None:
        lf = lf.filter(pl.col("symbol").is_in(list(symbols)))
    if start is not None:
        lf = lf.filter(pl.col("date") >= start)
    if end is not None:
        lf = lf.filter(pl.col("date") < end)
    return panel_from_frame(lf.select("symbol", "date", "adj_open", "adj_close", "close", "volume").collect())


def panel_from_frame(df: pl.DataFrame) -> Panel:
    """Long frame (symbol, date, adj_open, adj_close, close, volume) -> dense Panel."""
    from trader.universe.build import etf_list

    syms = sorted(df["symbol"].unique().to_list())
    dates = sorted(df["date"].unique().to_list())

    def wide(col: str) -> np.ndarray:
        w = df.pivot(on="symbol", index="date", values=col).sort("date")
        w = w.select(["date", *[s for s in syms if s in w.columns]])
        return w.drop("date").to_numpy().astype(float)

    o, c, rc, v = wide("adj_open"), wide("adj_close"), wide("close"), wide("volume")
    etfs = set(etf_list())
    return Panel(
        dates=np.array(dates, dtype="datetime64[D]"),
        symbols=syms,
        open=o,
        close=c,
        raw_close=rc,
        dollar_volume=rc * v,
        is_etf=np.array([s in etfs for s in syms]),
        meta={"start": str(dates[0]), "end": str(dates[-1])},
    )
