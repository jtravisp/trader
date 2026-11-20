"""D36: paper and live keep separate gateway state; neither can trade, halt or reconcile the other."""

import json

import pytest
from test_gateway_drills import ALLOW, ETFS, OPEN_TIME, FakeBroker

from trader import jobs
from trader.config import limits
from trader.live import accounts
from trader.monitor import alerts, daily
from trader.risk import gateway, state


@pytest.fixture()
def root(tmp_path, monkeypatch):
    # restore the module globals use() rewrites
    for mod, attr in ((state, "DB"), (state, "KILL_FILE"), (daily, "REPORTS"), (alerts, "ACCOUNT"), (accounts, "current")):
        monkeypatch.setattr(mod, attr, getattr(mod, attr))
    monkeypatch.setattr(alerts, "LOG", tmp_path / "live" / "logs" / "alerts.log")
    monkeypatch.setattr(alerts, "secret", lambda *a, **k: "")  # never push from tests
    return tmp_path


def test_migrate_legacy_moves_state_once(root):
    (root / "live" / "reports").mkdir(parents=True)
    (root / "live" / "reports" / "daily.jsonl").write_text("{}\n", encoding="utf-8")
    con = state.connect(root / "live" / "orders.db")
    state.event(con, "info", "marker", "legacy")
    con.close()
    (root / "KILL").write_text("x", encoding="utf-8")

    assert accounts.migrate_legacy(root, log=lambda *_: None)
    p = root / "live" / "paper"
    assert (p / "orders.db").exists() and (p / "reports" / "daily.jsonl").exists() and (p / "KILL").exists()
    assert not (root / "live" / "orders.db").exists() and not (root / "KILL").exists()
    con = state.connect(p / "orders.db")
    assert con.execute("SELECT detail FROM events WHERE kind='marker'").fetchone()[0] == "legacy"
    assert not accounts.migrate_legacy(root, log=lambda *_: None)  # idempotent
    state.connect(root / "live" / "orders.db").close()  # a stray legacy db next to the migrated one
    with pytest.raises(RuntimeError, match="resolve by hand"):
        accounts.migrate_legacy(root, log=lambda *_: None)


def test_use_points_everything_at_the_account_folder(root):
    accounts.use("live", root=root, log=lambda *_: None)
    assert state.DB == root / "live" / "live" / "orders.db"
    assert state.KILL_FILE == root / "live" / "live" / "KILL"
    assert daily.REPORTS == root / "live" / "live" / "reports"
    assert alerts.ACCOUNT == "live" and accounts.current == "live"
    with pytest.raises(ValueError):
        accounts.use("margin", root=root)


def test_drill_accounts_are_isolated(root, monkeypatch):
    """A halt, a kill switch and orders in paper must not stop or confuse live, and the reverse."""
    sent = []
    monkeypatch.setattr(alerts, "notify", lambda level, msg: sent.append((level, msg)))
    accounts.use("paper", root=root, log=lambda *_: None)
    paper_con = state.connect()
    paper_broker = FakeBroker()
    state.write_target(paper_con, "p1", "TEST", {"SPY": 0.30})
    assert gateway.run("p1", paper_broker, paper_con, ALLOW, ETFS, limits(), OPEN_TIME, dry_run=False, paper=True).status == "executed"
    state.set_halt(paper_con, "reconciliation_mismatch", "drill", OPEN_TIME.date().isoformat())
    state.KILL_FILE.write_text("x", encoding="utf-8")

    accounts.use("live", root=root, log=lambda *_: None)
    live_con = state.connect()
    assert live_con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    assert not state.active_halts(live_con, OPEN_TIME.date().isoformat())
    live_broker = FakeBroker()
    state.write_target(live_con, "l1", "TEST", {"SPY": 0.30})
    res = gateway.run("l1", live_broker, live_con, ALLOW, ETFS, limits(), OPEN_TIME, dry_run=False, paper=False)
    assert res.status == "executed" and live_broker.orders, "paper's halt and KILL must not block live"
    assert live_con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == len(live_broker.orders)

    # back on paper: its own kill switch, halt and orders are untouched by the live run
    accounts.use("paper", root=root, log=lambda *_: None)
    assert state.KILL_FILE.exists()
    con = state.connect()
    assert state.active_halts(con, OPEN_TIME.date().isoformat())
    assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == len(paper_broker.orders)


def test_jobs_expand_per_account_live_first():
    assert jobs.steps("gateway", ["live", "paper"]) == [["live", "--account", "live", "execute", "--submit"],
                                                       ["live", "--account", "paper", "execute", "--submit"]]
    f = jobs.steps("forward", ["paper"])
    assert f == [["forward"], ["shadow", "record"], ["live", "--account", "paper", "report"], ["live", "--account", "paper", "target"],
                 ["live", "compare"]]
    assert accounts.enabled() == ["paper"]  # settings.toml: live stays off until the user approves (D34)


def test_alerts_are_tagged_and_live_goes_one_priority_higher(root, monkeypatch):
    pushed = []
    monkeypatch.setattr(alerts, "secret", lambda name, required=False: "topic" if name == "NTFY_TOPIC" else "")
    monkeypatch.setattr(alerts, "send", lambda title, msg, prio: pushed.append((title, msg, prio)))
    accounts.use("live", root=root, log=lambda *_: None)
    alerts.notify("warning", "unsettled cash")
    accounts.use("paper", root=root, log=lambda *_: None)
    alerts.notify("warning", "unsettled cash")
    assert pushed[0][1].startswith("[LIVE]") and pushed[0][2] == "4"
    assert pushed[1][1].startswith("[paper]") and pushed[1][2] == "3"
    combined = [json.loads(x) for x in (root / "live" / "logs" / "alerts.log").read_text(encoding="utf-8").splitlines()]
    assert [r["account"] for r in combined] == ["live", "paper"]
    live_only = (root / "live" / "live" / "logs" / "alerts.log").read_text(encoding="utf-8").splitlines()
    assert len(live_only) == 1


def test_execute_refuses_a_disabled_account(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from trader import cli

    monkeypatch.setattr(accounts, "folder", lambda name, root=None: tmp_path / name)
    monkeypatch.setattr(accounts, "migrate_legacy", lambda *a, **k: False)
    for mod, attr in ((state, "DB"), (state, "KILL_FILE"), (daily, "REPORTS"), (alerts, "ACCOUNT"), (accounts, "current")):
        monkeypatch.setattr(mod, attr, getattr(mod, attr))
    con = state.connect(tmp_path / "live" / "orders.db")
    state.write_target(con, "l1", "TEST", {"SPY": 1.0})
    res = CliRunner().invoke(cli.app, ["live", "--account", "live", "execute", "--submit"])
    assert res.exit_code == 2 and "not enabled" in res.output
