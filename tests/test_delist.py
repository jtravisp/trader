from datetime import date

import polars as pl

from trader.universe import delist
from trader.universe.pit import _tokens, names_match


def test_names_match_handles_renames_and_rejects_vendor_errors():
    assert names_match(_tokens("QuidelOrtho Corporation Common Stock"), _tokens("QUIDEL CORP /DE/"))
    assert names_match(_tokens("Meta Platforms, Inc. Class A"), _tokens("Meta Platforms, Inc.|FACEBOOK INC"))
    assert not names_match(_tokens("Yahoo Inc"), _tokens("FIELDPOINT PETROLEUM CORP"))


def test_classify_categories_and_returns():
    ex = pl.DataFrame({
        "line_id": ["1-0", "2-0", "3-0", "4-0", "5-0", "6-0"], "cik": [1, 2, 3, 4, 5, 6],
        "ticker": ["M", "B", "D", "U", "S", "C"],
        "name": ["Target Co", "Broke Inc", "Tiny Inc", "Fine Inc", "Blue Acquisition Corp", "Moved Inc"],
        "exit_date": [date(2022, 6, 30)] * 6, "exchange": ["XNYS", "XNAS", "XNAS", "XNYS", "XNAS", "XNYS"],
        "last_close": [40.0, 0.5, 2.0, 30.0, 10.0, 20.0], "ret_120": [0.2, -0.9, -0.6, 0.0, 0.0, 0.0],
    })
    f = pl.DataFrame({"cik": [1, 2], "form": ["DEFM14A", "8-K"], "items": ["", "1.03,9.01"],
                      "filing_date": [date(2022, 3, 1), date(2022, 6, 1)]})
    cl = pl.DataFrame({"cik": [6], "cik_last_listed": [date(2023, 1, 31)]})
    r = {row["ticker"]: (row["category"], row["delist_ret"]) for row in delist.classify(ex, f, cl).iter_rows(named=True)}
    assert r == {"M": ("merger", 0.0), "B": ("bankruptcy", -0.55), "D": ("distressed", -0.55),
                 "U": ("unclassified", 0.0), "S": ("spac", 0.0), "C": ("continuing", 0.0)}
