import gzip
from datetime import date
from pathlib import Path

import pytest

from thesistally.fundamental_facts import html_facts, xml_facts
from thesistally.fundamental_notice_proof import candidates, period_match
from thesistally.fundamental_nse import published_at

EXAMPLES = Path(__file__).resolve().parents[1] / "data" / "fundamentals" / "examples"


def test_official_tcs_quarterly_old_table_and_xbrl():
    raw = gzip.decompress((EXAMPLES / "tcs-sep2014.html.gz").read_bytes())
    row = {
        "symbol": "TCS",
        "basis": "consolidated",
        "metadata": {
            "fromDate": "01-Jul-2014",
            "toDate": "30-Sep-2014",
            "period": "Quarterly",
            "bank": "N",
        },
    }
    facts, tags = html_facts(raw, row)
    assert facts == {
        "revenue": 238164800000,
        "operating_profit": 63511900000,
        "net_profit": 53122700000,
        "eps": 26.78,
    }
    with pytest.raises(ValueError, match="accounting basis"):
        html_facts(raw, {**row, "basis": "standalone"})
    row["metadata"]["fromDate"] = "01-Apr-2014"
    with pytest.raises(ValueError, match="cumulative"):
        html_facts(raw, row)
    raw = gzip.decompress((EXAMPLES / "tcs-sep2023.xml.gz").read_bytes())
    row = {
        "symbol": "TCS",
        "isin": "INE467B01029",
        "period_end": "2023-09-30",
        "basis": "consolidated",
    }
    facts, tags = xml_facts(raw, row)
    assert facts == {
        "revenue": 596920000000,
        "operating_profit": 144830000000,
        "net_profit": 113800000000,
        "eps": 31,
    }
    assert tags["operating_profit"] == "RevenueFromOperations-Expenses+FinanceCosts"
    # The source's FourD repeats the calendar duration but contains YTD EPS of 61.26.
    with pytest.raises(ValueError, match="basis mismatch"):
        xml_facts(raw, {**row, "basis": "standalone"})


def test_official_banking_quarterly_opex_units():
    raw = gzip.decompress((EXAMPLES / "sbin-sep2026.xml.gz").read_bytes())
    facts, _ = xml_facts(
        raw,
        {
            "symbol": "SBIN",
            "isin": "INE062A01020",
            "period_end": "2026-09-30",
            "basis": "consolidated",
        },
    )
    assert facts == {
        "revenue": 1877561900000,
        "operating_profit": 315918400000,
        "net_profit": 257414700000,
        "eps": 27.47,
    }


def test_only_a_real_dissemination_timestamp_can_anchor_a_notice():
    assert published_at("30-Sep-2014") is None
    assert published_at("16-Oct-2014 16:35:02").hour == 16
    assert period_match("for the period ended 30-Sep-2014", date(2014, 9, 30))
    row = {
        "isin": "INE467B01029",
        "period_end": "2014-09-30",
        "filed_at": "2014-10-21T12:16:04+05:30",
    }
    n = {
        "isin": row["isin"],
        "description": "Financial Result Updates",
        "text": "the financial results for the period ended 30-Sep-2014",
        "announced_at": "2014-10-16T16:35:02+05:30",
        "source_url": "https://nsearchives.nseindia.in/corporate/original.zip",
    }
    assert candidates(
        row, [n, {**n, "isin": "OTHER"}, {**n, "announced_at": "2014-11-01T00:00:00+05:30"}]
    ) == [n]


