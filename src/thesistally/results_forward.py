"""Archive public exchange results in a dedicated, append-only forward source database."""

import argparse
import fcntl
import gzip
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import results_forward_bse as bse
from . import results_forward_nse as nse
from . import results_forward_watch as watch

IST = ZoneInfo("Asia/Kolkata")
START = date(2026, 11, 23)
SUPPORT = Path.home() / "Library/Application Support/ThesisTally-results"
ARCHIVE = SUPPORT / "data/fundamentals/forward"
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS objects (
    sha256 TEXT PRIMARY KEY, content_gzip BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
    feed TEXT, query_day TEXT, url TEXT, retrieved_url TEXT, sha256 TEXT REFERENCES objects,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    PRIMARY KEY(feed, query_day, url, retrieved_url, sha256)
);
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY, feed TEXT NOT NULL, accession TEXT NOT NULL, kind TEXT NOT NULL,
    symbol TEXT NOT NULL, isin TEXT, period_end TEXT, basis TEXT, announced_at TEXT,
    timestamp_source TEXT, xml_url TEXT, data_json TEXT NOT NULL, first_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS record_accession ON records(feed, accession);
CREATE TABLE IF NOT EXISTS appearances (
    record_id TEXT REFERENCES records, page_url TEXT, page_sha256 TEXT REFERENCES objects,
    query_day TEXT, PRIMARY KEY(record_id, page_url, page_sha256, query_day)
);
CREATE TABLE IF NOT EXISTS scans (
    feed TEXT, query_day TEXT, last_attempt TEXT, last_success TEXT, error TEXT,
    PRIMARY KEY(feed, query_day)
);
CREATE TABLE IF NOT EXISTS xml_jobs (
    record_id TEXT PRIMARY KEY REFERENCES records, url TEXT NOT NULL,
    last_seen TEXT NOT NULL, last_check TEXT, last_success TEXT, next_attempt TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS xml_versions (
    record_id TEXT REFERENCES records, sha256 TEXT REFERENCES objects,
    retrieved_url TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    PRIMARY KEY(record_id, sha256, retrieved_url)
);
CREATE TABLE IF NOT EXISTS failures (
    id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, source TEXT NOT NULL,
    item TEXT NOT NULL, message TEXT NOT NULL
);
"""


def open_archive(path, since):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA + watch.SCHEMA)
    with db:
        db.execute("INSERT OR IGNORE INTO settings VALUES('since', ?)", (since.isoformat(),))
    stored = db.execute("SELECT value FROM settings WHERE name='since'").fetchone()[0]
    if stored != since.isoformat():
        db.close()
        raise ValueError("The collection start date differs from the existing archive")
    return db


def archive_object(db, raw):
    sha = hashlib.sha256(raw).hexdigest()
    if not db.execute("SELECT 1 FROM objects WHERE sha256=?", (sha,)).fetchone():
        db.execute("INSERT INTO objects VALUES(?, ?)", (sha, gzip.compress(raw, mtime=0)))
    return sha


def days_due(db, feed, since, now):
    today = now.date()
    saved = {
        date.fromisoformat(s["query_day"]): s
        for s in db.execute("SELECT * FROM scans WHERE feed=?", (feed,))
    }
    backlog = []
    for n in range((today - since).days):
        day = since + timedelta(days=n)
        scan = saved.get(day)
        # Revisit the preceding week once each day for delayed postings. An
        # outage spanning weeks keeps its own missing days until all succeed.
        revisit = today if day >= today - timedelta(days=7) else day + timedelta(days=1)
        if (
            not scan
            or scan["error"]
            or not scan["last_success"]
            or scan["last_success"][:10] < revisit.isoformat()
        ):
            backlog.append((scan["last_attempt"] if scan else "", day))
    return [today, *(day for _, day in sorted(backlog)[:14])] if today >= since else []


def store_page_set(db, feed, day, pages, records, seen):
    if not pages:
        raise ValueError("A successful feed must include its official response, even when empty")
    sources = {}
    for page in pages:
        sha = archive_object(db, page.raw)
        sources[page.url] = sha
        db.execute(
            """INSERT INTO pages VALUES(?,?,?,?,?,?,?) ON CONFLICT
               DO UPDATE SET last_seen=excluded.last_seen""",
            (feed, day, page.url, page.retrieved_url, sha, seen, seen),
        )
    for case in records:
        row = dict(case)
        url = row.pop("page_url")
        if row["feed"] != feed or url not in sources:
            raise ValueError("Result row is not bound to its original response")
        body = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        key = hashlib.sha256(body.encode()).hexdigest()
        db.execute(
            """INSERT OR IGNORE INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                key,
                feed,
                row["accession"],
                row["kind"],
                row["symbol"],
                row["isin"],
                row["period_end"],
                row["basis"],
                row["announced_at"],
                row["timestamp_source"],
                row["xml_url"],
                body,
                seen,
            ),
        )
        db.execute(
            "INSERT OR IGNORE INTO appearances VALUES(?,?,?,?)", (key, url, sources[url], day)
        )
        if row["xml_url"]:
            db.execute(
                """INSERT INTO xml_jobs(record_id,url,last_seen) VALUES(?,?,?)
                   ON CONFLICT(record_id) DO UPDATE SET last_seen=excluded.last_seen""",
                (key, row["xml_url"], seen),
            )
    db.execute(
        """INSERT INTO scans VALUES(?,?,?,?,NULL) ON CONFLICT(feed,query_day) DO UPDATE SET
           last_attempt=excluded.last_attempt,last_success=excluded.last_success,error=NULL""",
        (feed, day, seen, seen),
    )


