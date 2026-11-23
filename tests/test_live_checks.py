"""LIVE_PAPER.md parts 3-4: live-only checks, the tax fill log and the live/paper/SPY comparison (pure parts)."""

from dataclasses import dataclass
from datetime import date

import pytest

from trader.monitor import checks, compare, daily

WEEKDAYS = lambda d: d.weekday() < 5  # noqa: E731


def test_settlement_streak_counts_latest_consecutive_days():
    recs = [{"date": "2026-12-01", "bp_deferrals": 2}, {"date": "2026-12-02", "bp_deferrals": 0},
            {"date": "2026-12-03", "bp_deferrals": 1}, {"date": "2026-12-04", "bp_deferrals": 3}]
    assert checks.settlement_streak(recs) == 2
    assert checks.settlement_streak(recs + [{"date": "2026-12-07", "bp_deferrals": 1}]) == 3
    assert checks.settlement_streak([{"date": "2026-12-01"}]) == 0


def test_mark_gap_uses_alpaca_previous_close_equity():
    assert checks.mark_gap(None, 200.0) is None
    assert checks.mark_gap({"mark_equity": None}, 200.0) is None
    assert checks.mark_gap({"mark_equity": 199.30}, 200.0) == pytest.approx(0.70)


def _f(ts, sym, side, qty, px, oid):
    return {"ts": ts, "symbol": sym, "side": side, "qty": qty, "price": px, "order_id": oid}


def test_tax_lots_fifo_gain_and_wash_sale_flag():
    rows = checks.tax_lots([
        _f("2026-12-01T14:40:00Z", "AAA", "buy", 2.0, 10.0, "b1"),
        _f("2026-12-02T14:40:00Z", "AAA", "buy", 1.0, 12.0, "b2"),
        _f("2026-12-10T14:40:00Z", "AAA", "sell", 2.5, 9.0, "s1"),   # FIFO: 2 @ 10 + 0.5 @ 12 -> loss
        _f("2026-12-20T14:40:00Z", "AAA", "buy", 1.0, 9.5, "b3"),    # within 30 days after the loss
        _f("2026-12-21T14:40:00Z", "BBB", "sell", 1.0, 5.0, "s2"),   # nothing to match
    ])
    s1 = next(r for r in rows if r["order_id"] == "s1")
    assert s1["lots"] == "b1+b2" and s1["basis"] == pytest.approx(26.0) and s1["gain"] == pytest.approx(22.5 - 26.0)
    assert s1["wash_sale_check"] is True  # b3 is a replacement purchase within 30 days
    s2 = next(r for r in rows if r["order_id"] == "s2")
    assert s2["unmatched_qty"] == pytest.approx(1.0) and s2["gain"] == 0.0


def test_tax_lots_gain_is_not_a_wash_sale_and_sold_lots_dont_count():
    rows = checks.tax_lots([_f("2026-12-01T14:40:00Z", "AAA", "buy", 1.0, 10.0, "b1"),
                            _f("2026-12-05T14:40:00Z", "AAA", "sell", 1.0, 9.0, "s1")])
    assert rows[1]["gain"] == pytest.approx(-1.0) and rows[1]["wash_sale_check"] is False  # only buy is the lot sold
    rows = checks.tax_lots([_f("2026-12-01T14:40:00Z", "AAA", "buy", 1.0, 10.0, "b1"),
                            _f("2026-12-05T14:40:00Z", "AAA", "sell", 1.0, 11.0, "s1"),
                            _f("2026-12-06T14:40:00Z", "AAA", "buy", 1.0, 11.0, "b2")])
    assert rows[1]["wash_sale_check"] is False  # a gain is never a wash sale


@dataclass
class Fill:
    broker_id: str
    symbol: str
    side: str
    filled_qty: float
    filled_avg_price: float
    submitted_at: str


def test_append_fills_is_idempotent(tmp_path):
    led = tmp_path / "fills.jsonl"
    fills = [Fill("x1", "SPY", "buy", 0.1, 700.0, "2026-12-01T14:40:00Z"), Fill("x2", "SPY", "buy", 0.0, 700.0, "2026-12-01T14:40:00Z")]
    assert checks.append_fills(led, fills) == 1  # unfilled order skipped
    assert checks.append_fills(led, fills) == 0
    assert checks.write_tax_csv(led, tmp_path / "t.csv") == 1


def _rec(d, day, spy, fills=0, te=None, exits=0, **kw):
    return {"date": d, "equity": 200.0, "day_ret": day, "live_ret": day, "spy_ret": spy, "n_fills": fills,
            "exec_te_bps": te, "n_exit_fills": exits, "te_ref": "exec", "halts": [], "errors": [], **kw}