def test_earlier_missing_records_and_unknown_notice_tapes_cannot_be_promoted(tmp_path, monkeypatch):
    import json

    from thesistally import fundamental_nse_ledger as ledger
    from thesistally.fundamental_nse import digest

    first = {
        "id": "one",
        "isin": "ISIN",
        "symbol": "STOCK",
        "basis": "consolidated",
        "period_end": "2022-03-31",
        "filed_at": "2022-05-01T14:00:00+05:30",
        "source_url": "https://archives.nseindia.com/f.xml",
        "catalogue_url": "https://www.nseindia.in/api/integrated-filing-results",
        "metadata": {"type_Sub": "Original"},
    }
    revision = {
        **first,
        "id": "two",
        "filed_at": "2022-05-10T16:00:00+05:30",
        "metadata": {"type_Sub": "Revision"},
        "source_url": "https://archives.nseindia.com/g.xml",
    }
    monkeypatch.setattr(ledger, "ROOT", tmp_path)
    from thesistally import fundamental_notice_proof

    monkeypatch.setattr(fundamental_notice_proof, "ROOT", tmp_path)
    monkeypatch.setattr(ledger, "index_notices", lambda: {"STOCK": []})
    monkeypatch.setattr(
        ledger,
        "parse_document",
        lambda row, receipts: (
            {"eps": 10, "revenue": 100, "operating_profit": 50, "net_profit": 30},
            {},
            "e" * 64,
        ),
    )
    for name, data in [
        ("attachment-plan.json", [first, revision]),
        ("notice-census.json", []),
        ("notice-manifest.json", [{"symbol": "STOCK", "error": "403"}]),
    ]:
        (tmp_path / name).write_text(json.dumps(data))
    ledger.publish()
    raw = (tmp_path / "filings.jsonl").read_bytes()
    a, b = map(json.loads, raw.splitlines())
    assert not a["first_reported"] and a["gap"] == "unknown_issuer_announcement_archive"
    assert not b["first_reported"] and b["gap"] == "exchange_labels_revision"
    assert (
        gzip.decompress((tmp_path / "vintages" / (digest(raw) + ".jsonl.gz")).read_bytes()) == raw
    )
    (tmp_path / "notice-manifest.json").write_text(json.dumps([{"symbol": "STOCK", "total": 17}]))
    ledger.publish()
    assert json.loads((tmp_path / "filings.jsonl").read_bytes().splitlines()[0])["first_reported"]
    assert (
        gzip.decompress((tmp_path / "vintages" / (digest(raw) + ".jsonl.gz")).read_bytes()) == raw
    )


def test_split_isin_lookup_requires_evidence_in_original():
    from thesistally.fundamental_nse import dated_isin

    intervals = [
        {"isin": "INE476A01014", "from_date": "2014-01-01", "to_date": "2024-05-14"},
        {"isin": "INE476A01022", "from_date": "2024-05-15", "to_date": "2026-11-20"},
    ]
    assert dated_isin(intervals, "2024-06-30", "INE476A01014") == (
        "INE476A01022",
        "require_original_isin",
    )
    assert dated_isin(intervals, "2024-06-30", "INE476A01022") == (
        "INE476A01022",
        "exchange_and_membership",
    )
    assert dated_isin(intervals, "2024-06-30", "INE111A01012")[0] is None


def test_old_banking_taxonomy_requires_agreement_on_quarter_dates():
    raw = gzip.decompress((EXAMPLES / "albk-mar2018.xml.gz").read_bytes())
    row = {
        "symbol": "ALBK",
        "isin": "INE428A01015",
        "period_end": "2018-03-31",
        "basis": "standalone",
        "metadata": {
            "fromDate": "01-Jan-2018",
            "period": "Quarterly",
            "cumulative": "Non-cumulative",
        },
    }
    values, tags = xml_facts(raw, row)
    assert values == {
        "revenue": 42593700000,
        "operating_profit": 1229000000,
        "net_profit": -35096300000,
        "eps": -43.1,
    }
    assert tags["quarter_context"].startswith("NSE quarterly")
    row["metadata"]["fromDate"] = "01-Apr-2017"
    with pytest.raises(ValueError, match="contexts disagree"):
        xml_facts(raw, row)


@pytest.mark.parametrize(
    ("filename", "isin", "symbol", "expected"),
    [
        (
            "360one-mar2025.xml.gz",
            "INE466L01038",
            "360ONE",
            {
                "revenue": 8207700000,
                "operating_profit": 4415500000,
                "net_profit": 2496000000,
                "eps": 6.38,
            },
        ),
        (
            "hdfclife-mar2025.xml.gz",
            "INE795G01014",
            "HDFCLIFE",
            {
                "revenue": 238429900000,
                "operating_profit": None,
                "net_profit": 4753600000,
                "eps": 2.21,
            },
        ),
    ],
)
def test_financial_company_taxonomies(filename, isin, symbol, expected):
    fields, _ = xml_facts(
        gzip.decompress((EXAMPLES / filename).read_bytes()),
        {"isin": isin, "symbol": symbol, "period_end": "2025-03-31", "basis": "consolidated"},
    )
    assert fields == expected


