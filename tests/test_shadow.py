"""Shadow forward tests (D38): pure evaluation helpers, append-only recording, and golden pins on every
configured candidate. A golden failure means a code change alters a candidate's decisions mid-test: that
needs a new declaration (STRATEGIES.md) and a restarted forward clock, not an updated digest."""

import hashlib
from datetime import UTC, date, datetime

import numpy as np
import pytest

from trader import calendar
from trader.backtest.panel import Panel
from trader.live import shadow
from trader.signals.library import REGISTRY


def _digest(tg: np.ndarray) -> str:
    return hashlib.sha256(np.round(np.nan_to_num(tg, nan=-1.0), 8).tobytes()).hexdigest()[:16]


def fixture_panel(n_stocks: int = 0, llm: bool = False) -> Panel:
    sess = calendar.sessions(date(2024, 1, 2), date(2024, 12, 31))
    T = len(sess)
    rng = np.random.default_rng(42)
    syms = ["SPY", "SHY"] + [f"X{i:02d}" for i in range(n_stocks)]
    r = rng.normal(0.0003, 0.012, (T, len(syms)))
    r[:, 1] = rng.normal(0.0001, 0.001, T)
    close = 100 * np.cumprod(1 + r, axis=0)
    fields = {}
    if llm:
        fields = {"llm_signal": rng.normal(0, 0.3, (T, len(syms))), "llm_count": rng.integers(0, 5, (T, len(syms))).astype(float)}
    return Panel(dates=np.array([str(d) for d in sess], dtype="datetime64[D]"), symbols=syms, open=close, close=close,
                 raw_close=close, dollar_volume=np.full((T, len(syms)), 1e9),
                 is_etf=np.array([True, True] + [False] * n_stocks), fields=fields)


GOLDEN = {  # candidate name -> digest of its targets on the fixture panel
    "S14-sma100-daily": "cdf6f357972ddd21",
    "S16-news-k20": "3c0912b7f4bf1326",
}


def candidate_targets(c: dict) -> np.ndarray:
    strat = REGISTRY[c["strategy"]](**c.get("params", {}))
    p = fixture_panel(30, llm=True) if getattr(strat, "needs_llm", False) else fixture_panel()
    return strat.targets(p)


def test_at_most_three_candidates_and_each_is_pinned():
    cands = shadow.candidates()
    assert 0 < len(cands) <= shadow.MAX_CANDIDATES
    for c in cands:
        assert c["name"] in GOLDEN, f"{c['name']}: add a golden digest when the candidate is declared"
        assert date.fromisoformat(str(c["start"]))


@pytest.mark.parametrize("c", shadow.candidates(), ids=lambda c: c["name"])
def test_candidate_code_is_unchanged(c):
    tg = candidate_targets(c)
    assert (~np.all(np.isnan(tg), axis=1)).sum() > 0
    assert _digest(tg) == GOLDEN[c["name"]], "candidate decisions changed: new declaration needed (D38)"


def test_usable_drops_late_error_and_duplicate_rows():
    rows = [{"name": "A", "decision_date": "2026-11-20", "weights": {"SPY": 1.0}},
            {"name": "A", "decision_date": "2026-11-20", "weights": {"SHY": 1.0}},  # duplicate: first wins
            {"name": "A", "decision_date": "2026-11-23", "late": True, "weights": {"SHY": 1.0}},
            {"name": "A", "decision_date": "2026-11-24", "error": "boom", "weights": None},
            {"name": "B", "decision_date": "2026-11-21", "weights": None}]
    u = shadow.usable(rows, "A")
    assert [r["decision_date"] for r in u] == ["2026-11-20"] and u[0]["weights"] == {"SPY": 1.0}


def test_target_matrix_sets_decisions_and_leaves_holds_nan():
    rows = [{"decision_date": "d1", "weights": {"SPY": 0.6, "SHY": 0.35}}, {"decision_date": "d2", "weights": None},
            {"decision_date": "d3", "weights": {"$CASH": 1.0}}]
    tg = shadow.target_matrix(rows, ["d0", "d1", "d2", "d3"], ["SPY", "SHY", "X"])
    assert np.all(np.isnan(tg[0])) and np.all(np.isnan(tg[2]))
    assert tg[1].tolist() == [0.6, 0.35, 0.0] and tg[3].tolist() == [0.0, 0.0, 0.0]


def test_perf_vs_spy():
    net, spy = np.array([0.01, -0.02, 0.01]), np.array([0.0, -0.01, 0.0])
    p = shadow.perf(net, spy)
    assert p["sessions"] == 3 and p["ret"] == pytest.approx(1.01 * 0.98 * 1.01 - 1)
    assert p["max_dd"] == pytest.approx(-0.02) and p["spy_max_dd"] == pytest.approx(-0.01)
    assert p["active_ann"] == pytest.approx((0.01 - 0.01 + 0.01) / 3 * 252)


def test_record_is_append_only_and_marks_late_rows(tmp_path, monkeypatch):
    from trader.live import runner

    d = date(2026, 11, 20)
    monkeypatch.setattr(shadow, "FOLDER", tmp_path)
    monkeypatch.setattr(runner, "last_closed_session", lambda: d)
    p = fixture_panel()
    sess = calendar.sessions(date(2026, 7, 1), date(2026, 11, 21))
    p = Panel(dates=np.array([str(x) for x in sess[-len(p.dates):]], dtype="datetime64[D]"), symbols=p.symbols,
              open=p.open[-len(sess):], close=p.close[-len(sess):], raw_close=p.raw_close[-len(sess):],
              dollar_volume=p.dollar_volume[-len(sess):], is_etf=p.is_etf)
    monkeypatch.setattr(runner, "memory_panel", lambda syms, sessions=300: p)
    monkeypatch.setattr(shadow, "candidates", lambda: [{"name": "S14-x", "strategy": "S14", "params": {"sma": 20, "rebalance": "daily"},
                                                        "start": "2026-11-01"}])
    evening = datetime(2026, 11, 21, 0, 30, tzinfo=UTC)  # 19:30 ET on the decision day
    assert shadow.record(log=lambda *_: None, now=evening) == 1
    assert shadow.record(log=lambda *_: None, now=evening) == 0  # same decision date: nothing rewritten
    rows = shadow.read_rows(tmp_path)
    assert len(rows) == 1 and rows[0]["late"] is False and set(rows[0]["weights"]) <= {"SPY", "SHY"}
    # a run after the next session's open (Monday 09:30 ET) is late and ignored by the evaluator
    (tmp_path / "targets.jsonl").unlink()
    assert shadow.record(log=lambda *_: None, now=datetime(2026, 11, 23, 15, 0, tzinfo=UTC)) == 1
    rows = shadow.read_rows(tmp_path)
    assert rows[0]["late"] is True and shadow.usable(rows, "S14-x") == []
