"""Exercise the live issuer watch through its isolated research archive."""

import gzip
import json
from contextlib import closing
from datetime import datetime, time, timedelta

import httpx
import pytest

from thesistally import results_forward as archive
from thesistally import results_forward_bse as bse
from thesistally import results_forward_watch as watch
from thesistally.results_forward_nse import Page

START = archive.START
FIRST = datetime.combine(START, time(18), archive.IST)


@pytest.fixture
def db():
    with closing(archive.open_archive(":memory:", START)) as connection:
        yield connection


def test_roster_only_records_the_members_actually_observed_that_day(db, monkeypatch):
    one = {"isin": "INE000000001", "symbol": "ONE", "scrip": "500001"}
    two = {"isin": "INE000000002", "symbol": "TWO", "scrip": None}
    three = {"isin": "INE000000003", "symbol": "THREE", "scrip": "500003"}
    snapshots = iter(([one, two], [two, three]))

    def fetch(nse_http, bse_http):
        members = next(snapshots)
        return [Page("u", "u", json.dumps(members).encode())], members

    monkeypatch.setattr(watch.roster, "fetch", fetch)
    later = FIRST + timedelta(days=5)
    for seen in (FIRST, FIRST, later):
        watch.seed(db, None, None, seen, lambda seen=seen: seen)
    assert [
        tuple(r)
        for r in db.execute(
            "SELECT observed_day,symbol,scrip FROM roster ORDER BY observed_day,isin"
        )
    ] == [
        (str(START), "ONE", "500001"),
        (str(START), "TWO", None),
        (str(later.date()), "TWO", None),
        (str(later.date()), "THREE", "500003"),
    ]
    assert [r[0] for r in db.execute("SELECT scrip FROM issuers ORDER BY scrip")] == [
        "500001",
        "500003",
    ]


def test_result_notice_expedites_once_and_seeds_previously_unseen_issuer(db, monkeypatch):
    page = Page("roster", "roster", b"roster")
    monkeypatch.setattr(
        watch.roster,
        "fetch",
        lambda *_: ([page], [{"isin": "INE000000001", "symbol": "ONE", "scrip": "500001"}]),
    )
    watch.seed(db, None, None, FIRST, lambda: FIRST)
    empty = Page("cat", "cat", b'{"Table":[]}')
    monkeypatch.setattr(bse, "collect_integrated", lambda *_: ([empty], []))
    watch.refresh(db, None, START, FIRST, lambda: FIRST)
    now = FIRST + timedelta(minutes=45)
    with db:
        for scrip in ("500001", "500002"):
            raw = {
                "SCRIP_CD": scrip,
                "NEWSID": "notice-" + scrip,
                "CATEGORYNAME": "Result",
                "DissemDT": now.isoformat(),
            }
            p = Page("notice" + scrip, "notice" + scrip, json.dumps(raw).encode())
            row = bse._notice(raw, now.date(), p.url)
            archive.store_page_set(db, "bse-results", str(START), [p], [row], now.isoformat())
    watch.seed(db, None, None, now, lambda: now)
    assert [
        (r["scrip"], r["due_at"]) for r in db.execute("SELECT * FROM issuers ORDER BY scrip")
    ] == [("500001", now.isoformat()), ("500002", now.isoformat())]
    watch.refresh(db, None, START, now, lambda: now)
    watch.seed(db, None, None, now, lambda: now)
    assert {r[0] for r in db.execute("SELECT due_at FROM issuers")} == {
        (now + timedelta(days=1)).isoformat()
    }
    assert db.execute("SELECT COUNT(*) FROM issuer_notices").fetchone()[0] == 2


