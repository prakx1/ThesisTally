"""Session-aware foreground scheduler and restartable private journal delivery."""

import json
import os
import shutil
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from . import market, notifier, trading_calendar
from .journal import journal

JOBS = ("session", "instruments", "collect", "scan", "summary", "backup")
SCHEMA = """CREATE TABLE IF NOT EXISTS scheduled_jobs (
    day TEXT NOT NULL, job TEXT NOT NULL, status TEXT NOT NULL, touched REAL NOT NULL,
    error TEXT, PRIMARY KEY(day,job)
)"""


def holidays(root, day):
    path = root / "calendars" / f"{day.year}.json"
    bundled = Path(trading_calendar.__file__).with_name("calendars") / path.name
    if not path.exists() and not bundled.exists():
        refresh_calendar(root, day.year)
    doc = json.loads((path if path.exists() else bundled).read_text())
    return str(day) in doc["special_sessions"] or (
        day.weekday() < 5 and str(day) not in doc["holidays"]
    )


def refresh_calendar(root, year):
    url = "https://www.nseindia.com/api/holiday-master?type=trading"
    with httpx.Client(
        timeout=15, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True
    ) as client:
        client.get("https://www.nseindia.com/")
        r = client.get(url)
        r.raise_for_status()
    days = []
    special = []
    for row in r.json()["CM"]:
        d = datetime.strptime(row["tradingDate"], "%d-%b-%Y").date()
        if d.year == year:
            days.append(str(d))
            if (
                "muhurat" in str(row).lower()
                or "diwali" in str(row.get("description", "")).lower()
                and "laxmi" in str(row).lower()
            ):
                special.append(str(d))
    if not days:
        raise market.MarketError(f"NSE has not supplied a {year} capital-market holiday calendar.")
    doc = {
        "issued": str(market.today()),
        "source_url": url,
        "sha256": market.sha(r.content),
        "holidays": days,
        "special_sessions": special,
    }
    folder = root / "calendars"
    with market.staging(folder) as tmp:
        (tmp / "year.json").write_text(json.dumps(doc, indent=2))
        (tmp / "year.json").replace(folder / f"{year}.json")
    trading_calendar.schedule.cache_clear()


def ping(job, stage=""):
    url = json.loads(os.getenv("RADAR_HEALTHCHECKS_JSON", "{}")).get(job)
    if url:
        try:
            httpx.get(url.rstrip("/") + stage, timeout=6).raise_for_status()
        except httpx.HTTPError:
            # The monitor independently detects an absent heartbeat.
            pass


def backup(root, dest):
    dest = Path(dest).expanduser().resolve()
    root = root.resolve()
    if dest == root or dest in root.parents or root in dest.parents:
        raise market.MarketError("Choose a separate backup directory outside the data tree.")
    dest.mkdir(parents=True, exist_ok=True)
    with market.staging(dest) as tmp:
        for directory, dirs, files in os.walk(root):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".building-") and not (Path(directory) / d).is_symlink()
            ]
            for name in files:
                src = Path(directory) / name
                rel = src.relative_to(root)
                if (
                    rel.parts[0].startswith("journal.sqlite")
                    or src.is_symlink()
                    or name.endswith(".lock")
                ):
                    continue
                out = dest / rel
                if (
                    out.is_file()
                    and out.stat().st_size == src.stat().st_size
                    and out.stat().st_mtime_ns == src.stat().st_mtime_ns
                ):
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, tmp / "item")
                (tmp / "item").replace(out)
        with (
            closing(sqlite3.connect(root / "journal.sqlite")) as db,
            closing(sqlite3.connect(tmp / "journal.sqlite")) as copy,
        ):
            db.backup(copy)
            if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise market.MarketError("SQLite backup failed integrity verification.")
        (tmp / "journal.sqlite").replace(dest / "journal.sqlite")


def execute(root, day, job, func):
    with closing(journal(root / "journal.sqlite")) as db:
        db.execute(SCHEMA)
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,touched FROM scheduled_jobs WHERE day=? AND job=?", (str(day), job)
            ).fetchone()
            if row and (
                row["status"] == "done"
                or row["status"] == "running"
                and time.time() - row["touched"] < 1800
            ):
                return row["status"] == "done"
            db.execute(
                "INSERT OR REPLACE INTO scheduled_jobs VALUES(?,?,'running',?,NULL)",
                (str(day), job, time.time()),
            )
        ping(job, "/start")
        try:
            ready = func()
            status = "waiting" if ready is False else "done"
        except Exception as exc:  # noqa: BLE001 - persist job failures and retry at the next tick
            status = "failed"
            fault = type(exc).__name__
            message = f"{job} on {day} failed ({fault})."
            if job == "backup" and not os.getenv("RADAR_BACKUP_DIR"):
                message += " Set RADAR_BACKUP_DIR to the secondary location."
            if job == "session":
                from urllib.parse import urlencode

                try:
                    key = os.getenv("KITE_API_KEY") or market.credentials()["api_key"]
                    message += " Sign in at https://kite.zerodha.com/connect/login?" + urlencode(
                        {"v": "3", "api_key": key}
                    )
                except market.MarketError:
                    message += " Configure the local Kite session."
            notifier.warning(root, f"{day}:{job}", message)
            ping(job, "/fail")
        else:
            fault = None
            if status == "done":
                ping(job)
        with db:
            db.execute(
                "UPDATE scheduled_jobs SET status=?,touched=?,error=? WHERE day=? AND job=?",
                (status, time.time(), fault, str(day), job),
            )
        return status == "done"


