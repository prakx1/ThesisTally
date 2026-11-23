"""Prospective, revision-bound A126 paper ledger and same-session total-return comparison."""

from __future__ import annotations

import fcntl
import json
import subprocess
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path

import httpx

from . import (
    exchange,
    market,
    stage3_benchmark,
    steady_actions,
    steady_metrics,
    steady_replay,
)
from .pilot_data import write_json
from .steady_momentum import CAPITAL, REGISTRY, last_state, open_journal, seed

PACKAGE = Path(__file__).resolve().parent
REGISTRATION = PACKAGE / "revisions/a126-forward.json"
RUN = "A126-forward"
SERIES = "Nifty 200 gross total return"


def engine_sha256():
    # Bind the scoring, orders, fees, ledger, and prospective collection clocks.
    parts = [steady_replay.source_hash(), market.sha(REGISTRY.read_bytes())]
    for name in (
        "history.py",
        "constituents.py",
        "daemon.py",
        "pilot_data.py",
        "forward_conventions.py",
        "frozen_forward.py",
        "frozen_inputs.py",
        "frozen_prices.py",
        "stage3_benchmark.py",
        "revisions/r2.yaml",
        "revisions/close-holder-actions.json",
    ):
        parts.append(market.sha((PACKAGE / name).read_bytes()))
    return market.sha("\n".join(parts).encode())


def registration():
    cfg = json.loads(REGISTRATION.read_bytes())
    if (
        cfg["strategy"] != "A126"
        or cfg["mode"] != "paper"
        or cfg["lookback"] != 126
        or cfg["status"] not in ("qualified", "candidate, under observation")
        or date.fromisoformat(cfg["anchor_session"]) > date.fromisoformat(cfg["freeze_date"])
        or cfg["engine_sha256"] != engine_sha256()
    ):
        raise market.MarketError(
            "Frozen A126 identity changed. Use a new frozen revision and book."
        )
    return cfg


def tagged(cfg):
    repo = PACKAGE.parents[1]
    path = REGISTRATION.relative_to(repo)
    # Resolve the local tag to its immutable configuration. An untagged or modified
    # checkout cannot append real observations under the frozen revision.
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"refs/tags/{cfg['tag']}:{path}"],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return False
    if result.stdout != REGISTRATION.read_bytes():
        raise market.MarketError("Frozen A126 configuration differs from its git tag")
    return True


def data_root(root, cfg):
    pointer = root / "forward" / "installation.json"
    if not pointer.is_file():
        return root
    record = json.loads(pointer.read_bytes())
    if record["tag"] != cfg["tag"]:
        return root
    destination = Path(record["data_root"])
    if not destination.is_absolute() or not destination.is_dir():
        raise market.MarketError("The installed A126 paper data directory is unavailable")
    return destination


def folder(root, cfg):
    return root / "forward" / cfg["tag"]


def initialize(db, cfg):
    anchor = date.fromisoformat(cfg["anchor_session"])
    seed(db, RUN, anchor, None, False, cfg)
    db.execute("""CREATE TABLE IF NOT EXISTS input_receipts(
        day TEXT PRIMARY KEY, prior TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
        context_sha256 TEXT NOT NULL, conventions_sha256 TEXT NOT NULL)""")


def observe(root, db, cfg, day, prior, terms):
    if str(day) <= cfg["freeze_date"]:
        raise market.MarketError("Paper observations must follow the freeze date")
    daily = root / "sessions" / str(day)
    manifest = market.verify(root, day)
    if manifest["prior_day"] != str(prior):
        raise market.MarketError(
            f"Missing official predecessor: {day} follows {manifest['prior_day']}, expected {prior}"
        )
    context = exchange.context_file(root, day)
    exchange.read_context(root, day, verify_sources=True)
    release = root / "sources" / "forward-conventions" / f"{day}.json"
    if manifest.get("conventions_sha256") != market.sha(release.read_bytes()):
        raise market.MarketError(
            f"Forward closing conventions differ from the published bars on {day}"
        )
    receipt = (
        str(day),
        str(prior),
        market.sha((daily / "manifest.json").read_bytes()),
        market.sha(context.read_bytes()),
        market.sha(release.read_bytes()),
    )
    # Receipts precede the ledger commit. After an interruption they can be refreshed
    # only for an as-yet unvalued session, e.g. once missing official evidence arrives.
    if db.execute("SELECT 1 FROM sessions WHERE run=? AND day=?", (RUN, str(day))).fetchone():
        old = db.execute("SELECT * FROM input_receipts WHERE day=?", (str(day),)).fetchone()
        if old is None or tuple(old) != receipt:
            raise market.MarketError(f"Forward source fingerprint changed on {day}")
        return
    db.execute("INSERT OR REPLACE INTO input_receipts VALUES(?,?,?,?,?)", receipt)
    blocked = {}
    steady_replay._advance_day(
        root,
        db,
        day,
        prior,
        {126: RUN},
        terms,
        {**steady_replay.execution_config(), "allow_short_dividend": True},
        blocked,
    )
    if RUN in blocked:
        issue = blocked[RUN]
        raise market.MarketError(f"{day} {issue['isin']}: {issue['reason']}")