def exchange_now():
    return datetime.now(IST)


def collect_catalogues(db, http, since, now, *, source=nse, clock=exchange_now):
    for feed in source.FEEDS:
        for day in days_due(db, feed, since, now):
            try:
                pages, rows = source.collect(http, feed, day)
                seen = clock().isoformat()
                with db:
                    store_page_set(db, feed, day.isoformat(), pages, rows, seen)
            except (ValueError, KeyError, TypeError, OSError, httpx.HTTPError) as exc:
                seen = clock().isoformat()
                with db:
                    db.execute(
                        """INSERT INTO scans VALUES(?,?,?,NULL,?) ON CONFLICT(feed,query_day)
                           DO UPDATE SET last_attempt=excluded.last_attempt,error=excluded.error""",
                        (feed, day.isoformat(), seen, str(exc)),
                    )
                    db.execute(
                        "INSERT INTO failures(observed_at,source,item,message) VALUES(?,?,?,?)",
                        (seen, feed, day.isoformat(), str(exc)),
                    )


def collect_xml(db, http, now, *, source=nse, extra_feeds=(), clock=exchange_now):
    seen = now.isoformat()
    # Failed downloads remain eligible independently of catalogue lookback.
    # An unchanged listing can also acquire new bytes at its original URL.
    due = db.execute(
        """SELECT record_id,url FROM xml_jobs WHERE record_id IN (
               SELECT id FROM records WHERE feed IN (SELECT value FROM json_each(?))) AND (
           (error IS NOT NULL AND next_attempt<=?)
           OR (error IS NULL AND (last_check IS NULL OR
               (substr(last_check,1,10) < ? AND substr(last_seen,1,10) = ?)))
            ) ORDER BY last_check IS NOT NULL, last_check, url""",
        (json.dumps((*source.FEEDS, *extra_feeds)), seen, str(now.date()), str(now.date())),
    )
    by_url = defaultdict(list)
    for row in due:
        by_url[row["url"]].append(row["record_id"])
    for url, keys in by_url.items():
        try:
            page = source.fetch_xml(http, url)
            seen = clock().isoformat()
        except (ValueError, OSError, httpx.HTTPError) as exc:
            checked = clock()
            seen = checked.isoformat()
            with db:
                db.executemany(
                    "UPDATE xml_jobs SET last_check=?,next_attempt=?,error=? WHERE record_id=?",
                    [(seen, (checked + timedelta(hours=2)).isoformat(), str(exc), k) for k in keys],
                )
                db.execute(
                    "INSERT INTO failures(observed_at,source,item,message) VALUES(?,?,?,?)",
                    (seen, "xml", url, str(exc)),
                )
            continue
        with db:
            sha = archive_object(db, page.raw)
            db.executemany(
                """INSERT INTO xml_versions VALUES(?,?,?,?,?) ON CONFLICT
                   DO UPDATE SET last_seen=excluded.last_seen""",
                [(k, sha, page.retrieved_url, seen, seen) for k in keys],
            )
            db.executemany(
                "UPDATE xml_jobs SET last_check=?,last_success=?,next_attempt=NULL,error=NULL WHERE record_id=?",
                [(seen, seen, k) for k in keys],
            )