def test_intimating_a_future_meeting_is_separate_from_publishing_results():
    from thesistally.fundamental_notice_proof import prospective_notice

    assert prospective_notice(
        "Board Meeting Postponed", "The meeting to consider the results has been postponed."
    )
    assert prospective_notice(
        "Updates", "A meeting is scheduled to consider the audited financial results."
    )
    assert prospective_notice(
        "Results / Dividend",
        "01-May-2015: To consider the Audited Financial Results for the quarter.",
    )
    assert not prospective_notice(
        "Board Meeting Intimation",
        "Please find enclosed the provisional results. The next board meeting will be held on Monday.",
    )
    assert not prospective_notice(
        "Outcome of board meeting", "It has approved the financial results for the quarter today."
    )
    assert not prospective_notice(
        "Financial Result Updates",
        "The Bank is announcing its Q3 FY26 Financial Results (Provisional).",
    )


def test_recover_only_original_legal_name_under_verified_unique_dated_security():
    from thesistally.fundamental_identity import identity_witness

    symbol, isin = "KOTAKBANK", "INE237A01028"
    row = {
        "symbol": symbol,
        "isin": isin,
        "identity": "require_original_isin",
        "period_end": "2024-09-30",
        "metadata": {"isin": "INE237A01010"},
    }
    notice = {"symbol": symbol, "isin": "INE237A01010", "announced_at": "2024-10-18T13:00:00+05:30"}
    dates = ["2024-09-30", "2024-10-17", "2024-10-18"]
    receipts = {
        d: {
            "equities": {symbol: isin},
            "url": "https://archives.nseindia.com/foo",
            "sha256": "a" * 64,
        }
        for d in dates
    }
    intervals = {(symbol, isin): [("2020-01-01", "2025-01-01")]}
    proof = identity_witness(row, notice, receipts, dates, intervals)
    assert [e["day"] for e in proof["evidence"]] == dates[:2]
    assert not identity_witness(row, {**notice, "isin": "INE000A01010"}, receipts, dates, intervals)
    assert not identity_witness(row, notice, receipts, dates, {})
    del receipts["2024-10-17"]
    assert not identity_witness(row, notice, receipts, dates, intervals)


def test_renamed_stock_structured_alias_requires_same_dated_isin_and_verified_ticker(monkeypatch):
    from thesistally import fundamental_facts as facts

    row = {
        "symbol": "ZYDUSLIFE",
        "isin": "INE010B01027",
        "period_end": "2018-12-31",
        "metadata": {"isin": "INE010B01027"},
    }
    monkeypatch.setattr(
        facts,
        "symbol_intervals",
        lambda: {
            "INE010B01027": [
                ("2015-01-01", "2022-03-04", "CADILAHC", "https://archives.nseindia.com/day")
            ]
        },
    )
    assert facts.confirmed_symbol(row, "CADILAHC")["dated_isin"] == row["isin"]
    assert not facts.confirmed_symbol(row, "XYZ")
    assert not facts.confirmed_symbol({**row, "period_end": "2024-12-31"}, "CADILAHC")
    assert not facts.confirmed_symbol({**row, "metadata": {"isin": "INE010B01010"}}, "CADILAHC")


def test_issuer_archive_repair_keeps_accession_quarter_and_basis():
    from thesistally.fundamental_alias_paths import candidate_urls, header_matches

    row = {
        "symbol": "ABREL",
        "metadata": {
            "seqNumber": "1000322",
            "period": "Quarterly",
            "toDate": "30-Jun-2015",
            "cumulative": "Non-Cumulative",
        },
        "source_url": "https://nsearchives.nseindia.in/archives/financial_results/financial_res_ABREL_1000322.html",
        "basis": "standalone",
    }
    older_url = candidate_urls(row, {"CENTURYTEX"})[0][1]
    assert older_url.endswith("/financial_res_CENTURYTEX_1000322.html")
    cells = [
        "Symbol",
        "CENTURYTEX",
        "Cumulative / Non-Cumulative",
        "Non-Cumulative",
        "Consolidated / Non-Consolidated",
        "Non-Consolidated",
        "Period Ended",
        "30-Jun-2015",
        "Period",
        "Quarterly",
    ]
    raw = ("<table>" + "".join("<td>" + k + "</td>" for k in cells) + "</table>").encode()
    assert header_matches(raw, row, "CENTURYTEX")
    assert not header_matches(raw, {**row, "basis": "consolidated"}, "CENTURYTEX")
    assert not header_matches(raw, row, "ABREL")
    assert not candidate_urls(
        {**row, "source_url": row["source_url"].replace("1000322", "2011225")}, {"CENTURYTEX"}
    )


