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
def ingest_bars(start: str = typer.Option(None), end: str = typer.Option(None),
                all_lake: bool = typer.Option(False, help="re-fetch every symbol already in the lake (upsert)")) -> None:
    """Daily bars (raw + adjusted) for the ETF list and the stock seed (or everything in the lake)."""
    from trader.data import bars, lake
    from trader.universe.build import etf_list, load_seed

    syms = etf_list() + load_seed()["symbol"].to_list()
    if all_lake:
        syms = sorted(set(syms) | set(lake.scan(bars.DAILY).select("symbol").unique().collect()["symbol"]))
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
def ingest_edgar(universe: bool = typer.Option(False, help="all CIKs in the Massive listings (PIT universe), filings only"),
                 universe_facts: bool = typer.Option(False, help="XBRL company facts for PIT-universe CIKs not yet in the lake")) -> None:
    """EDGAR filings index + XBRL facts for the stock seed (or filings for the whole PIT universe)."""
    from trader.data import edgar
    from trader.universe.build import load_seed

    if universe_facts:
        from trader.data import lake
        from trader.universe.pit import snapshots

        have = set(lake.scan(edgar.FACTS).select("cik").unique().collect()["cik"])
        ciks = sorted({int(c) for c in snapshots()["cik"].drop_nulls().unique() if str(c).isdigit()} - have)
        log(f"edgar facts for {len(ciks)} universe CIKs not yet fetched")
        for i in range(0, len(ciks), 500):  # bounded memory; each batch merges into the lake
            log(f" batch {i // 500 + 1}/{(len(ciks) + 499) // 500}")
            edgar.ingest_facts(ciks[i : i + 500], log=log)
        log("done")
        return
    if universe:
        from trader.data import lake
        from trader.universe.pit import snapshots

        have = set(lake.scan(edgar.FILINGS).select("cik").unique().collect()["cik"])
        ciks = sorted({int(c) for c in snapshots()["cik"].drop_nulls().unique() if str(c).isdigit()} - have)
        log(f"edgar filings for {len(ciks)} universe CIKs not yet fetched")
        for i in range(0, len(ciks), 1000):  # bounded memory; each batch merges into the lake
            log(f" batch {i // 1000 + 1}/{(len(ciks) + 999) // 1000}")
            edgar.ingest_filings(ciks[i : i + 1000], log=log)
        log("done")
        return
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
def adjustments() -> None:
    """P3: rebuild adjustment factors from Massive splits/dividends and compare with Alpaca's."""
    from trader.data import adjust, lake
    from trader.universe import pit

    syms = lake.scan(pit.PIT).select("ticker").unique().collect()["ticker"].to_list() if lake.exists(pit.PIT) else None
    adjust.compare(symbols=syms, log=log)  # tickers that were ever universe members


@app.command("build-universe")
def build_universe(fetch: bool = typer.Option(True, help="fetch Alpaca bars for symbols missing from the lake")) -> None:
    """P3: point-in-time universe from Massive listings (+ Alpaca bars for delisted names)."""
    from trader.universe import pit

    pit.build(log=log, fetch=fetch)


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
    from trader.monitor.alerts import heartbeat

    d = _d(day) or datetime.now(NY).date()
    ok = fw.run(d, log=log)
    heartbeat("forward", ok)
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
    universe: str = typer.Option("pit", help="pit (P3 point-in-time) | seed (biased D14 seed)"),
    notes: str = typer.Option(""),
) -> None:
    """Run + register base, 2x-cost and +1-day-delay backtests; write a report."""
    import json

    from trader.research import runner

    runner.full_experiment(strategy, json.loads(params), constraints=constraints, notes=notes, log=log, universe=universe)


