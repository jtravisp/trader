from datetime import UTC, date, datetime

import polars as pl

from trader.data.fundamentals import per_filing


def _row(acc, concept, start, end, val, form, known):
    return {"cik": 1, "taxonomy": "us-gaap", "concept": concept, "unit": "USD", "start": start, "end": end, "val": val,
            "accession": acc, "form": form, "known_ts": known}


def test_ttm_and_growth_from_one_filing():
    k1, k2 = datetime(2024, 2, 1, tzinfo=UTC), datetime(2024, 8, 1, tzinfo=UTC)
    rows = [
        # 10-K FY2023: revenue 400 (FY2022 comparative 360)
        _row("A1", "Revenues", date(2023, 1, 1), date(2023, 12, 31), 400.0, "10-K", k1),
        _row("A1", "Revenues", date(2022, 1, 1), date(2022, 12, 31), 360.0, "10-K", k1),
        _row("A1", "Assets", None, date(2023, 12, 31), 1000.0, "10-K", k1),
        # 10-Q H1 2024: YTD 220 vs H1 2023 190; Q2 alone 115 vs 100
        _row("A2", "Revenues", date(2024, 1, 1), date(2024, 6, 30), 220.0, "10-Q", k2),
        _row("A2", "Revenues", date(2023, 1, 1), date(2023, 6, 30), 190.0, "10-Q", k2),
        _row("A2", "Revenues", date(2024, 4, 1), date(2024, 6, 30), 115.0, "10-Q", k2),
        _row("A2", "Revenues", date(2023, 4, 1), date(2023, 6, 30), 100.0, "10-Q", k2),
        _row("A2", "Assets", None, date(2024, 6, 30), 1100.0, "10-Q", k2),
        _row("A2", "Assets", None, date(2023, 12, 31), 1000.0, "10-Q", k2),
    ]
    out = per_filing(pl.DataFrame(rows)).sort("known_ts")
    a1, a2 = out.row(0, named=True), out.row(1, named=True)
    assert a1["revenue_ttm"] == 400 and abs(a1["revenue_growth"] - (400 / 360 - 1)) < 1e-12 and a1["assets"] == 1000
    assert a2["revenue_ttm"] == 220 + 400 - 190  # YTD + last FY - prior YTD
    assert abs(a2["revenue_growth"] - (220 / 190 - 1)) < 1e-12 and a2["assets"] == 1100
    assert a2["period_end"] == date(2024, 6, 30)


def test_fiscal_year_not_yet_known_gives_no_ttm():
    k = datetime(2024, 5, 1, tzinfo=UTC)
    rows = [_row("B", "Revenues", date(2024, 1, 1), date(2024, 3, 31), 100.0, "10-Q", k),
            _row("B", "Revenues", date(2023, 1, 1), date(2023, 3, 31), 90.0, "10-Q", k),
            # FY2023 10-K accepted AFTER this 10-Q: must not be used
            _row("C", "Revenues", date(2023, 1, 1), date(2023, 12, 31), 400.0, "10-K", datetime(2024, 6, 1, tzinfo=UTC))]
    out = per_filing(pl.DataFrame(rows)).filter(pl.col("accession") == "B")
    assert out["revenue_ttm"][0] is None
