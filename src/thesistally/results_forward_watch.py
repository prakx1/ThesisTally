"""Refresh BSE issuer catalogues, including issuers with no new board notice."""

from datetime import date, timedelta

from . import results_forward_bse as bse
from . import results_forward_roster as roster

SCHEMA = """
CREATE TABLE IF NOT EXISTS roster (
    observed_day TEXT, isin TEXT, symbol TEXT NOT NULL, scrip TEXT,
    PRIMARY KEY(observed_day,isin)
);
CREATE TABLE IF NOT EXISTS issuers (
    scrip TEXT PRIMARY KEY, refresh_through TEXT NOT NULL, due_at TEXT NOT NULL,
    last_complete_day TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS issuer_notices (
    record_id TEXT PRIMARY KEY REFERENCES records
);
"""


def enqueue(db, code, day, when, *, expedite):
    db.execute(
        """INSERT INTO issuers(scrip,refresh_through,due_at) VALUES(?,?,?)
           ON CONFLICT(scrip) DO UPDATE SET
           refresh_through=MAX(refresh_through,excluded.refresh_through),
           due_at=CASE WHEN ? THEN MIN(due_at,excluded.due_at) ELSE due_at END""",
        (code, str(day + timedelta(days=30)), when.isoformat(), expedite),
    )


def seed(db, nse_http, bse_http, now, clock):
    import httpx

    from .results_forward import store_page_set

    day = now.date()
    if not db.execute("SELECT 1 FROM roster WHERE observed_day=? LIMIT 1", (str(day),)).fetchone():
        try:
            pages, members = roster.fetch(nse_http, bse_http)
            at = clock()
            with db:
                store_page_set(db, "issuer-roster", str(day), pages, [], at.isoformat())
                for member in members:
                    db.execute(
                        "INSERT INTO roster VALUES(?,?,?,?)",
                        (str(day), member["isin"], member["symbol"], member["scrip"]),
                    )
                    if member["scrip"]:
                        enqueue(db, member["scrip"], day, at, expedite=False)
        except (ValueError, KeyError, TypeError, OSError, httpx.HTTPError) as exc:
            at = clock().isoformat()
            with db:
                db.execute(
                    "INSERT INTO scans VALUES(?,?,?,NULL,?) ON CONFLICT(feed,query_day) DO UPDATE SET last_attempt=excluded.last_attempt,error=excluded.error",
                    ("issuer-roster", str(day), at, str(exc)),
                )
                db.execute(
                    "INSERT INTO failures(observed_at,source,item,message) VALUES(?,?,?,?)",
                    (at, "issuer-roster", str(day), str(exc)),
                )
    fresh = db.execute(
        """SELECT id,symbol FROM records WHERE feed='bse-results'
           AND NOT EXISTS (SELECT 1 FROM issuer_notices WHERE record_id=records.id) ORDER BY id"""
    ).fetchall()
    with db:
        for row in fresh:
            if not row["symbol"].isdigit():
                raise ValueError("Unexpected BSE issuer identifier")
            enqueue(db, row["symbol"], day, now, expedite=True)
            db.execute("INSERT INTO issuer_notices VALUES(?)", (row["id"],))


def market_issuers(db, http, now, clock):
    import httpx

    from .results_forward import store_page_set

    feed = "bse-latest-issuer-discovery"
    day = str(now.date())
    try:
        page, codes = bse.latest_issuers(http)
        at = clock()
        with db:
            store_page_set(db, feed, day, [page], [], at.isoformat())
            for code in sorted(codes):
                enqueue(db, code, now.date(), at, expedite=False)
    except (ValueError, KeyError, TypeError, OSError, httpx.HTTPError) as exc:
        at = clock().isoformat()
        with db:
            db.execute(
                "INSERT INTO scans VALUES(?,?,?,NULL,?) ON CONFLICT(feed,query_day) DO UPDATE SET last_attempt=excluded.last_attempt,error=excluded.error",
                (feed, day, at, str(exc)),
            )
            db.execute(
                "INSERT INTO failures(observed_at,source,item,message) VALUES(?,?,?,?)",
                (at, feed, day, str(exc)),
            )


def refresh(db, http, since, now, clock, limit=24):
    import httpx

    from .results_forward import store_page_set

    today = now.date()
    due = db.execute(
        """SELECT * FROM issuers WHERE due_at<=? AND
           (refresh_through>=? OR last_complete_day IS NULL OR last_complete_day<refresh_through)
           ORDER BY due_at,scrip LIMIT ?""",
        (now.isoformat(), str(today), limit),
    ).fetchall()
    for issuer in due:
        start = since
        if issuer["last_complete_day"]:
            start = max(since, date.fromisoformat(issuer["last_complete_day"]) - timedelta(days=7))
        code = issuer["scrip"]
        try:
            pages, rows = bse.collect_integrated(http, code, start, today)
            at = clock()
            with db:
                store_page_set(db, "bse-issuer-xbrl", str(today), pages, rows, at.isoformat())
                db.execute(
                    "UPDATE issuers SET due_at=?,last_complete_day=?,error=NULL WHERE scrip=?",
                    ((at + timedelta(hours=24)).isoformat(), str(today), code),
                )
        except (ValueError, KeyError, TypeError, OSError, httpx.HTTPError) as exc:
            at = clock()
            with db:
                db.execute(
                    "UPDATE issuers SET due_at=?,error=? WHERE scrip=?",
                    ((at + timedelta(hours=2)).isoformat(), str(exc), code),
                )
                db.execute(
                    "INSERT INTO failures(observed_at,source,item,message) VALUES(?,?,?,?)",
                    (at.isoformat(), "bse-issuer-xbrl", code, str(exc)),
                )