def benchmark_close(directory, day):
    # Publish a cache entry only when the requested official session is present.
    # NSE Indices can temporarily return a valid empty array before publication.
    key = str(day)
    dest = directory / "tri" / key
    if dest.is_dir():
        _, rows = stage3_benchmark.download(dest, None, (day, day))
    else:
        with (
            market.staging(dest.parent) as tmp,
            httpx.Client(
                timeout=25,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": stage3_benchmark.HISTORY,
                    "X-Requested-With": "XMLHttpRequest",
                },
            ) as client,
        ):
            _, rows = stage3_benchmark.download(tmp, client, (day, day))
            if day not in rows:
                raise market.MarketError(f"Awaiting official Nifty 200 TRI on {day}")
            tmp.rename(dest)
    return rows[day]


def catch_up(root, through=None, *, collect_missing=False, now=None):
    from .frozen_inputs import ensure_input, market_view, session_dates

    now = now or datetime.now(market.IST)
    cfg = registration()
    root = data_root(root, cfg)
    if not tagged(cfg):
        raise market.MarketError(f"Awaiting frozen git tag {cfg['tag']}")
    latest = now.date() if now.time() >= time(15, 45) else now.date() - timedelta(days=1)
    until = through or latest
    if until > latest:
        raise market.MarketError("Forward observations require a completed exchange session")
    directory = folder(root, cfg)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "update.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with closing(open_journal(directory / "journal.sqlite")) as db:
            initialize(db, cfg)
            previous = date.fromisoformat(last_state(db, RUN)[0])
            terms = None
            for day in session_dates(root, previous, until):
                if str(day) <= cfg["freeze_date"]:
                    continue
                try:
                    view = market_view(root, cfg) if collect_missing else root
                    if terms is None:
                        terms = steady_actions.terms(root, revised=True, close_verified=True)
                    if collect_missing:
                        if str(previous) != cfg["anchor_session"]:
                            frozen_input = db.execute(
                                "SELECT manifest_sha256 FROM input_receipts WHERE day=?",
                                (str(previous),),
                            ).fetchone()
                            previous_file = view / "sessions" / str(previous) / "manifest.json"
                            if (
                                frozen_input is None
                                or market.sha(previous_file.read_bytes()) != frozen_input[0]
                            ):
                                raise market.MarketError(
                                    f"Previously committed forward input changed for {previous}"
                                )
                        needed = steady_replay.needed([last_state(db, RUN)[1]], str(day), terms)
                        ensure_input(
                            root,
                            view,
                            cfg,
                            day,
                            previous,
                            [{"isin": i} for i in sorted(needed)],
                            terms,
                        )
                    observe(view, db, cfg, day, previous, terms)
                except (OSError, ValueError, httpx.HTTPError) as error:
                    reason = (
                        str(error)
                        if isinstance(error, market.MarketError)
                        else type(error).__name__
                    )
                    write_json(
                        directory / "update.json",
                        {
                            "checked_at": now.isoformat(),
                            "tag": cfg["tag"],
                            "awaiting_session": str(day),
                            "latest_committed_session": str(previous)
                            if str(previous) != cfg["anchor_session"]
                            else None,
                            "reason": reason,
                        },
                    )
                    raise
                previous = day
            # Collect TRI independently of fills. A delayed index never changes an order.
            pending = []
            days = [
                r[0]
                for r in db.execute("SELECT day FROM sessions WHERE run=? ORDER BY day", (RUN,))
            ]
            for key in days:
                try:
                    benchmark_close(directory, date.fromisoformat(key))
                except (httpx.HTTPError, market.MarketError) as e:
                    pending.append({"day": key, "error": str(e)})
            write_json(
                directory / "update.json",
                {
                    "checked_at": now.isoformat(),
                    "tag": cfg["tag"],
                    "latest_committed_session": days[-1] if days else None,
                    "sessions": len(days),
                    "tri_pending": pending,
                },
            )
    return report(root)