def status(db):
    queries = {
        "filing_versions": "SELECT COUNT(*) FROM records WHERE kind='filing'",
        "notice_versions": "SELECT COUNT(*) FROM records WHERE kind='notice'",
        "records_with_xml": "SELECT COUNT(DISTINCT record_id) FROM xml_versions",
        "distinct_xml_objects": "SELECT COUNT(DISTINCT sha256) FROM xml_versions",
        "filings_without_xml_link": "SELECT COUNT(*) FROM records WHERE kind='filing' AND xml_url IS NULL",
        "records_without_exchange_clock": "SELECT COUNT(*) FROM records WHERE announced_at IS NULL",
        "outstanding_xml_errors": "SELECT COUNT(*) FROM xml_jobs WHERE error IS NOT NULL",
        "outstanding_feed_errors": """SELECT COUNT(*) FROM scans WHERE error IS NOT NULL AND
            (feed NOT IN ('issuer-roster','bse-latest-issuer-discovery')
             OR query_day=date('now','+5 hours','+30 minutes'))""",
        "historical_snapshot_gaps": """SELECT COUNT(*) FROM scans WHERE error IS NOT NULL
            AND feed IN ('issuer-roster','bse-latest-issuer-discovery')
            AND query_day<date('now','+5 hours','+30 minutes')""",
        "issuer_monitors": "SELECT COUNT(*) FROM issuers",
        "issuer_monitors_awaiting_first_check": "SELECT COUNT(*) FROM issuers WHERE last_complete_day IS NULL",
        "outstanding_issuer_errors": "SELECT COUNT(*) FROM issuers WHERE error IS NOT NULL",
        "catalogue_days_observed": "SELECT COUNT(DISTINCT query_day) FROM pages",
        "last_catalogue_success": "SELECT MAX(last_success) FROM scans",
    }
    return {key: db.execute(sql).fetchone()[0] for key, sql in queries.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true")
    opts = parser.parse_args()
    path = ARCHIVE / "filings.sqlite3"
    if opts.status:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            print(json.dumps(status(db), indent=2))
        return
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    with (ARCHIVE / "collection.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        now = datetime.now(IST)
        with closing(open_archive(path, START)) as db:
            with nse.client() as nse_http, bse.client() as bse_http:
                collect_catalogues(db, nse_http, START, now, source=nse)
                collect_xml(db, nse_http, exchange_now(), source=nse)
                collect_catalogues(db, bse_http, START, now, source=bse)
                watch.seed(db, nse_http, bse_http, now, exchange_now)
                watch.market_issuers(db, bse_http, now, exchange_now)
                watch.refresh(db, bse_http, START, exchange_now(), exchange_now)
                collect_xml(
                    db, bse_http, exchange_now(), source=bse, extra_feeds=("bse-issuer-xbrl",)
                )
            result = status(db)
        print(json.dumps({"checked_at": now.isoformat(), **result}), flush=True)
        if any(
            result[k]
            for k in (
                "outstanding_feed_errors",
                "outstanding_xml_errors",
                "outstanding_issuer_errors",
            )
        ):
            sys.exit(1)


if __name__ == "__main__":
    main()
