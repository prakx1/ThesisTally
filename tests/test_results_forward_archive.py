import gzip
from datetime import date, datetime, timedelta

from thesistally import results_forward as forward
from thesistally import results_forward_nse as source

DAY = date(2026, 11, 23)
NOW = datetime(2026, 11, 23, 17, tzinfo=forward.IST)
URL = "https://nsearchives.nseindia.com/file.xml"


def filing(values, stamp=None):
    url = "https://www.nseindia.in/api/results"
    page = source.Page(url, url, values.encode())
    row = {
        "feed": "integrated",
        "kind": "filing",
        "accession": "A",
        "symbol": "AAA",
        "isin": None,
        "period_end": "2026-09-30",
        "basis": "consolidated",
        "announced_at": stamp,
        "timestamp_source": None,
        "xml_url": URL,
        "page_url": url,
        "row": {"value": values},
    }
    return [page], [row]


def test_complete_raw_versions_and_exchange_clock_survive_repoll_and_xml_failure(
    tmp_path, monkeypatch
):
    db = forward.open_archive(tmp_path / "store.sqlite", DAY)
    monkeypatch.setattr(source, "FEEDS", ("integrated",))
    monkeypatch.setattr(source, "collect", lambda *args: filing("first"))

    def unavailable(*args):
        raise ValueError("unavailable")

    monkeypatch.setattr(source, "fetch_xml", unavailable)
    forward.collect_catalogues(db, None, DAY, NOW, clock=lambda: NOW)
    forward.collect_xml(db, None, NOW, clock=lambda: NOW)
    assert forward.status(db)["outstanding_xml_errors"] == 1
    assert db.execute("SELECT announced_at FROM records").fetchone()[0] is None
    # Recover the exact archived bytes after the backlog has aged out of its catalogue.
    later = NOW + timedelta(days=12)
    monkeypatch.setattr(source, "fetch_xml", lambda *args: source.Page(URL, URL, b"first bytes"))
    forward.collect_xml(db, None, later, clock=lambda: later)
    assert forward.status(db)["outstanding_xml_errors"] == 0
    # A second XML version can appear at the same URL without a new accession.
    tomorrow = later + timedelta(days=1)
    monkeypatch.setattr(source, "fetch_xml", lambda *args: source.Page(URL, URL, b"reissued"))
    with db:
        forward.store_page_set(
            db, "integrated", str(tomorrow.date()), *filing("first"), tomorrow.isoformat()
        )
    forward.collect_xml(db, None, tomorrow, clock=lambda: tomorrow)
    raw = db.execute("SELECT content_gzip FROM objects JOIN xml_versions USING(sha256)").fetchall()
    assert {gzip.decompress(row[0]) for row in raw} == {b"first bytes", b"reissued"}
    with db:
        forward.store_page_set(
            db, "integrated", str(DAY), *filing("revised", NOW.isoformat()), tomorrow.isoformat()
        )
        forward.store_page_set(
            db, "integrated", str(DAY), *filing("revised", NOW.isoformat()), tomorrow.isoformat()
        )
    forward.collect_xml(db, None, tomorrow, clock=lambda: tomorrow)
    assert forward.status(db)["filing_versions"] == 2
    assert forward.status(db)["records_with_xml"] == 2


def test_missed_days_and_failed_feed_are_retried_without_replacing_last_good_snapshot(
    tmp_path, monkeypatch
):
    db = forward.open_archive(tmp_path / "store.sqlite", DAY)
    monkeypatch.setattr(source, "FEEDS", ("integrated",))
    monkeypatch.setattr(source, "collect", lambda *args: filing("first"))
    forward.collect_catalogues(db, None, DAY, NOW, clock=lambda: NOW)
    later = NOW + timedelta(days=2)

    def bad(*args):
        p, rows = filing("corrupt")
        rows[0]["page_url"] += "wrong"
        return p, rows

    monkeypatch.setattr(source, "collect", bad)
    forward.collect_catalogues(db, None, DAY, later, clock=lambda: later)
    assert db.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 1
    assert (
        db.execute("SELECT last_success FROM scans WHERE query_day=?", (str(DAY),)).fetchone()[0]
        == NOW.isoformat()
    )
    assert forward.status(db)["outstanding_feed_errors"] == 3
    monkeypatch.setattr(source, "collect", lambda *args: filing("recovered"))
    forward.collect_catalogues(db, None, DAY, later, clock=lambda: later)
    assert forward.status(db)["outstanding_feed_errors"] == 0
    assert set(forward.days_due(db, "integrated", DAY, later)) == {later.date()}
    assert set(forward.days_due(db, "integrated", DAY, later + timedelta(days=1))) == {
        DAY,
        DAY + timedelta(days=1),
        later.date(),
        later.date() + timedelta(days=1),
    }


def test_failed_revisit_outside_overlap_is_retried_and_observations_use_receipt_time(
    tmp_path, monkeypatch
):
    db = forward.open_archive(tmp_path / "store.sqlite", DAY)
    previous = NOW + timedelta(days=5)
    db.execute(
        "INSERT INTO scans VALUES(?,?,?,?,?)",
        (
            "integrated",
            str(DAY),
            previous.isoformat(),
            previous.isoformat(),
            "failed on later revisit",
        ),
    )
    assert DAY in forward.days_due(db, "integrated", DAY, NOW + timedelta(days=9))
    monkeypatch.setattr(source, "FEEDS", ("integrated",))
    monkeypatch.setattr(source, "collect", lambda *args: filing("new", NOW.isoformat()))
    collected = NOW + timedelta(minutes=3)
    forward.collect_catalogues(db, None, DAY, NOW, clock=lambda: collected)
    monkeypatch.setattr(source, "fetch_xml", lambda *args: source.Page(URL, URL, b"new bytes"))
    fetched = NOW + timedelta(minutes=8)
    forward.collect_xml(db, None, NOW, clock=lambda: fetched)
    assert db.execute("SELECT first_seen FROM xml_versions").fetchone()[0] == fetched.isoformat()
    assert list(db.execute("SELECT first_seen,announced_at FROM records").fetchone()) == [
        collected.isoformat(),
        NOW.isoformat(),
    ]