@app.command("replay")
def replay_cmd(start: str = typer.Option("2025-01-02"), end: str = typer.Option("2025-11-01", help="exclusive; must not pass HOLDOUT_START"),
               capital: float = typer.Option(200.0), auto_clear: bool = typer.Option(False, help="clear human halts each morning")) -> None:
    """Run the live runner + gateway code over past sessions with a simulated broker; compare with the engine."""
    from datetime import date as d_

    import numpy as np

    from trader.research import replay

    r = replay.run(d_.fromisoformat(start), d_.fromisoformat(end), capital, auto_clear)
    diff = (r["replay"] - r["engine"]) * 1e4
    log(f"replay {start}..{end}: {len(diff)} sessions, {r['orders']} orders ({len(r['rejected'])} rejected), "
        f"final equity ${r['equity'][-1]:,.2f}")
    log(f"  return replay {np.prod(1 + r['replay']) - 1:+.2%} vs engine {np.prod(1 + r['engine']) - 1:+.2%}; "
        f"daily diff mean {diff.mean():+.2f} bps, mean |diff| {np.abs(diff).mean():.1f} bps, corr {np.corrcoef(r['replay'], r['engine'])[0, 1]:.3f}")
    for kind, sess, reason in r["halts"][:10]:
        log(f"  halt {sess} {kind}: {reason}")
    log(f"  {len(r['alerts'])} alerts would have been sent")


@app.command("llm-eval")
def llm_eval(force: bool = typer.Option(False, help="compute outcome stats before the declared sample size (logged as forced)")) -> None:
    """S06 stage 1: forward event study of LLM scores (data health until the declared read)."""
    from trader.llm import evaluate

    evaluate.report(force=force, log=log)


@app.command()
def grid(
    strategy: str = typer.Option(..., help="Strategy ID with a declared grid (research/grids.py)"),
    universe: str = typer.Option("pit", help="pit | seed"),
    dry_run: bool = typer.Option(False, help="list the points still to run"),
) -> None:
    """Run every not-yet-run point of a strategy's declared grid (each: base, 2x costs, +1 delay)."""
    from trader.research import grids

    if dry_run:
        done = grids.done(strategy, universe)
        for p in grids.points(strategy):
            log(f"  {'done' if p in done else 'todo'} {p}")
        return
    grids.run(strategy, universe, log=log)


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


@gw_app.command("preview")
def live_preview(run_id: str = typer.Option("", help="default: latest pending run")) -> None:
    """Read-only: the orders the gateway would plan now for the pending target (any time of day)."""
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
    br = _broker()
    acct = br.account()
    allow = set(etf_list()) | set(load_seed()["symbol"].to_list())
    plan = gateway.preview(run_id, br, con, allow, set(etf_list()), limits())
    log(f"preview {run_id}: equity ${acct.equity:,.2f}, cash ${acct.cash:,.2f}, buying power ${acct.buying_power:,.2f}")
    for o in plan.orders:
        log(f"  {o.side} {o.symbol} " + (f"${o.notional:,.2f}" if o.notional is not None else f"qty {o.qty}") + f" (chunk {o.chunk})")
    for sym, why in plan.deferred:
        log(f"  deferred {sym}: {why}")
    for sym, why in plan.refused:
        log(f"  refused {sym}: {why}")
    for n in plan.notes:
        log(f"  note: {n}")


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
    log(f"gateway {run_id}: {res.status}, submitted {res.submitted}, refused {len(res.refused)}, deferred {len(res.deferred)}")
    if submit:
        from trader.monitor.alerts import heartbeat

        heartbeat("gateway", res.status in ("executed", "already_executed", "outside_window"))
    for n in res.notes:
        log(f"  note: {n}")
    for s, why in res.refused:
        log(f"  refused {s}: {why}")


@gw_app.command("report")
def live_report(day: str = typer.Option(None, help="New York date; default today")) -> None:
    """Daily live report (read-only): live/reports/<date>.md."""
    from trader.monitor import daily

    daily.build(_d(day) or datetime.now(NY).date(), _broker(), log=log)


@gw_app.command("gate")
def live_gate() -> None:
    """P4 gate status (read-only): clean-day streak and execution tracking error vs the bound."""
    from trader.monitor import daily

    g = daily.gate(daily.read_records())
    log(f"P4 gate: clean streak {g['clean_streak']}/10; |exec TE| < 10bp on {'n/a' if g['te_days'] == 0 else format(g['te_within_share'], '.0%')} of "
        f"{g['te_days']} non-build-up days -> {'PASS' if g['pass'] else 'not yet'}")


@gw_app.command("test-alert")
def live_test_alert() -> None:
    """Send a test push notification through ntfy."""
    from trader.monitor.alerts import send

    code = send("trader test", "Test alert from the trader system. If you see this, phone alerts work.", "3")
    log(f"ntfy: {'not configured' if code is None else f'HTTP {code}'}")
    raise typer.Exit(0 if code == 200 else 1)


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
