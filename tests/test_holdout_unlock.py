from datetime import UTC, date, datetime

import polars as pl
import pytest

from trader.config import settings
from trader.data import lake


def _frame(d: date) -> pl.DataFrame:
    ts = datetime(d.year, d.month, d.day, 21, tzinfo=UTC)
    return pl.DataFrame({"symbol": ["X"], "event_ts": [ts], "known_ts": [ts]})


def test_locked_by_default():
    s = settings()
    assert lake.horizon() == s.holdout_start
    inside = _frame(s.holdout_start.replace(day=15))
    assert lake.drop_holdout(inside)[1] == 1
    assert lake.clip_window(s.history_start, s.holdout_end) == [(s.history_start, s.holdout_start)]
    with pytest.raises(lake.HoldoutViolation):
        lake.write(inside, "unit_test_never_written", key=["symbol"])


def test_unlock_needs_reason():
    with pytest.raises(lake.HoldoutViolation), lake.holdout_unlocked(""):
        pass


def test_unlock_is_scoped_and_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(lake, "HOLDOUT_LOG", tmp_path / "access.jsonl")
    s = settings()
    inside = _frame(s.holdout_start.replace(day=15))
    with lake.holdout_unlocked("unit test"):
        assert lake.horizon() == s.holdout_end
        assert lake.drop_holdout(inside)[1] == 0
        assert lake.clip_window(s.history_start, s.holdout_end) == [(s.history_start, s.holdout_end)]
    assert lake.horizon() == s.holdout_start  # relocked on exit
    log = (tmp_path / "access.jsonl").read_text(encoding="utf-8")
    assert '"unlock"' in log and '"relock"' in log and "unit test" in log
