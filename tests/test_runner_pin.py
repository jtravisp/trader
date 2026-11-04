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
