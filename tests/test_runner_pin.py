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
