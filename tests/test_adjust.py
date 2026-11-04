from datetime import date

import polars as pl

from trader.data.adjust import adjusted, events


def test_split_and_dividend_backward_adjustment():
    raw = pl.DataFrame({"symbol": ["X"] * 4, "date": [date(2020, 1, d) for d in (2, 3, 6, 7)],
                        "close": [200.0, 100.0, 99.0, 100.0]})
    splits = pl.DataFrame({"ticker": ["X"], "execution_date": ["2020-01-03"], "split_from": [1.0], "split_to": [2.0]})
    divs = pl.DataFrame({"ticker": ["X", "X"], "ex_dividend_date": ["2020-01-07"] * 2, "cash_amount": [0.99, 0.99],
                         "currency": ["USD", "USD"]})  # duplicate vendor rows count once
    a = adjusted(raw, events(raw, splits, divs))
    f = dict(zip(a["date"], a["factor"], strict=True))
    assert f[date(2020, 1, 7)] == 1.0
    assert abs(f[date(2020, 1, 6)] - 0.99) < 1e-12  # 1 - 0.99/99
    assert abs(f[date(2020, 1, 3)] - 0.99) < 1e-12
    assert abs(f[date(2020, 1, 2)] - 0.5 * 0.99) < 1e-12
    # split day: no return; adjusted series is continuous
    m = a["m_adj_close"].to_list()
    assert abs(m[1] / m[0] - 1) < 1e-12
