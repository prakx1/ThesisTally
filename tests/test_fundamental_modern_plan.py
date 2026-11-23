import gzip
import io
import zipfile

from thesistally.discovery_registry import digest, encoded
from thesistally.fundamental_modern_plan import case, nomination, pages_for, source_members
from thesistally.fundamental_ocr import VERSION


def test_selects_primary_statements_even_with_spaced_letters_and_retains_continuation_scope():
    table = """S T A T E M E N T  of F I N A N C I A L R E S U L T S
        Revenue from operations   172.25  164.10  163.88
        Tax expense   12.93  13.00  15.37
        Profit after tax   24.88  22.77  20.38"""
    assert nomination("", table) == (True, True)
    note = "Quarter ended 31 March 2024. Auditors reviewed the financial results. Date 31/03/2024."
    assert not nomination(note, "")[0]
    rest = (
        "Finance costs 2.01 3.14 7.19 6.28 1.47\n"
        "Total revenue 18.31 19.10 21.27 22.37 14.24\nProfit after tax 7.31 6.19 8.91 5.22\n"
    )
    assert nomination(rest, "", True) == (True, False)
    assert nomination(rest, "", False) == (False, False)


def test_eligible_original_release_group_is_not_selected_on_prior_performance():
    f = {
        "id": "I1",
        "source_url": "https://nse.test/f.xml",
        "isin": "I",
        "basis": "consolidated",
        "period_end": "2023-12-31",
        "filed_at": "2024-02-01T18:44:00+05:30",
        "symbol": "A",
    }
    r = {
        **f,
        "filing_status": "uncertified",
        "structured_url": f["source_url"],
        "structured_sha256": "abc",
        "structured_values": {
            "revenue": 17,
            "operating_profit": None,
            "eps": None,
            "net_profit": None,
        },
        "structured_tags": {"revenue": "RevenueFromOperations"},
        "first_notice": {
            "source_url": "https://nse.test/original",
            "announced_at": "2024-01-31T15:02:00+05:30",
        },
    }
    c, why = case(r, {"I1": f})
    assert (
        not why
        and c["release_at"] == r["first_notice"]["announced_at"]
        and c["filing"]["revenue"] == 17
    )
    assert case({**r, "filing_status": "revision"}, {"I1": f})[0] is None
    assert (
        case({**r, "first_notice": {**r["first_notice"], "announced_at": "2024-01-31"}}, {"I1": f})[
            0
        ]
        is None
    )
    assert (
        case(
            {**r, "first_notice": {**r["first_notice"], "announced_at": f["filed_at"]}}, {"I1": f}
        )[0]
        is None
    )
    assert (
        case(
            {
                **r,
                "first_notice": {**r["first_notice"], "description": "revised financial results"},
            },
            {"I1": f},
        )[0]
        is None
    )
    assert (
        case(
            {
                **r,
                "filing_status": "original",
                "revenue": 3,
                "net_profit": 4,
                "operating_profit": 5,
                "eps": 0,
            },
            {"I1": f},
        )[0]
        is None
    )


def test_page_census_uses_matching_frozen_ocr_and_records_unreadable_pages(tmp_path):
    native = "Quarterly financial results. Revenue 285.10 280.31 270.28\nExpenses 19.08 18.17 15.14"
    leaf = digest(b"PDF")
    wk = "known.json.gz"
    capsule = {
        "pdf_sha256": leaf,
        "method": VERSION,
        "engine_sha256": "engine",
        "pages": {"1": native, "2": ""},
    }
    (tmp_path / wk).write_bytes(gzip.compress(encoded(capsule)))
    pkg = {"pdfs": [{"pdf_sha256": leaf, "pages": ["", "", native, ""]}]}
    pages, m, gaps, pins = pages_for(tmp_path, pkg, {leaf: wk}, "engine")
    assert pages == {leaf: [1, 3]}
    assert m[0]["physical_pages"] == 3
    assert m[0]["sparse_pages_without_readable_text"] == [2]
    assert pins[leaf] == digest((tmp_path / wk).read_bytes())
    assert gaps[0]["pages"] == [2]
    assert "zero_decoded_members" in {
        r["cause"] for r in pages_for(tmp_path, {"pdfs": []}, {}, "engine")[2]
    }


def test_archive_inventory_reports_a_non_pdf_without_guessing_its_page_count():
    pdf = b"%PDF-1.4 example"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("results.pdf", pdf)
        z.writestr("other.bin", b"unknown")
        z.writestr("Thumbs.db", b"system")
    leaves, gaps = source_members(buf.getvalue(), [{"source_member_sha256": digest(pdf)}])
    assert len(leaves) == 3
    assert [v["source_member"] for v in gaps] == ["other.bin"]
