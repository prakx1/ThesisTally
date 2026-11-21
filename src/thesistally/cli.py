"""Terminal entry point. All broker requests are market data or session checks."""

from __future__ import annotations

import json
import os
from contextlib import closing
from datetime import date, timedelta
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from . import evaluation, history, market
from .exchange import collect, read_context
from .journal import journal, process, read_call, record_action, run_report
from .output import CSV, JSON, emit
from .signals import load_session, rules

app = typer.Typer(
    no_args_is_help=False,
    pretty_exceptions_enable=False,
    help="ThesisTally · Nifty 200 swing research",
)
out = Console()


data_app = typer.Typer(help="Archive and audit dated exchange data.")
app.add_typer(data_app, name="data")


@data_app.command("backfill")
def data_backfill(
    start: Annotated[date, typer.Option("--from")],
    end: Annotated[date | None, typer.Option("--to")] = None,
    as_json: JSON = False,
    as_csv: CSV = False,
):
    """Resume official prices, constituent notices, adjusted sessions and data coverage."""
    from . import constituents, historical_sessions, reconstitutions

    end = end or market.today()
    try:
        data_root = root()
        existing = data_root / "universes" / "membership-gaps.json"
        if existing.exists():
            prior = json.loads(existing.read_text())
            first = min(start, date.fromisoformat(prior["from"]))
            last = max(end, date.fromisoformat(prior["to"]))
        else:
            first, last = start, end
        import pandas as pd

        warm = (pd.Timestamp(first) - pd.DateOffset(months=14)).date()
        days = history.calendar(data_root, first, last)
        all_days = history.calendar(data_root, warm, last)
        source_errors = history.stage_history(
            data_root, all_days, progress=lambda x: typer.echo(x, err=True)
        )
        days = history.verified_calendar(data_root, first, last)
        all_days = sorted(set(all_days) | set(days))
        source_errors.update(
            history.stage_history(
                data_root,
                [
                    d
                    for d in days
                    if not (data_root / "sources" / "nse-daily" / str(d)).is_dir()
                    and d not in market.sessions(data_root)
                ],
            )
        )
        ref_errors = history.reference_sources(data_root, warm, last)
        history.collect_vix(data_root, warm, last)
        reconstitutions.fetch_notices(data_root, warm, last)
        membership = constituents.reconstruct(
            data_root, days, progress=lambda x: typer.echo(x, err=True)
        )
        import pandas as pd

        from .exchange import historical_controls

        historical_controls(
            data_root, all_days, pd.read_parquet(data_root / "universes" / "reconstructed.parquet")
        )
        result = historical_sessions.publish(
            data_root,
            all_days,
            [d for d in days if start <= d <= end],
            progress=lambda x: typer.echo(x, err=True),
        )
        history.kite_checks(data_root, warm, last)
        emit(
            {
                "membership": membership,
                "prices": result,
                "source_errors": source_errors,
                "reference_errors": ref_errors,
                "kite_crosscheck": history.crosscheck(data_root),
            },
            as_json,
            as_csv,
        )
        if membership["gaps"] or result["unpublished"] or source_errors:
            raise typer.Exit(1)
    except (ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None


@data_app.command("check")
def data_check(
    start: Annotated[date | None, typer.Option("--from")] = None,
    end: Annotated[date | None, typer.Option("--to")] = None,
    as_json: JSON = False,
    as_csv: CSV = False,
):
    """Verify archived prices, independent trading dates, membership coverage and quarantine."""
    available = market.sessions(root())
    if not available:
        typer.echo("No archived sessions.", err=True)
        raise typer.Exit(1)
    result = history.audit(
        root(), history.verified_calendar(root(), start or available[0], end or available[-1])
    )
    emit(result, as_json, as_csv)
    if not result["complete"]:
        from .notifier import warning

        warning(
            root(),
            f"check:{start}:{end}",
            f"Data checks found gaps or integrity failures for {start or available[0]} through {end or available[-1]}.",
        )
        raise typer.Exit(1)


@app.callback(invoke_without_command=True)
def launch(ctx: typer.Context):
    """Open the live terminal, or select a command for automation."""
    if ctx.invoked_subcommand is None:
        import sys

        if not sys.stdin.isatty() or not sys.stdout.isatty():
            typer.echo(ctx.get_help())
            return
        from .dashboard import Dashboard
        from .terminal import RadarTerminal

        RadarTerminal(Dashboard(root())).run()


def command(func):
    @wraps(func)
    def guarded(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (market.MarketError, OSError, ValueError) as e:
            typer.echo(f"radar: {e}", err=True)
            raise typer.Exit(1) from None

    return app.command()(guarded)


def root():
    return Path(os.environ.get("RADAR_DATA", "data")).expanduser()


def chosen(day):
    days = market.sessions(root())
    if day is None and not days:
        raise market.MarketError("No market sessions. Run radar sync.")
    return day or days[-1]


def show(data):
    typer.echo(json.dumps(data, indent=2, allow_nan=False))


def journal_path():
    return root() / "journal.sqlite"


@command
def session():
    """Verify that the externally supplied Kite session is usable."""
    with market.Feed() as feed:
        feed.session()
    typer.echo("Kite session accepted.")


@command
def sync(day: date | None = None, start: date | None = None):
    """Fetch, settle and archive a complete NSE session with indicator warm-up."""
    d = day or market.today()
    show(collect_session(root(), d, start, typer.echo))


def collect_session(data_root, d, start, progress):
    if start is None and d in market.sessions(data_root):
        start = date.fromisoformat(market.verify(data_root, d)["requested_start"])
    with market.Feed() as feed:
        info = market.sync(data_root, feed, d, start or d - timedelta(days=730), progress)
        members, _ = market.membership(data_root, d)
        collect(data_root, feed, d, date.fromisoformat(info["prior_day"]), members)
        return info


@command
def import_universe(file: Path, start: date, end: date, source: str):
    """Import a complete NSE-format list for its evidenced inclusive validity interval."""
    path = market.save_members(root(), file.read_bytes(), start, end, source)
    typer.echo(path)


@command
def backfill(start: date, end: date, anchor: date | None = None):
    """Build historical sessions from dated membership, bhavcopies and cached Kite history."""
    with market.Feed() as feed:
        market.backfill(root(), feed, start, end, chosen(anchor), typer.echo)


@command
def verify(day: date | None = None):
    """Verify archived checksums and compare all settled equity closes with the NSE final."""
    d = chosen(day)
    info = market.verify(root(), d)
    info["exchange_context_members"] = len(read_context(root(), d, verify_sources=True))
    show(info)


@command
def scan(
    day: Annotated[date | None, typer.Option("--day", "--date")] = None,
    run: str = "observations",
    config: Path | None = None,
    as_json: JSON = False,
    as_csv: CSV = False,
):
    """Journal rules-only observations and advance existing hypothetical calls."""
    d = chosen(day)
    emit(scan_session(root(), d, run, config), as_json, as_csv)


def scan_session(data_root, d, run, config):
    cfg = rules(config)
    active = data_root / "rankers" / "active.json"
    if run == "observations" and active.is_file():
        selected = json.loads(active.read_text())
        if (
            selected["revision"] == cfg["revision"]
            and str(d) > selected["evaluated_through"]
            and selected["eligible_to_switch"]
        ):
            cfg["ranking_model"] = selected["model"]
    with closing(journal(data_root / "journal.sqlite")) as db:
        previous = db.execute(
            "SELECT r.rules FROM runs r JOIN scans s ON s.run=r.id WHERE s.day=? AND s.run=?",
            (str(d), run),
        ).fetchone()
        actual_revision = cfg["revision"]
        if previous:
            recorded = db.execute(
                "SELECT revision FROM calls WHERE run=? AND day=? LIMIT 1", (run, str(d))
            ).fetchone()
            actual_revision = recorded[0] if recorded else previous[0]
        regime, candidates, fs = load_session(data_root, d, cfg, open_calls(db, run, d))
        n = process(db, run, d, regime, candidates, fs, cfg)
        if previous and run == "observations" and d == market.today():
            from . import notifier, research_journal

            with db:
                notifier.scan_messages(db, run, str(d), regime)
                research_journal.queue(db, run, str(d))
        alloc = dict(
            db.execute(
                "SELECT allocation,count(*) FROM calls WHERE run=? AND day=? GROUP BY allocation",
                (run, str(d)),
            ).fetchall()
        )
    if run == "observations":
        from .research_worker import start_pending

        start_pending(data_root, after_scan=previous is None)
    return {
        "day": str(d),
        "run": run,
        "revision": actual_revision,
        "regime": regime,
        "detected": n,
        "allocation": alloc,
    }


@command
def calls(
    run: str = "observations",
    limit: int = typer.Option(30, min=1),
    call_id: int | None = None,
    only_open: Annotated[bool, typer.Option("--open")] = False,
    strategy: str | None = None,
    shadow: bool = False,
    as_json: JSON = False,
    as_csv: CSV = False,
):
    """List observations or inspect a call's detection snapshot, events and fills."""
    with closing(journal(journal_path())) as db:
        if call_id is not None:
            return emit(read_call(db, call_id), as_json, as_csv)
        filters = ["run=?"]
        args = [run]
        if only_open:
            filters.append("status IN ('active','confirmed')")
        if strategy:
            filters.append("strategy=?")
            args.append(strategy)
        if shadow:
            filters.append("allocation!='portfolio'")
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM calls WHERE "
                + " AND ".join(filters)
                + " ORDER BY day DESC,id LIMIT ?",
                (*args, limit),
            )
        ]
        if as_json or as_csv:
            for r in rows:
                for key in ("features", "regime"):
                    r[key] = json.loads(r[key])
            return emit(
                rows,
                as_json,
                as_csv,
                columns=[item[1] for item in db.execute("PRAGMA table_info(calls)")],
            )
        table = Table(title=f"ThesisTally · {run}", box=box.SIMPLE, pad_edge=False)
        for key in ("ID", "Session", "Symbol", "Setup", "Status", "Book", "Qty", "Net R"):
            table.add_column(key)
        labels = {"trend_pullback": "Pullback", "vcp_breakout": "VCP", "high_momentum": "52w high"}
        for row in rows:
            table.add_row(
                str(row["id"]),
                row["day"],
                row["symbol"],
                labels.get(row["strategy"], row["strategy"]),
                row["status"],
                "portfolio" if row["allocation"] == "portfolio" else "shadow",
                str(row["size"]),
                "—" if row["outcome_r"] is None else f"{row['outcome_r']:.2f}",
            )
        out.print(table)