def inputs(root, day):
    folder = root / "daily-inputs" / str(day)
    if folder.is_dir():
        return
    with market.Feed() as feed, market.staging(folder.parent) as tmp:
        (tmp / "instruments.csv").write_bytes(feed.instruments())
        # The prior completed lists are available before the opening auction.
        recent = [s for s in market.sessions(root) if s < day]
        if not recent:
            raise market.MarketError("A previous completed session is required.")
        prior = recent[-1]
        for name, url in {
            "surveillance.csv": f"{market.ARCHIVE}/cm/REG_IND{prior:%d%m%y}.csv",
            "security.csv.gz": f"{market.ARCHIVE}/cm/NSE_CM_security_{prior:%d%m%Y}.csv.gz",
        }.items():
            (tmp / name).write_bytes(feed.get(url).content)
        (tmp / "sources.json").write_text(
            json.dumps(
                {
                    "prior": str(prior),
                    "sha256": {p.name: market.sha(p.read_bytes()) for p in tmp.iterdir()},
                }
            )
        )
        tmp.rename(folder)


def scan_after_close(root, day):
    from .cli import collect_session, scan_session

    def collect():
        if not (root / "sessions" / str(day) / "manifest.json").exists():
            url = f"{market.ARCHIVE}/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
            resp = httpx.head(url, timeout=10)
            if resp.status_code == 404:
                return False
            resp.raise_for_status()
        collect_session(root, day, None, lambda _: None)

    def advance():
        with closing(journal(root / "journal.sqlite")) as db:
            prior = db.execute("SELECT max(day) FROM scans WHERE run='observations'").fetchone()[0]
        if prior and prior < str(day):
            cursor = day
            needed = []
            while str(cursor) > prior:
                path = root / "sessions" / str(cursor) / "manifest.json"
                if not path.exists():
                    from .cli import data_backfill

                    data_backfill(date.fromisoformat(prior) + timedelta(days=1), cursor)
                if not path.is_file():
                    raise market.MarketError(f"Awaiting an official session for {cursor}.")
                previous = date.fromisoformat(json.loads(path.read_text())["prior_day"])
                if previous >= cursor:
                    raise market.MarketError("Invalid predecessor session.")
                needed.append(cursor)
                cursor = previous
            if str(cursor) != prior:
                raise market.MarketError("Archive does not link to the last observation.")
            for d in reversed(needed):
                scan_session(root, d, "observations", None)
        else:
            scan_session(root, day, "observations", None)

    return execute(root, day, "collect", collect) and execute(root, day, "scan", advance)


def tick(root, when=None):

    now = when or datetime.now(market.IST)
    day = now.date()
    minute = now.hour * 60 + now.minute
    if minute >= 23 * 60:

        def nightly():
            dest = os.environ.get("RADAR_BACKUP_DIR")
            if not dest:
                raise market.MarketError("Set RADAR_BACKUP_DIR.")
            backup(root, dest)

        execute(root, day, "backup", nightly)
    if not holidays(root, day):
        return

    def login():
        with market.Feed() as feed:
            feed.session()

    if minute >= 510:
        execute(root, day, "session", login)
        execute(root, day, "instruments", lambda: inputs(root, day))
    if minute < 945:
        return

    if scan_after_close(root, day):
        execute(root, day, "summary", lambda: notifier.deliver(root))
    if minute >= 22 * 60 + 45:
        with closing(journal(root / "journal.sqlite")) as db:
            done = {
                r[0]
                for r in db.execute(
                    "SELECT job FROM scheduled_jobs WHERE day=? AND status='done'", (str(day),)
                )
            }
        for j in set(JOBS[:-1]) - done:
            notifier.warning(
                root, f"{day}:missed:{j}", f"The {j} job for {day} missed its deadline."
            )
            ping(j, "/fail")


def serve(root):
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
    from apscheduler.schedulers.blocking import BlockingScheduler

    with closing(journal(root / "journal.sqlite")) as db:
        db.execute(SCHEMA)
        previous = db.execute("SELECT max(day) FROM scheduled_jobs").fetchone()[0]
    if previous:
        cursor = date.fromisoformat(previous)
        while cursor < market.today():
            if holidays(root, cursor):
                with closing(journal(root / "journal.sqlite")) as db:
                    done = {
                        r[0]
                        for r in db.execute(
                            "SELECT job FROM scheduled_jobs WHERE day=? AND status='done'",
                            (str(cursor),),
                        )
                    }
                for job in set(JOBS) - done:
                    notifier.warning(
                        root, f"{cursor}:missed:{job}", f"{job} did not finish on {cursor}."
                    )
            cursor += timedelta(days=1)
    service = BlockingScheduler(timezone=market.IST)
    service.add_job(
        tick,
        "interval",
        minutes=5,
        args=[root],
        id="exchange",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(market.IST),
    )
    service.add_job(
        notifier.deliver,
        "interval",
        seconds=15,
        args=[root],
        id="telegram",
        max_instances=1,
        coalesce=True,
    )

    from .research_journal import drain

    service.add_job(
        drain,
        "interval",
        seconds=30,
        args=[root],
        id="shadow_research",
        max_instances=1,
        coalesce=True,
    )

    def refresh():
        next_year = market.today().year + 1
        if not (root / "calendars" / f"{next_year}.json").exists():
            refresh_calendar(root, next_year)

    service.add_job(refresh, "cron", month="12", hour=18, minute=30, id="calendar", coalesce=True)

    def issue(ev):
        notifier.warning(
            root,
            f"{market.today()}:{ev.job_id}:scheduler",
            f"The scheduled {ev.job_id} job failed or missed its run.",
        )

    service.add_listener(issue, EVENT_JOB_MISSED | EVENT_JOB_ERROR)
    try:
        service.start()
    except KeyboardInterrupt:
        pass
