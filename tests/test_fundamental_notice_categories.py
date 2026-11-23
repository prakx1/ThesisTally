from datetime import date

from thesistally.fundamental_notice_categories import file_info, project

DAY = date(2026, 7, 24)
URL = "https://nsearchives.nseindia.in/corporate/"
ISIN = "INE111A01025"
RECEIPT = {
    "url": "https://www.nseindia.in/api/corporate-announcements?index=equities",
    "sha256": "f" * 64,
}


def row(subject, body, filename, sec, isin=ISIN, ticker="CONCOR"):
    return {
        "symbol": ticker,
        "sm_isin": isin,
        "desc": subject,
        "attchmntText": body,
        "attchmntFile": URL + filename,
        "exchdisstime": f"24-Jul-2026 15:29:{sec:02d}",
        "seq_id": str(sec),
    }


def run(rows, formal=None):
    return project(rows, RECEIPT, {"CONCOR"}, {ISIN}, formal or {})[0]


def test_other_subject_earlier_time_and_different_byte_uploads_are_only_possible():
    result = row(
        "Financial Results Updates",
        "Financial results for the quarter",
        "issuer_24072026170031_SE_FY_Q1_JUL.pdf",
        31,
    )
    first = row(
        "Record Date",
        "Issuer announced a record date for dividend.",
        "issuer_24072026170015_SE_FY_Q1_JUL.pdf",
        15,
    )
    other = row(
        "General Update", "Separate issuer", "issuer_24072026170005_SE_FY_Q1_JUL.pdf", 5, "OTHER"
    )
    found = run([result, first, other])
    assert len(found) == 1
    assert found[0]["announced_at"] == "2026-07-24T15:29:15+05:30"
    assert found[0]["source_url"] != found[0]["same_day_source_links"][0]["source_url"]
    assert found[0]["selection_reasons"] == ["same_issuer_day_and_document_basename"]
    assert found[0]["release_language"] == "possible_original_financial_packet"
    assert "attachment_sha256" not in found[0]


def test_other_subject_results_words_and_anchored_board_outcome_filename():
    grouped = [
        row(
            "Dividend",
            "The Board declared a dividend.",
            "CONCOR_24072026174235_CONCOR_SE_BOD_Outcome_240726.pdf",
            20,
        ),
        row(
            "Record Date",
            "Dividend record date.",
            "CONCOR_24072026174744_CONCOR_SE_BOD_Outcome_240726.pdf",
            21,
        ),
        row(
            "Updates",
            "Submission of Audited Financial Statement for the financial year ended March 31, 2026.",
            "VAML_24072026144757_SEIntimationFinancialresultssigned.pdf",
            12,
        ),
        row(
            "Press Release",
            "Company releases Q1 FY26 earnings.",
            "issuer_Q1EarningsRelease.pdf",
            15,
        ),
        row(
            "General Updates",
            "Intimation regarding earnings analyst call",
            "issuer_FR_Q1EarningsRelease.pdf",
            16,
        ),
        row(
            "General Updates",
            "Company's quarterly operations",
            "issuer_24072026174235_PressRelease.pdf",
            17,
        ),
        row(
            "General Updates",
            "Issuer reports business operations",
            "issuer_24072026145931_Outcome.pdf",
            18,
        ),
        row(
            "Limited Review Report",
            "The auditors have conducted the limited review for the unaudited financial results for the quarter.",
            "issuer_LR.pdf",
            19,
        ),
    ]
    without = run(grouped)
    assert {r["sequence"] for r in without} == {"12", "15"}
    anchored = run(grouped, {("CONCOR", ISIN, DAY): {"2026-06-30"}})
    assert {r["sequence"] for r in anchored} == {"12", "15", "18", "20", "21"}
    assert all(r["same_day_structured_filing_periods"] == ["2026-06-30"] for r in anchored)
    assert all("declared_period_end" not in r for r in anchored)


def test_only_recognized_official_upload_clock_prefix_is_removed():
    assert file_info(URL + "ISSUER_24072026045935_Quarter.PDF", DAY)[0] == "quarter.pdf"
    assert file_info(URL + "ISSUER_25072026045935_Quarter.PDF", DAY)[0].startswith("issuer_")
    assert file_info(URL + "ISSUER_24072026995935_Quarter.PDF", DAY)[0].startswith("issuer_")
    assert file_info("https://other.test/company/Quarter.PDF", DAY) is None


def test_dated_board_packet_also_applies_when_the_formal_table_followed_next_morning():
    known = row(
        "Dividend",
        "Acme Limited has informed the Exchange that Board of Directors at its meeting held on July 24, 2026, declared dividend.",
        "issuer_24072026145700_Outcome24072026.pdf",
        15,
    )
    other = {**known, "attchmntFile": URL + "issuer_24072026145700_Outcome24072025.pdf"}
    assert run([known])[0]["selection_reasons"] == ["dated_board_outcome_at_reported_meeting"]
    assert run([other]) == []
    unrelated = row(
        "Press Release",
        'Acme Limited has informed the Exchange of a press release titled "Otherco Reports Q3 Results".',
        "issuer_24072026145811_OthercoQ3FY26Results.pdf",
        17,
    )
    unrelated["sm_name"] = "Acme Limited"
    assert not run([unrelated])


def test_publish_results_with_actual_attachment_is_distinct_from_future_intimation():
    name = "HAL_24072026145919_AuditedFinancialResults30062026.pdf"
    actual = row(
        "Publish Audited Results",
        "Issuer has informed the Exchange about Publish Audited Results",
        name,
        16,
    )
    future = row(
        "Board Meeting Intimation",
        "The board meeting is scheduled for August 3 to consider audited results.",
        name,
        19,
    )
    assert {v["sequence"] for v in run([actual, future])} == {"16"}
