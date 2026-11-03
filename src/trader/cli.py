"""trader CLI. Data commands for P1; trading commands arrive with the gateway in P4."""

from __future__ import annotations

import sys
from datetime import date, datetime

import typer

from trader.config import NY, settings

app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_show_locals=False)
ingest_app = typer.Typer(no_args_is_help=True, help="Historical backfills (never touch the holdout).")
app.add_typer(ingest_app, name="ingest")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def log(msg: str) -> None:
    print(f"[{datetime.now(NY):%H:%M:%S}] {msg}", flush=True)


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


@app.command()
def universe() -> None:
    """Build config/universe_seed.csv (top-1000 seed, pre-holdout ranking window)."""
    from trader.universe.build import build_seed

    build_seed(log=log)


@ingest_app.command("bars")
def ingest_bars(start: str = typer.Option(None), end: str = typer.Option(None)) -> None:
    """Daily bars (raw + adjusted) for the ETF list and the stock seed."""
    from trader.data import bars
    from trader.universe.build import etf_list, load_seed

    syms = etf_list() + load_seed()["symbol"].to_list()
    log(f"daily bars for {len(syms)} symbols")
    n = bars.ingest_daily(syms, _d(start), _d(end), log=log)
    log(f"done: {n} rows")


@ingest_app.command("minute")
def ingest_minute(start: str = typer.Option(None), end: str = typer.Option(None)) -> None:
    """Regular-session minute bars for the ETF list."""
    from trader.config import universe_config
    from trader.data import bars
    from trader.universe.build import etf_list

    st = _d(start) or date.fromisoformat(universe_config()["minute_bars"]["start"])
    n = bars.ingest_minute(etf_list(), st, _d(end), log=log)
    log(f"done: {n} rows")


@ingest_app.command("news")
def ingest_news(start: str = typer.Option("2015-01-01"), end: str = typer.Option(None)) -> None:
    """Alpaca news headlines + summaries (resumable by month)."""
    from trader.data import news

    n = news.ingest(date.fromisoformat(start), _d(end) or settings().holdout_start, log=log)
    log(f"done: {n} items")


@ingest_app.command("edgar")
def ingest_edgar() -> None:
    """EDGAR filings index + XBRL facts for the stock seed."""
    from trader.data import edgar
    from trader.universe.build import load_seed

    ciks = load_seed()["cik"].unique().to_list()
    log(f"edgar filings for {len(ciks)} CIKs")
    edgar.ingest_filings(ciks, log=log)
    log("edgar company facts")
    edgar.ingest_facts(ciks, log=log)
    log("done")


@ingest_app.command("fred")
def ingest_fred() -> None:
    """FRED regime series."""
    from trader.data import fred

    fred.ingest(log=log)


@ingest_app.command("massive")
def ingest_massive() -> None:
    """Massive free-tier reference data: month-end ticker snapshots, splits, dividends (~4 h)."""
    from trader.data import massive

    log(f"massive ticker snapshots: {len(massive.snapshot_dates())} month-ends")
    massive.ingest_tickers(log=log)
    massive.ingest_corporate_actions(log=log)
    log("done")


@ingest_app.command("all")
def ingest_all() -> None:
    """Every Alpaca backfill in sequence (shares one rate limiter)."""
    ingest_bars(None, None)
    ingest_minute(None, None)
    ingest_news("2015-01-01", None)


@app.command()
def quality(sample: int = 20, seed: int = 7) -> None:
    """Run data-quality checks; writes data/quality/<date>.md. Exit 1 on unexplained failures."""
    from trader.data import quality as q

    ok = q.run_all(sample=sample, seed=seed, log=log)
    raise typer.Exit(0 if ok else 1)


@app.command()
def survivorship() -> None:
    """P1 survivorship check: can Alpaca reconstruct a past universe?"""
    from trader.universe import survivorship as sv

    sv.run(log=log)


@app.command()
def gate() -> None:
    """P1 gate: SPY total return vs published figures."""
    from trader.data.gates import spy_total_return_check

    ok, table = spy_total_return_check(log=log)
    print(table)
    log("SPY total-return gate: " + ("PASS" if ok else "FAIL"))
    raise typer.Exit(0 if ok else 1)


@app.command()
def forward(day: str = typer.Option(None, help="New York date; default = today")) -> None:
    """Daily forward job: today's news + bars, then LLM scoring within budget."""
    from trader.live import forward as fw

    d = _d(day) or datetime.now(NY).date()
    ok = fw.run(d, log=log)
    raise typer.Exit(0 if ok else 1)


@app.command("score-status")
def score_status() -> None:
    """LLM scoring spend and counts."""
    from trader.llm import budget

    budget.report(log=log)


