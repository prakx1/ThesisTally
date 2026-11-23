import json
from datetime import date

import httpx
import pytest

from thesistally import results_forward_nse as feed

DAY = date(2026, 11, 23)
XML_URL = "https://nsearchives.nseindia.in/corporate/xbrl/result.xml"


def integrated(seq, *, kind="Financials"):
    return {
        "seq_Id": str(seq),
        "type": "Integrated Filing- " + kind,
        "symbol": "DUMMY",
        "qe_Date": "30-SEP-2026",
        "consolidated": "Consolidated",
        "type_Sub": "Original",
        "creation_Date": "23-Nov-2026 15:30:01",
        "broadcast_Date": "23-Nov-2026 15:29:48",
        "revised_Date": None,
        "xbrl": XML_URL,
    }


def mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_integrated_uses_dated_market_pagination_and_keeps_raw_exchange_clocks(monkeypatch):
    monkeypatch.setattr(feed, "PAGE_SIZE", 2)
    calls = []

    def handle(request):
        q = dict(request.url.params)
        n = int(q["page"])
        calls.append(q)
        data = [integrated(5, kind="Governance"), integrated(6)] if n == 1 else [integrated(7)]
        return httpx.Response(200, json={"data": data, "size": 2, "page": n - 1, "totalCount": 3})

    with mock(handle) as http:
        pages, records = feed.collect(http, "integrated", DAY)
    assert [q["page"] for q in calls] == ["1", "2"]
    assert all(q["from_date"] == q["to_date"] == "23-11-2026" and "symbol" not in q for q in calls)
    assert len(pages) == 2 and json.loads(pages[0].raw)["data"][0]["seq_Id"] == "5"
    assert [(r["accession"], r["page_url"]) for r in records] == [
        ("6", pages[0].url),
        ("7", pages[1].url),
    ]
    first = records[0]
    assert first["timestamp_source"] == "creation_Date"
    assert first["announced_at"] == "2026-11-23T15:30:01+05:30"
    assert first["row"]["broadcast_Date"] == "23-Nov-2026 15:29:48"
    assert first["period_end"] == "2026-09-30" and first["basis"] == "consolidated"
    assert first["xml_url"] == XML_URL and first["isin"] is None and first["is_revision"] is False


@pytest.mark.parametrize(
    "issue", ["repeat", "changing-total", "partial", "ignored-page", "wrong-day"]
)
def test_rejects_unreliable_date_and_pagination(monkeypatch, issue):
    monkeypatch.setattr(feed, "PAGE_SIZE", 1)

    def handle(req):
        n = int(req.url.params["page"])
        item = integrated(1 if issue == "repeat" else n)
        if issue == "wrong-day":
            item.update(creation_Date="22-Nov-2026 10:01:03", broadcast_Date=None)
        return httpx.Response(
            200,
            json={
                "data": [] if n == 2 and issue == "partial" else [item],
                "size": 1,
                "page": 0 if issue == "ignored-page" else n - 1,
                "totalCount": 3 if n == 2 and issue == "changing-total" else 2,
            },
        )

    with mock(handle) as http, pytest.raises(ValueError, match="NSE"):
        feed.collect(http, "integrated", DAY)


def test_explicit_empty_page_and_invalid_json():
    with mock(
        lambda req: httpx.Response(200, json={"data": [], "size": 100, "page": 0, "totalCount": 0})
    ) as http:
        pages, records = feed.collect(http, "integrated", DAY)
    assert len(pages) == 1 and not records
    with (
        mock(lambda req: httpx.Response(200, html="<title>Log in</title>")) as http,
        pytest.raises(ValueError),
    ):
        feed.collect(http, "announcements", DAY)


def test_notice_and_structured_clock_never_interchange_and_empty_links_remain_unknown():
    notice = {
        "an_dt": "23-Nov-2026 12:01:00",
        "exchdisstime": "23-Nov-2026 12:01:01",
        "symbol": "DUMMY",
        "sm_isin": "isin",
        "desc": "Outcome of Board Meeting",
        "attchmntFile": "https://nsearchives.nseindia.com/corporate/outcome.pdf",
        "seq_id": "240",
    }
    legacy = {
        "period": "Quarterly",
        "seqNumber": "240",
        "isin": "isin",
        "symbol": "DUMMY",
        "toDate": "30-Sep-2026",
        "consolidated": "Non-Consolidated",
        "broadCastDate": "23-Nov-2026",
        "exchdisstime": None,
        "xbrl": "https://nsearchives.nseindia.in/corporate/xbrl/-",
    }
    with mock(
        lambda req: httpx.Response(
            200, json=[notice, {**notice, "desc": "Prior intimation of board meeting"}]
        )
    ) as http:
        _, notices = feed.collect(http, "announcements", DAY)
    with mock(lambda req: httpx.Response(200, json=[legacy])) as http:
        _, records = feed.collect(http, "legacy-quarterly", DAY)
    assert len(notices) == len(records) == 1
    assert (
        notices[0]["announced_at"] == "2026-11-23T12:01:01+05:30" and notices[0]["kind"] == "notice"
    )
    assert records[0]["basis"] == "standalone" and records[0]["period_end"] == "2026-09-30"
    assert records[0]["xml_url"] is None and records[0]["timestamp_source"] is None
    assert records[0]["announced_at"] is None and records[0]["is_revision"] is None


def test_archived_xml_requires_real_xbrl_and_checks_alternate_official_copy():
    expected = b'<xbrl xmlns="http://www.xbrl.org/2003/instance"><context id="Current"/></xbrl>'
    domains = []

    def answer(req):
        domains.append(req.url.host)
        return httpx.Response(200, content=b"<html/>" if len(domains) == 1 else expected)

    with mock(answer) as http:
        page = feed.fetch_xml(http, XML_URL)
        assert page.raw == expected and page.url == XML_URL
        assert page.retrieved_url.startswith("https://nsearchives.nseindia.com/")
        with pytest.raises(ValueError):
            feed.fetch_xml(http, "https://example.org/result.xml")
    assert domains == ["archives.nseindia.com", "nsearchives.nseindia.com"]
