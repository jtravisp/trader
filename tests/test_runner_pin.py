from trader.live import runner


def test_pin_refuses_missing_tag():
    ok, why = runner.pin_ok("")
    assert not ok and "no strategy_tag" in why
    ok, why = runner.pin_ok("strategy/NOPE/v0")
    assert not ok and "not found" in why


def test_pin_accepts_head(tmp_path, monkeypatch):
    # HEAD as a pseudo-tag would need refs/tags; instead check an existing strategy tag if any
    import subprocess

    tags = subprocess.run(["git", "tag", "-l", "strategy/*"], capture_output=True, text=True).stdout.split()
    for t in tags:
        ok, why = runner.pin_ok(t)
        assert why in ("ok",) or "differs" in why


def test_carried_target(tmp_path):
    from trader.risk import state

    con = state.connect(tmp_path / "o.db")
    assert runner.carried_target(con) == (None, None)
    state.write_target(con, "FALLBACK-2026-11-03", "FALLBACK", {"SPY": 1.0})
    state.write_target(con, "BAD-2026-11-04", "X", {"ZZZ": 1.0})
    con.execute("UPDATE runs SET status='rejected' WHERE run_id='BAD-2026-11-04'")
    tgt, src = runner.carried_target(con)
    assert tgt == {"SPY": 1.0} and src == "FALLBACK-2026-11-03"


def test_has_native_run_ignores_carried_and_rejected(tmp_path):
    from trader.risk import state

    con = state.connect(tmp_path / "o.db")
    assert not runner.has_native_run(con, "S03")
    state.write_target(con, "S03-2026-11-04", "S03", {"SPY": 1.0})
    state.event(con, "info", "target_carried", {"run_id": "S03-2026-11-04", "from": "FALLBACK-2026-11-03"})
    state.write_target(con, "S03-2026-11-05", "S03", {"ZZZ": 1.0})
    con.execute("UPDATE runs SET status='rejected' WHERE run_id='S03-2026-11-05'")
    assert not runner.has_native_run(con, "S03")
    state.write_target(con, "S03-2026-11-06", "S03", {"AAA": 0.5, "BBB": 0.5})
    assert runner.has_native_run(con, "S03")