@app.command()
def backtest(
    strategy: str = typer.Option(..., help="Strategy ID declared in docs/STRATEGIES.md"),
    params: str = typer.Option("{}", help="JSON parameters"),
    constraints: str = typer.Option("limits", help="limits | none"),
    notes: str = typer.Option(""),
) -> None:
    """Run + register base, 2x-cost and +1-day-delay backtests; write a report."""
    import json

    from trader.research import runner

    runner.full_experiment(strategy, json.loads(params), constraints=constraints, notes=notes, log=log)


@app.command()
def sanity(seeds: int = 100, only: str = typer.Option("", help="san01|san02|san03|san04")) -> None:
    """P2 gate: harness sanity tests (registered)."""
    from trader.research import sanity as sn

    if only:
        ok, lines = getattr(sn, only)(log=log) if only != "san02" else sn.san02(seeds, log=log)
        print(chr(10).join(lines))
    else:
        ok = sn.run_all(seeds, log=log)
    raise typer.Exit(0 if ok else 1)


gw_app = typer.Typer(no_args_is_help=True, help="Live runner and risk gateway (paper unless mode=live).")
app.add_typer(gw_app, name="live")


def _broker():
    from trader.risk.broker import AlpacaBroker

    b = AlpacaBroker()
    if not b.paper and settings().live_mode != "live":
        log("REFUSED: trade keys point at a LIVE account but settings.toml [live] mode is not 'live'")
        raise typer.Exit(2)
    return b


@gw_app.command("target")
def live_target() -> None:
    """After the close: compute and store tomorrow's target portfolio."""
    from trader.live import runner

    runner.run(log=log)


@gw_app.command("execute")
def live_execute(run_id: str = typer.Option("", help="default: latest pending run"),
                 submit: bool = typer.Option(False, help="actually send orders (otherwise dry run)")) -> None:
    """In the trading window: validate the pending target and send orders through the gateway."""
    from datetime import UTC
    from datetime import datetime as dt

    from trader.config import limits
    from trader.risk import gateway, state
    from trader.universe.build import etf_list, load_seed

    con = state.connect()
    if not run_id:
        row = con.execute("SELECT run_id FROM runs WHERE status IN ('target_written','incomplete') ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            log("no pending target")
            return
        run_id = row[0]
        # older targets that never executed are stale: only the latest decision may trade
        n = con.execute("UPDATE runs SET status='superseded' WHERE status IN ('target_written','incomplete') AND run_id != ?", (run_id,)).rowcount
        con.commit()
        if n:
            log(f"marked {n} older pending target(s) superseded")
    allow = set(etf_list()) | set(load_seed()["symbol"].to_list())
    res = gateway.run(run_id, _broker(), con, allow, set(etf_list()), limits(), dt.now(UTC), dry_run=not submit)
    log(f"gateway {run_id}: {res.status}, submitted {res.submitted}, refused {len(res.refused)}")
    for n in res.notes:
        log(f"  note: {n}")
    for s, why in res.refused:
        log(f"  refused {s}: {why}")


@gw_app.command("report")
def live_report(day: str = typer.Option(None, help="New York date; default today")) -> None:
    """Daily live report (read-only): live/reports/<date>.md."""
    from trader.monitor import daily

    daily.build(_d(day) or datetime.now(NY).date(), _broker(), log=log)


@gw_app.command("kill")
def live_kill() -> None:
    """Kill switch: cancel all open orders and block new ones (creates KILL)."""
    from trader.risk import gateway, state

    log(f"kill: cancelled {gateway.kill(_broker(), state.connect())} orders; remove with `trader live unkill`")


@gw_app.command("unkill")
def live_unkill() -> None:
    """Human: remove the kill switch."""
    from trader.risk import state

    state.KILL_FILE.unlink(missing_ok=True)
    state.event(state.connect(), "warning", "kill_cleared", "operator removed KILL")
    log("kill switch removed")


@gw_app.command("flatten")
def live_flatten(yes: bool = typer.Option(False, "--yes", help="confirm")) -> None:
    """Human: close every position (market orders)."""
    from trader.risk import gateway, state

    if not yes:
        log("refusing without --yes")
        raise typer.Exit(1)
    log(f"flatten: {gateway.flatten(_broker(), state.connect())} close orders")


@gw_app.command("halts")
def live_halts() -> None:
    """Show active halts."""
    from trader.risk import state

    con = state.connect()
    from datetime import datetime as dt

    for h in state.active_halts(con, dt.now(NY).date().isoformat()):
        log(f"{h['kind']} since {h['set_at']}: {h['reason']}")


@gw_app.command("clear-halt")
def live_clear_halt(kind: str, who: str = typer.Option(..., help="your name, for the audit log")) -> None:
    """Human: clear a peak_drawdown / reconciliation_mismatch / duplicate_order halt after review."""
    from trader.risk import state

    log(f"cleared {state.clear_halt(state.connect(), kind, who)} {kind} halt(s)")


@app.command("score-smoke")
def score_smoke() -> None:
    """One live scoring request to validate the LLM setup (~$0.0001)."""
    from trader.llm import scorer

    raise typer.Exit(0 if scorer.smoke_test(log=log) else 1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