def test_public_archive_preserves_individual_official_mirror_failures():
    import httpx
    import pytest

    from thesistally.fundamental_nse import digest, public_copy
    from thesistally.fundamental_retry import transient

    def fake(request):
        if request.url.host == "archives.nseindia.com":
            raise httpx.ConnectError("name resolution failed", request=request)
        if request.url.host == "nsearchives.nseindia.com":
            return httpx.Response(200, content=b"")
        return httpx.Response(404)

    with (
        httpx.Client(transport=httpx.MockTransport(fake)) as client,
        pytest.raises(ValueError) as caught,
    ):
        public_copy(client, "https://nsearchives.nseindia.in/corporate/old.pdf", accept=bool)
    text = str(caught.value)
    assert "archives.nseindia.com=connection ConnectError" in text
    assert f"nsearchives.nseindia.com=invalid body (0 bytes, sha256 {digest(b'')})" in text
    assert "nsearchives.nseindia.in=HTTP 404" in text
    assert transient(text)
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b""))
        ) as client,
        pytest.raises(ValueError) as caught,
    ):
        public_copy(client, "https://nsearchives.nseindia.in/corporate/old.pdf", accept=bool)
    assert str(caught.value).count("invalid body") == 3
    assert not transient(str(caught.value))


def test_later_archive_download_cannot_enter_a_frozen_source_edition(tmp_path, monkeypatch):
    from thesistally import fundamental_nse_ledger as ledger
    from thesistally.fundamental_nse import digest, write_json

    monkeypatch.setattr(ledger, "ROOT", tmp_path)
    url = "https://archives.nseindia.com/late.xml"
    write_json(
        tmp_path / "receipts" / (digest(url.encode()) + ".json"), {"url": url, "sha256": "f" * 64}
    )
    with pytest.raises(ValueError, match="structured_attachment_unavailable"):
        ledger.parse_document({"source_url": url}, {})


def test_recording_an_issuer_vintage_cannot_hide_the_next_issuers_frozen_bytes(
    tmp_path, monkeypatch
):
    import json

    from thesistally import fundamental_notice_proof as notices
    from thesistally import fundamental_nse_ledger as ledger
    from thesistally.fundamental_nse import digest, pack, write_json

    xml = gzip.decompress((EXAMPLES / "tcs-sep2023.xml.gz").read_bytes())
    first = {
        "id": "tcs",
        "symbol": "TCS",
        "isin": "INE467B01029",
        "period_end": "2023-09-30",
        "basis": "consolidated",
        "filed_at": "2023-10-12T12:30:00+05:30",
        "source_url": "https://archives.nseindia.com/tcs.xml",
        "catalogue_url": "https://www.nseindia.in/api/integrated-filing-results",
        "metadata": {"type_Sub": "Original"},
    }
    second = {
        **first,
        "id": "sbi",
        "symbol": "SBIN",
        "isin": "INE062A01020",
        "period_end": "2026-09-30",
        "filed_at": "2026-11-01T14:00:00+05:30",
        "source_url": "https://archives.nseindia.com/sbi.xml",
    }
    second_xml = gzip.decompress((EXAMPLES / "sbin-sep2026.xml.gz").read_bytes())
    for row, data in ((first, xml), (second, second_xml)):
        h = digest(data)
        u = row["source_url"]
        write_json(tmp_path / "receipts" / (digest(u.encode()) + ".json"), {"url": u, "sha256": h})
        pack(tmp_path / "objects" / h[:2] / (h + ".gz"), data)
    for filename, obj in (
        ("attachment-plan", [first, second]),
        ("notice-census", []),
        ("notice-manifest", [{"symbol": "TCS", "total": 3}, {"symbol": "SBIN", "total": 3}]),
    ):
        write_json(tmp_path / (filename + ".json"), obj)
    monkeypatch.setattr(ledger, "ROOT", tmp_path)
    monkeypatch.setattr(notices, "ROOT", tmp_path)
    ledger.publish()
    rows = [json.loads(line) for line in (tmp_path / "filings.jsonl").read_bytes().splitlines()]
    assert {(r["symbol"], r["eps"], r["first_reported"]) for r in rows} == {
        ("TCS", 31, True),
        ("SBIN", 27.47, True),
    }
    assert all(r["structured_document_sha256"] == r["structured_sha256"] for r in rows)
