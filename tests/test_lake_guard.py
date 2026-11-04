from dataclasses import replace
from datetime import UTC, date, datetime

import polars as pl
import pytest

from trader.config import settings
from trader.data import lake


@pytest.fixture()
def tmp_lake(tmp_path, monkeypatch):
    s = replace(settings(), lake_dir=tmp_path / "lake")
    monkeypatch.setattr(lake, "settings", lambda: s)
    monkeypatch.setattr(lake, "HOLDOUT_LOG", tmp_path / "holdout.jsonl")
    return s


def _df(ts_list):
    ts = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in ts_list]
    return pl.DataFrame({"k": list(range(len(ts))), "event_ts": ts, "known_ts": ts})


def test_write_refuses_holdout_rows(tmp_lake):
    df = _df(["2025-10-31T20:00:00", "2026-03-02T20:00:00"])
    with pytest.raises(lake.HoldoutViolation):
        lake.write(df, "t", key=["k"])
    assert not lake.exists("t")


def test_drop_holdout_then_write(tmp_lake):
    df = _df(["2025-10-31T20:00:00", "2026-03-02T20:00:00", "2026-11-02T21:00:00"])
    kept, dropped = lake.drop_holdout(df)
    assert dropped == 1 and kept.height == 2
    lake.write(kept, "t", key=["k"])
    # research reads exclude forward data too; forward needs an explicit flag
    assert lake.scan("t").collect().height == 1
    assert lake.scan("t", allow_forward=True).collect().height == 2


def test_holdout_boundary_is_midnight_new_york(tmp_lake):
    # 2025-11-01 03:59 UTC is still Oct 31 in New York -> allowed; 04:00 UTC is holdout.
    ok = _df(["2025-11-01T03:59:59"])
    lake.write(ok, "b", key=["k"])
    with pytest.raises(lake.HoldoutViolation):
        lake.write(_df(["2025-11-01T04:00:00"]), "b2", key=["k"])


def test_unlock_requires_reason_and_logs(tmp_lake):
    lake.write(_df(["2020-01-02T21:00:00"]), "t", key=["k"])
    with pytest.raises(lake.HoldoutViolation):
        lake.scan("t", unlock_holdout=True)
    lake.scan("t", unlock_holdout=True, reason="P7 approved by user (test)")
    assert lake.HOLDOUT_LOG.exists()


def test_naive_timestamps_rejected(tmp_lake):
    df = pl.DataFrame({"k": [1], "event_ts": [datetime(2020, 1, 2)], "known_ts": [datetime(2020, 1, 2)]})  # noqa: DTZ001
    with pytest.raises(ValueError):
        lake.write(df, "t", key=["k"])


def test_upsert_is_idempotent(tmp_lake):
    df = _df(["2020-01-02T21:00:00", "2020-01-03T21:00:00"])
    lake.write(df, "t", key=["k"])
    lake.write(df, "t", key=["k"])
    assert lake.scan("t").collect().height == 2


def test_clip_window(tmp_lake):
    w = lake.clip_window(date(2025, 10, 1), date(2026, 11, 5))
    assert w == [(date(2025, 10, 1), date(2025, 11, 1)), (date(2026, 11, 1), date(2026, 11, 5))]
    assert lake.clip_window(date(2026, 1, 1), date(2026, 2, 1)) == []


def test_bar_frames_never_truncate_prices_after_integer_rows(monkeypatch):
    from datetime import date

    from trader.data import bars

    page = {"bars": {"X": [{"t": f"2020-01-{1 + i % 28:02d}T05:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 10, "n": 1, "vw": 1}
                           for i in range(150)] + [{"t": "2020-02-03T05:00:00Z", "o": 0.97, "h": 1.03, "l": 0.9, "c": 0.97,
                                                    "v": 10, "n": 1, "vw": 0.98}]}}
    monkeypatch.setattr(bars, "paginate", lambda *a, **k: iter([page]))
    monkeypatch.setattr(bars, "alpaca_data", lambda: None)

    class A:
        def append(self, *a):
            pass

    df = bars._fetch(["X"], "1Day", date(2020, 1, 1), date(2020, 3, 1), "raw", A())
    assert df["c"].to_list()[-1] == 0.97