@command
def action(call_id: int, decision: str):
    """Record taken/skip feedback while tracking the original hypothetical outcome."""
    with closing(journal(journal_path())) as db:
        record_action(db, call_id, decision)
    typer.echo(f"Call {call_id} marked {decision}.")


@command
def reports(run: str = "observations"):
    """Report after-cost outcomes in the portfolio and counterfactual cohorts."""
    with closing(journal(journal_path())) as db:
        from .provenance import journal_proof

        show({**run_report(db, run), "provenance": journal_proof(root(), db, run)})


def open_calls(db, run, day):
    return db.execute(
        """SELECT symbol, isin, min(day) AS day FROM calls
        WHERE run=? AND day<? AND status IN ('active','confirmed') GROUP BY symbol,isin""",
        (run, str(day)),
    ).fetchall()


def windows(start, end, holdout, folds):
    if not start < holdout <= end:
        raise market.MarketError("Choose start < holdout <= end.")
    days = []
    cursor = end
    while cursor >= start:
        manifest = root() / "sessions" / str(cursor) / "manifest.json"
        if not manifest.is_file():
            raise market.MarketError(
                f"Missing verified session {cursor}. Import dated membership and backfill first."
            )
        days.append(cursor)
        previous = date.fromisoformat(json.loads(manifest.read_text())["prior_day"])
        if previous >= cursor:
            raise market.MarketError(f"Invalid prior session in {cursor}.")
        cursor = previous
    days.reverse()
    if not days or days[0] != start or holdout not in days:
        raise market.MarketError("Start, holdout and end must all be covered trading sessions.")
    hist = [d for d in days if d < holdout]
    if len(hist) < folds:
        raise market.MarketError("More folds than pre-holdout sessions.")
    return [
        (f"walk-{x + 1}", hist[len(hist) * x // folds : len(hist) * (x + 1) // folds])
        for x in range(folds)
    ] + [("holdout", [d for d in days if d >= holdout])]


@command
def backtest(
    start: Annotated[date | None, typer.Argument()] = None,
    end: Annotated[date | None, typer.Argument()] = None,
    holdout: Annotated[date | None, typer.Argument()] = None,
    folds: int = typer.Option(2, min=1),
    config: Path | None = None,
    walk_forward: bool = False,
    tests_only: bool = False,
    as_json: JSON = False,
    as_csv: CSV = False,
    train_years: int = typer.Option(3, min=1),
    test_years: int = typer.Option(1, min=1),
):
    """Replay a diagnostic date range or reserve the final 18 months of a walk-forward study."""
    cfg = rules(config)
    available = market.sessions(root())
    if not available:
        raise market.MarketError("No verified sessions available.")
    start, end = start or available[0], end or available[-1]
    if tests_only and not walk_forward:
        raise market.MarketError("--tests-only requires --walk-forward.")
    if walk_forward:
        if holdout is not None:
            raise market.MarketError("Walk-forward selects the final holdout automatically.")
        days = history.verified_calendar(root(), start, end)
        if missing := sorted(set(days) - set(available)):
            raise market.MarketError(
                f"Missing {len(missing)} verified trading sessions, first {missing[0]}."
            )
        parts = evaluation.walk_windows(
            days, train_years, test_years, cfg["validation"]["holdout_months"]
        )
        params = {
            "mode": "walk_forward",
            "train_years": train_years,
            "test_years": test_years,
            "holdout_months": cfg["validation"]["holdout_months"],
        }
    else:
        if holdout is None:
            raise market.MarketError("Supply a diagnostic holdout date or use --walk-forward.")
        parts = [(name, d, None) for name, d in windows(start, end, holdout, folds)]
        days = [d for _, part, _ in parts for d in part]
        params = {"mode": "diagnostic", "folds": folds, "holdout": str(holdout)}
    result = evaluation.evaluate(root(), parts, days, cfg, params, walk_forward, tests_only)
    emit(result, as_json, as_csv, rows=result["folds"])


@command
def daily(run: str = "observations", config: Path | None = None):
    """Archive the NSE final and advance today's observation journal."""
    sync(day=market.today(), start=None)
    scan(day=market.today(), run=run, config=config)


@command
def schedule(run: str = "observations", config: Path | None = None):
    """Collect completed sessions while this foreground process is running."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    service = BlockingScheduler(timezone=market.IST)

    def collect_published_session():
        day = market.today()
        with closing(journal(journal_path())) as db:
            if db.execute("SELECT 1 FROM scans WHERE run=? AND day=?", (run, str(day))).fetchone():
                return
        # No trading-day guesses. The dated final is the exchange's
        # publication signal, including for special weekend sessions.
        archive = f"{market.ARCHIVE}/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
        with market.Feed() as feed:
            response = feed.http.head(archive)
            if response.status_code == 404:
                return
            response.raise_for_status()
        try:
            daily(run=run, config=config)
        except typer.Exit:
            return  # The CLI logs the cause. Retry on the next publication check.

    service.add_job(
        collect_published_session,
        CronTrigger(hour="15", minute="45", timezone=market.IST),
        max_instances=1,
        coalesce=True,
    )
    service.add_job(
        collect_published_session,
        CronTrigger(hour="16-22", minute="*/15", timezone=market.IST),
        max_instances=1,
        coalesce=True,
    )
    typer.echo("Watching for the NSE final from 15:45 to 22:45 IST. Press Ctrl-C to stop.")
    from datetime import datetime, time

    if time(15, 45) <= datetime.now(market.IST).time() <= time(22, 45):
        collect_published_session()
    try:
        service.start()
    except KeyboardInterrupt:
        pass


@command
def call(call_id: int, as_json: JSON = False, as_csv: CSV = False):
    """Inspect a single journaled call."""
    with closing(journal(journal_path())) as db:
        emit(read_call(db, call_id), as_json, as_csv)


@command
def report(
    by: str = "strategy", run: str = "observations", as_json: JSON = False, as_csv: CSV = False
):
    """Group after-cost outcomes in separate portfolio and shadow books."""
    from collections import defaultdict

    from .journal import metrics

    groups = defaultdict(list)
    with closing(journal(journal_path())) as db:
        if by not in {"strategy", "regime", "sector", "year"}:
            raise market.MarketError("Choose strategy, regime, sector or year.")
        raw = db.execute("SELECT * FROM calls WHERE run=? ORDER BY day,id", (run,))
        for row in raw:
            group = (
                ("pass" if json.loads(row["regime"])["enabled"] else "fail")
                if by == "regime"
                else row["day"][:4]
                if by == "year"
                else row[by]
            )
            groups[group, "portfolio" if row["allocation"] == "portfolio" else "shadow"].append(row)
        from .provenance import journal_proof

        proof = journal_proof(root(), db, run)
        output = [
            {by: key, "book": book, **metrics(rows), "provenance": proof}
            for (key, book), rows in sorted(groups.items())
        ]
    emit(output, as_json, as_csv)


@command
def run():
    """Run exchange-day collection, private delivery and the nightly backup."""
    from .daemon import serve

    serve(root())


@command
def research_mode(
    mode: Annotated[str | None, typer.Argument(help="on, off, codex or openai")] = None,
):
    """Show or save the research setting for this data directory."""
    from .research_journal import configured_provider

    directory = root()
    if mode is not None:
        mode = mode.strip().lower()
        if mode == "on":
            mode = "codex"
        if mode not in {"codex", "openai", "off"}:
            raise market.MarketError("Choose on, off, codex or openai.")
        with market.staging(directory) as tmp:
            setting = tmp / "research-provider.txt"
            setting.write_text(mode + "\n")
            setting.replace(directory / setting.name)
    effective = configured_provider(directory)
    typer.echo(f"Research mode: {effective}")
    if mode and mode != effective:
        typer.echo(f"Saved {mode}. RADAR_AI_PROVIDER currently overrides this setting.")
    if mode and mode == effective and effective != "off":
        from .research_worker import start_pending

        start_pending(directory, after_scan=True)


@command
def research(
    target: str,
    as_of: Annotated[date | None, typer.Option("--date")] = None,
    as_json: JSON = False,
    as_csv: CSV = False,
):
    """Run dated shadow research for a symbol or a journal call."""
    from .research import run_report

    emit(run_report(root(), target, as_of), as_json, as_csv)


@command
def ai_audit(as_json: JSON = False, as_csv: CSV = False):
    """Compare pre-entry AI verdicts with the same calls' after-cost outcomes."""
    from .research_journal import audit

    with closing(journal(journal_path())) as db:
        from .provenance import journal_proof

        emit(
            {**audit(db), "provenance": journal_proof(root(), db, "observations", True)},
            as_json,
            as_csv,
        )


@data_app.command("filings")
def data_filings(symbol: str, day: Annotated[date | None, typer.Option("--date")] = None):
    """Archive dated official company filings independently of a paid model."""
    import httpx

    from .filings import collect as collect_filings

    day = chosen(day)
    members = market.read_members((root() / "sessions" / str(day) / "members.csv").read_bytes())
    row = members[members.symbol == symbol.upper()]
    if row.empty:
        raise typer.BadParameter("Symbol absent from the dated universe.")
    with (
        closing(journal(journal_path())) as db,
        httpx.Client(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bseindia.com/"},
        ) as client,
    ):
        result = collect_filings(root(), db, row.iloc[0].symbol, row.iloc[0]["isin"], day, client)
        show(
            {
                "symbol": symbol.upper(),
                "filings": len(result),
                "types": sorted({f["type"] for f in result}),
                "sources": [
                    dict(r)
                    for r in db.execute(
                        "SELECT source,status,detail FROM filing_fetches WHERE symbol=? AND as_of=? ORDER BY id DESC LIMIT 4",
                        (symbol.upper(), str(day)),
                    )
                ],
            }
        )


@command
def ranker_study(report_file: Path | None = None, as_json: JSON = False, as_csv: CSV = False):
    """Compare a gapped LightGBM ranker with relative strength on rolling test accounts."""
    from .ranking import study

    result = study(root(), report_file)
    emit(result, as_json, as_csv, rows=result["windows"])