def report(root):
    cfg = registration()
    root = data_root(root, cfg)
    active = tagged(cfg)
    directory = folder(root, cfg)
    result = {
        "strategy": "Steady Momentum A126",
        "mode": "forward paper",
        "revision": cfg["tag"],
        "status": cfg["status"],
        "freeze_date": cfg["freeze_date"],
        "anchor_session": cfg["anchor_session"],
        "initial_capital_inr": CAPITAL,
        "freeze_tag_verified": active,
        "sessions": 0,
        "state": "awaiting first completed session",
        "first": None,
        "last": None,
        "average_exposure_pct": None,
        "return_pct": None,
        "max_drawdown_pct": None,
        "one_way_turnover_pct": None,
        "benchmark": SERIES,
        "benchmark_return_pct": None,
        "benchmark_max_drawdown_pct": None,
        "benchmark_exposure_pct": None,
        "benchmark_one_way_turnover_pct": None,
        "benchmark_pending_dates": [],
    }
    check = directory / "update.json"
    if check.is_file():
        status = json.loads(check.read_bytes())
        if status.get("tag") == cfg["tag"]:
            result["last_checked_at"] = status["checked_at"]
            if status.get("awaiting_session"):
                result["data_gap"] = {"day": status["awaiting_session"], "reason": status["reason"]}
                result["state"] = "awaiting verified inputs"
    if not active:
        result["state"] = "awaiting freeze tag"
        return result
    journal = directory / "journal.sqlite"
    if not journal.is_file():
        return result
    import sqlite3

    with closing(sqlite3.connect(f"{journal.resolve().as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT definition FROM books WHERE run=?", (RUN,)).fetchone()
        if row is None or json.loads(row[0]) != cfg:
            raise market.MarketError("Paper ledger and frozen revision differ")
        marks = db.execute(
            "SELECT day,prior FROM sessions WHERE run=? ORDER BY day", (RUN,)
        ).fetchall()
        if not marks:
            return result
        dates = [m["day"] for m in marks]
        if [m["prior"] for m in marks] != [cfg["anchor_session"], *dates[:-1]]:
            raise market.MarketError("Forward paper ledger contains a gap")
        levels = {cfg["anchor_session"]: cfg["anchor_tri"]["close"]}
        missing = []
        for key in dates:
            if not (directory / "tri" / key).is_dir():
                missing.append(key)
            else:
                levels[key] = benchmark_close(directory, date.fromisoformat(key))
        previous_equity = float(CAPITAL)
        rows, exposure, turnover = [], [], []
        for mark in db.execute(
            "SELECT day,prior,equity,invested,turnover FROM sessions WHERE run=? ORDER BY day",
            (RUN,),
        ):
            day, prior = mark["day"], mark["prior"]
            row = {"day": day, "prior": prior, "net": mark["equity"] / previous_equity - 1}
            if not missing:
                row["index"] = levels[day] / levels[prior] - 1
            rows.append(row)
            exposure.append(mark["invested"] / mark["equity"])
            turnover.append(0.5 * mark["turnover"] / previous_equity)
            previous_equity = mark["equity"]
    strategy = steady_metrics.curve(rows, "net", calendar_time=True)
    result.update(
        state="awaiting verified inputs" if result.get("data_gap") else "observing",
        sessions=len(dates),
        first=dates[0],
        last=dates[-1],
        average_exposure_pct=100 * sum(exposure) / len(rows),
        return_pct=strategy["return_pct"],
        max_drawdown_pct=strategy["max_drawdown_pct"],
        one_way_turnover_pct=100 * sum(turnover),
        benchmark_pending_dates=missing,
    )
    if not missing:
        benchmark = steady_metrics.curve(rows, "index", calendar_time=True)
        result.update(
            benchmark_return_pct=benchmark["return_pct"],
            benchmark_max_drawdown_pct=benchmark["max_drawdown_pct"],
            benchmark_exposure_pct=100.0,
            benchmark_one_way_turnover_pct=0.0,
        )
    return result
