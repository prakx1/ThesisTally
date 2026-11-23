from datetime import date

import httpx
import pytest

from thesistally import results_forward_bse as bse

DAY = date(2026, 11, 23)


def announcement(key):
    return {
        "NEWSID": key,
        "SCRIP_CD": 1234,
        "CATEGORYNAME": "Result",
        "NEWS_DT": "2026-11-23T15:29:59.999",
        "DissemDT": "2026-11-23T15:30:00.017",
        "XML_NAME": "ANN_1234_AAAA",
        "ATTACHMENTNAME": "published.pdf",
    }


def test_bse_dated_market_pages_and_dissemination_time(monkeypatch):
    monkeypatch.setattr(bse, "PAGE_SIZE", 1)

    def get(request):
        values = request.url.params
        assert values["strPrevDate"] == values["strToDate"] == "20261123"
        assert values["strCat"] == "Result" and not values["strScrip"]
        return httpx.Response(
            200, json={"Table": [announcement(values["pageno"])], "Table1": [{"ROWCNT": 2}]}
        )

    with httpx.Client(transport=httpx.MockTransport(get)) as http:
        pages, records = bse.collect(http, "bse-results", DAY)
    assert len(pages) == 2 and [r["accession"] for r in records] == ["1", "2"]
    assert records[1]["page_url"] == pages[1].url and records[0]["xml_url"] is None
    assert records[0]["announced_at"] == "2026-11-23T15:30:00.017000+05:30"
    assert records[0]["timestamp_source"] == "DissemDT"


@pytest.mark.parametrize("issue", ["duplicate", "not-dated", "empty-shell", "new-total"])
def test_incomplete_or_undated_bse_data_never_certifies_an_empty_day(monkeypatch, issue):
    monkeypatch.setattr(bse, "PAGE_SIZE", 1)

    def get(req):
        page = int(req.url.params["pageno"])
        row = announcement("same" if issue == "duplicate" else str(page))
        if issue == "not-dated":
            row["DissemDT"] = row["NEWS_DT"] = "2026-11-20T12:01:00"
        if issue == "empty-shell":
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "Table": [row],
                "Table1": [{"ROWCNT": 3 if issue == "new-total" and page == 2 else 2}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(get)) as http, pytest.raises(ValueError):
        bse.collect(http, "bse-results", DAY)


def test_bse_forbidden_is_a_retryable_gap_and_rss_is_not_a_dated_result_list():
    with (
        httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(403))) as http,
        pytest.raises(httpx.HTTPStatusError),
    ):
        bse.collect(http, "bse-results", DAY)
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, content=b"<rss><channel/></rss>")
            )
        ) as http,
        pytest.raises(ValueError),
    ):
        bse.collect(http, "bse-results", DAY)


def test_bse_attachment_requires_an_explicit_official_url_and_real_instance():
    good = "https://www.bseindia.com/xbrl/original.xml"
    raw = b'<xbrl xmlns="http://www.xbrl.org/2003/instance"><context id="2026"/></xbrl>'
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=raw))
    ) as http:
        assert bse.fetch_xml(http, good).raw == raw
        with pytest.raises(ValueError):
            bse.fetch_xml(http, "https://notbseindia.com/document.xml")
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"<html/>"))
        ) as http,
        pytest.raises(ValueError),
    ):
        bse.fetch_xml(http, good)


def filing(status="New", **extra):
    return {
        "Qtrid": 131,
        "Quarter_Name": "Consolidated-Sep-Qtr-2026",
        "status": status,
        "filing_date_time": "2026-10-08T22:05:59.38",
        "revised_date_time": None,
        "seourl": "/stock-share-price/company/TCS/532540/",
        "xbrlurl": "/XBRLFILES/finance/original.html",
        **extra,
    }


def test_bse_issuer_original_and_editions_have_their_own_clock_and_raw_provenance():
    original = filing()
    revision = filing(
        "Revised",
        revised_date_time="2026-11-23T15:31:15.17",
        xbrlurl="/XBRLFILES/finance/revision.xml",
    )

    def get(request):
        assert request.url.params["scripcode"] == "532540"
        return httpx.Response(200, json={"Table": [original, revision]})

    with httpx.Client(transport=httpx.MockTransport(get)) as http:
        pages, records = bse.collect_integrated(http, "532540", date(2026, 10, 1), DAY)
        _, only_revision = bse.collect_integrated(http, "532540", DAY, DAY)
    assert only_revision == [records[1]]
    a, b = records
    assert a["feed"] == b["feed"] == bse.ISSUER_FEED
    assert a["period_end"] == "2026-09-30" and a["basis"] == "consolidated"
    assert a["is_revision"] is False and b["is_revision"] is True
    assert a["accession"] != b["accession"] and a["page_url"] == b["page_url"] == pages[0].url
    assert b["row"] == revision and b["announced_at"] == "2026-11-23T15:31:15.170000+05:30"
    assert b["timestamp_source"] == "revised_date_time"
    assert a["xml_url"] == "https://www.bseindia.com/XBRLFILES/finance/original.html"


@pytest.mark.parametrize(
    "row",
    [
        filing("Revised", revised_date_time=None),
        filing(seourl="/another-issuer/599999/"),
    ],
)
def test_invalid_issuer_publication_is_retryable(row):
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"Table": [row]}))
        ) as http,
        pytest.raises(ValueError),
    ):
        bse.collect_integrated(http, "532540", DAY, DAY)


def test_latest_market_snapshot_only_seeds_issuers_from_actual_rows():
    def get(request):
        assert request.url.params["FlagDur"] == "1" and request.url.params["SCRIP_CD"] == ""
        return httpx.Response(
            200, json={"Table": [{"Scrip_cd": 532540, "Fld_CreateDate": "2026-11-21T14:05:10"}] * 2}
        )

    with httpx.Client(transport=httpx.MockTransport(get)) as http:
        source, codes = bse.latest_issuers(http)
    assert codes == {"532540"} and b"2026-11-21" in source.raw


@pytest.mark.parametrize("real", [True, False])
def test_bse_html_requires_inline_fact_linked_to_an_actual_instance_context(real):
    html = b"""<html xmlns="http://www.w3.org/1999/xhtml"
        xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance">
        <xbrli:context id="qtr"/><ix:nonFraction name="sebi:EPS" contextRef="qtr">2.1</ix:nonFraction></html>"""
    if not real:
        html = html.replace(b'contextRef="qtr"', b'contextRef="dangling"')
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=html))
    ) as http:
        if real:
            assert bse.fetch_xml(http, "https://www.bseindia.com/XBRLFILES/q.html").raw == html
        else:
            with pytest.raises(ValueError):
                bse.fetch_xml(http, "https://www.bseindia.com/XBRLFILES/q.html")