def test_account_view_chains_returns_and_rolls_te():
    recs = [_rec("2026-11-30", 0.01, 0.01), _rec("2026-12-01", 0.02, 0.01, 1, 3.0), _rec("2026-12-02", -0.01, -0.01, 1, 12.0),
            _rec("2026-12-02", -0.01, -0.01, 1, 4.0)]  # re-run of 12-02: latest record wins
    v = compare.account_view(recs, "2026-12-02", is_session=WEEKDAYS)
    assert v["mtd"] == pytest.approx(1.02 * 0.99 - 1) and v["since"] == pytest.approx(1.01 * 1.02 * 0.99 - 1)
    assert v["te_fill_days"] == 2 and v["te_within_share"] == 1.0 and v["exec_te_bps"] == 4.0
    assert compare.account_view(recs, "2026-12-03", is_session=WEEKDAYS) is None
    assert compare.account_view(recs, "2026-12-02", start="2026-12-01", is_session=WEEKDAYS)["since"] == pytest.approx(1.02 * 0.99 - 1)


def test_account_view_uses_close_marks_and_skips_non_sessions():
    # 11/20 as it happened: Alpaca's evening equity (after-hours) said -2.64%, the close mark -3.64%; the
    # weekend records the old evening job wrote (day_ret ~0, no close mark, SPY NaN) must not count
    recs = [_rec("2026-11-19", -0.02, -0.02), {**_rec("2026-11-20", -0.0264, -0.0364), "live_ret": -0.0364},
            _rec("2026-11-21", 0.0, float("nan"), live_ret=None), _rec("2026-11-23", 0.01, 0.01)]
    v = compare.account_view(recs, "2026-11-23", is_session=WEEKDAYS)
    assert v["since"] == pytest.approx(0.98 * (1 - 0.0364) * 1.01 - 1) == pytest.approx(v["spy_since"])
    assert compare.account_view(recs, "2026-11-21", is_session=WEEKDAYS) is None
    assert compare.account_view(recs, "2026-11-20", is_session=WEEKDAYS)["day"] == -0.0364


def test_compare_skips_day_gap_without_both_returns():
    out = compare.compare({"live": {"day": None, "exec_te_bps": None, "te_within_share": None, "te_fill_days": 0,
                                    "since": 0.0, "spy_since": 0.0, "start": "2026-12-01", "halts": []},
                           "paper": {"day": 0.01}}, same_strategy=True, fallback=True)
    assert "gap_day_bps" not in out and out["warnings"] == []


def test_report_and_compare_skip_non_sessions(monkeypatch):
    import trader.calendar as cal

    monkeypatch.setattr(cal, "is_session", lambda d: False)
    logs: list[str] = []
    assert daily.build(date(2026, 11, 21), broker=None, log=logs.append) == ""  # returns before touching the broker
    assert compare.run(date(2026, 11, 21), log=logs.append) == ""
    assert len(logs) == 2 and all("not a trading session" in m for m in logs)


def test_compare_warnings():
    lv = {"day": 0.0030, "exec_te_bps": 12.0, "te_within_share": 0.8, "te_fill_days": 10, "since": 0.00, "spy_since": 0.025,
          "start": "2026-12-01", "halts": []}
    pv = {"day": 0.0010}
    out = compare.compare({"live": lv, "paper": pv}, same_strategy=True, fallback=True)
    kinds = [m for _, m in out["warnings"]]
    assert any("execution error +12.0" in m for m in kinds)
    assert any(lvl == "error" and "review" in m for lvl, m in out["warnings"])
    assert any("trails SPY by 2.50%" in m for m in kinds)
    assert any("live minus paper +20.0 bps" in m for m in kinds)
    quiet = compare.compare({"live": {**lv, "day": 0.001, "exec_te_bps": 2.0, "te_within_share": 1.0, "spy_since": 0.001},
                             "paper": pv}, True, True)
    assert quiet["warnings"] == []
    assert compare.compare({"live": None, "paper": pv}, False, True)["warnings"] == []


def test_summary_line():
    views = {"live": {"day": 0.0042, "spy_day": 0.0043, "exec_te_bps": 2.0, "halts": []},
             "paper": {"day": 0.0041, "spy_day": 0.0043, "exec_te_bps": None, "halts": []}}
    assert compare.summary_line(views, {"warnings": []}) == "live +0.42% · paper +0.41% · SPY +0.43% · TE +2bp · halts: none"


def test_account_view_skips_nan_days_and_old_te_records():
    recs = [_rec("2026-12-01", 0.01, float("nan")), _rec("2026-12-02", 0.01, 0.02, 1, 30.0, te_ref=None),
            _rec("2026-12-03", 0.0, 0.0, 1, 30.0, exits=3), {**_rec("2026-12-04", 0.0, 0.0, 1, 2.0), "n_exit_fills": None}]
    v = compare.account_view(recs, "2026-12-04", is_session=WEEKDAYS)
    assert v["spy_mtd"] == pytest.approx(0.02)
    assert v["te_fill_days"] == 0 and v["te_within_share"] is None  # open-based, exit-fill and pre-D35 days don't count