def test_oldest_due_issuers_go_first_and_failed_days_are_retried(db, monkeypatch):
    bad, good = "500001", "500002"
    for code in (bad, good):
        watch.enqueue(db, code, START, FIRST, expedite=False)
    calls = []
    page = Page("catalogue", "catalogue", b'{"Table":[]}')
    broken = True

    def fetch(http, code, start, end):
        calls.append((code, start, end))
        if code == bad and broken:
            raise ValueError("missing table")
        return [page], []

    monkeypatch.setattr(bse, "collect_integrated", fetch)
    day = FIRST + timedelta(days=12)
    for _ in range(3):
        watch.refresh(db, None, START, day, lambda: day, limit=1)
    assert calls == [(bad, START, day.date()), (good, START, day.date())]
    assert [
        tuple(r)
        for r in db.execute("SELECT scrip,last_complete_day,error FROM issuers ORDER BY scrip")
    ] == [(bad, None, "missing table"), (good, str(day.date()), None)]
    assert db.execute("SELECT item FROM failures").fetchone()[0] == bad
    broken = False
    retry = day + timedelta(hours=2)
    watch.refresh(db, None, START, retry, lambda: retry, limit=1)
    assert calls[-1] == (bad, START, retry.date())
    assert db.execute("SELECT error FROM issuers WHERE scrip=?", (bad,)).fetchone()[0] is None
    after_outage = retry + timedelta(days=50)
    watch.refresh(db, None, START, after_outage, lambda: after_outage)
    assert calls[-2:] == [
        (good, day.date() - timedelta(days=7), after_outage.date()),
        (bad, retry.date() - timedelta(days=7), after_outage.date()),
    ]


def test_catalogue_refresh_archives_a_later_ixbrl_without_a_new_notice(db):
    code = "532540"
    watch.enqueue(db, code, START, FIRST, expedite=False)
    later = FIRST + timedelta(days=1, minutes=3)
    downloaded = later + timedelta(minutes=2)
    publication = later.replace(hour=15, minute=41, second=29, microsecond=123000)
    html = b"""<html xmlns="http://www.w3.org/1999/xhtml"
    xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
    xmlns:in="urn:in"><body><ix:header><ix:resources><xbrli:context id="q3"/>
    </ix:resources></ix:header><ix:nonFraction contextRef="q3" name="in:Profit"> 127.30 </ix:nonFraction>
    </body></html>"""
    listing = json.dumps(
        {
            "Table": [
                {
                    "Qtrid": 131,
                    "Quarter_Name": "Consolidated-Sep-Qtr-2026",
                    "status": "New",
                    "seourl": "/stock-share-price/company/TCS/532540/",
                    "filing_date_time": publication.replace(tzinfo=None).isoformat(),
                    "xbrlurl": "/XBRLFILES/finance/issuer.html",
                }
            ]
        }
    ).encode()
    present = False

    def handle(request):
        if request.url.path.endswith(".html"):
            return httpx.Response(200, content=html)
        assert request.url.params["scripcode"] == code
        return httpx.Response(200, content=listing if present else b'{"Table":[]}')

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        watch.refresh(db, http, START, FIRST, lambda: FIRST)
        assert db.execute("SELECT count(*) FROM records").fetchone()[0] == 0
        present = True
        watch.refresh(db, http, START, later, lambda: later)
        archive.collect_xml(
            db, http, later, source=bse, extra_feeds=(bse.ISSUER_FEED,), clock=lambda: downloaded
        )
    record = db.execute("SELECT * FROM records").fetchone()
    assert record["feed"] == bse.ISSUER_FEED and record["timestamp_source"] == "filing_date_time"
    assert record["announced_at"] == publication.isoformat()
    assert record["first_seen"] == later.isoformat()
    raw_page = db.execute(
        """SELECT content_gzip FROM objects o JOIN appearances a
                             ON a.page_sha256=o.sha256 WHERE a.record_id=?""",
        (record["id"],),
    ).fetchone()
    assert gzip.decompress(raw_page[0]) == listing
    version = db.execute(
        """SELECT v.first_seen,o.content_gzip FROM xml_versions v
                            JOIN objects o USING(sha256) WHERE record_id=?""",
        (record["id"],),
    ).fetchone()
    assert version["first_seen"] == downloaded.isoformat()
    assert gzip.decompress(version["content_gzip"]) == html
