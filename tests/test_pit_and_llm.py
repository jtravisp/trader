from datetime import UTC, date, datetime

import polars as pl

from trader import calendar
from trader.data.edgar import _acceptance_to_utc
from trader.llm import schema
from trader.llm.budget import consecutive
from trader.llm.scorer import cost


def test_early_close_known_ts():
    # Day after Thanksgiving 2019 closed at 13:00 New York = 18:00 UTC
    assert calendar.session_close(date(2019, 11, 29)) == datetime(2019, 11, 29, 18, 0, tzinfo=UTC)


def test_edgar_acceptance_is_utc():
    # Real Apple Item 2.02 8-Ks, released 16:30 New York: 20:30Z (EDT) and 21:30Z (EST)
    df = pl.DataFrame({"a": ["2024-05-02T20:30:34.000Z", "2024-02-01T21:30:30.000Z"]})
    out = df.select(_acceptance_to_utc("a").dt.convert_time_zone("America/New_York").alias("t"))["t"]
    assert [(t.hour, t.minute) for t in out] == [(16, 30), (16, 30)]


GOOD = (
    '{"event_type":"earnings","is_new_information":true,"horizon_days":5,"risk_flags":["none"],'
    '"scores":[{"symbol":"AAPL","direction":0.4,"magnitude":0.3,"confidence":0.7}]}'
)


def test_schema_accepts_valid():
    assert schema.parse(GOOD, {"AAPL"}) is not None


def test_schema_drops_invented_symbol_and_out_of_range():
    assert schema.parse(GOOD, {"MSFT"}) is None
    assert schema.parse(GOOD.replace("0.4", "1.4"), {"AAPL"}) is None
    assert schema.parse(GOOD.replace('"earnings"', '"buy_now"'), {"AAPL"}) is None
    assert schema.parse("not json", {"AAPL"}) is None


def test_json_schema_matches_pydantic_fields():
    js = schema.json_schema()
    assert set(js["required"]) == set(schema.NewsScore.model_fields)


def test_cost_batch_discount():
    assert abs(cost("claude-haiku-5-5", 1_000_000, 0) - 0.05) < 1e-12
    assert abs(cost("claude-haiku-5-5", 0, 1_000_000, batch=False) - 0.50) < 1e-12


def test_consecutive_days():
    assert consecutive(["2026-11-03", "2026-11-04", "2026-11-05"]) == 3
    assert consecutive(["2026-11-01", "2026-11-03", "2026-11-04"]) == 2
    assert consecutive([]) == 0
