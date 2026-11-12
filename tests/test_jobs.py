from datetime import UTC, datetime

from typer.testing import CliRunner

from trader import cli, jobs


def test_log_path_uses_new_york_date(tmp_path):
    # 03:30 UTC on Nov 13 is still Nov 12 in New York
    p = jobs.log_path("forward", datetime(2026, 11, 13, 3, 30, tzinfo=UTC), root=tmp_path)
    assert p == tmp_path / "data" / "logs" / "forward_2026-11-12.log"


def test_run_executes_every_step_and_returns_first_failure(tmp_path, monkeypatch):
    # a failing step (unknown command: exit 2) must not skip the steps after it
    monkeypatch.setitem(jobs.JOBS, "t", [["--help"], ["no-such-command"], ["env-check", "--help"]])
    code = jobs.run("t", root=tmp_path)
    assert code == 2
    log = jobs.log_path("t", root=tmp_path).read_text(encoding="utf-8")
    assert log.count("job t: trader") == 3
    assert "exit codes [0, 2, 0]" in log
    assert "Usage" in log


def test_unknown_job(tmp_path):
    try:
        jobs.run("nope", root=tmp_path)
    except ValueError as e:
        assert "forward" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_env_check_never_prints_values(tmp_path, monkeypatch):
    import trader.config

    secret = "pk-SHOULD-NOT-APPEAR"
    (tmp_path / ".env").write_text(f"ALPACA_DATA_KEY={secret}\nFRED_API_KEY=\n", encoding="utf-8")
    monkeypatch.setattr(trader.config, "ROOT", tmp_path)
    res = CliRunner().invoke(cli.app, ["env-check"])
    assert res.exit_code == 1
    assert secret not in res.output
    assert "ALPACA_DATA_KEY" in res.output and "set" in res.output
    assert "FRED_API_KEY" in res.output and "MISSING" in res.output
