"""NYSE trading calendar helpers (America/New_York logic, UTC storage)."""

from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache

import exchange_calendars as xcals
import polars as pl


@lru_cache(maxsize=1)
def _cal() -> xcals.ExchangeCalendar:
    return xcals.get_calendar("XNYS", start="2005-01-01")


@lru_cache(maxsize=1)
def sessions_frame() -> pl.DataFrame:
    """One row per NYSE session: date, open_ts, close_ts (UTC)."""
    sched = _cal().schedule
    return pl.DataFrame(
        {
            "date": [d.date() for d in sched.index],
            "open_ts": [t.to_pydatetime() for t in sched["open"]],
            "close_ts": [t.to_pydatetime() for t in sched["close"]],
        }
    ).with_columns(pl.col("open_ts").dt.convert_time_zone("UTC"), pl.col("close_ts").dt.convert_time_zone("UTC"))


def sessions(start: date, end: date) -> list[date]:
    """Sessions with start <= d < end."""
    f = sessions_frame().filter((pl.col("date") >= start) & (pl.col("date") < end))
    return f["date"].to_list()


def is_session(d: date) -> bool:
    return _cal().is_session(d.isoformat())


def previous_session(d: date) -> date:
    return _cal().date_to_session(d.isoformat(), direction="previous").date() if not is_session(d) else (
        _cal().previous_session(d.isoformat()).date()
    )


def next_session(d: date) -> date:
    return _cal().date_to_session(d.isoformat(), direction="next").date() if not is_session(d) else (
        _cal().next_session(d.isoformat()).date()
    )


def session_close(d: date) -> datetime:
    return _cal().session_close(d.isoformat()).to_pydatetime()


def session_open(d: date) -> datetime:
    return _cal().session_open(d.isoformat()).to_pydatetime()
