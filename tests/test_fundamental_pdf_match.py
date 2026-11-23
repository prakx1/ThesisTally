from thesistally.fundamental_pdf_match import match

# Dates, statement layout and selected figures from page 7 of TCS's 10-Oct-2024
# original NSE results. The second column is deliberately different in each test.
TABLE = """Consolidated statement of results
(INR Crores)
                    Three months ended            Six months ended
                   September 30,    June 30,    September 30,
                       2024            2024          2024
Revenue from operati ons          64,259      62,613      1,26,872
Finance costs                        162         173          335
Total expenses                    48,956      47,344       96,300
Profit for the period             11,955      12,105       24,060
Basic and diluted EPS              32.92       33.28        66.20
"""
ROW = {
    "basis": "consolidated",
    "period_end": "2024-09-30",
    "revenue": 64259e7,
    "operating_profit": 15465e7,
    "net_profit": 11955e7,
    "eps": 32.92,
}


def result(row, page):
    return match(row, [{"pdf_sha256": "original", "pages": ["cover", page]}])


def test_explicit_facts_on_first_quarter_column_reconcile_operating_profit():
    x = result(
        ROW, TABLE.replace("Basic and diluted EPS", "Earnings per share – Basic and diluted")
    )
    assert x["page"] == 2 and x["operating_profit_in_printed_units"] == "15465"
    assert x["cells"]["eps"][0]["value"] == "32.92"


def test_reject_a_later_column_different_basis_missing_operating_inputs_or_swapped_periods():
    table = TABLE.replace("Basic and diluted EPS", "Earnings per share – Basic and diluted")
    for bad in [
        table.replace("September 30,    June 30,", "June 30,      September 30,"),
        table.replace("Consolidated", "Standalone"),
        table.replace("Finance costs", "Depreciation"),
        table.replace("INR Crores", "INR Lakhs"),
        table.replace("32.92       33.28", "33.28       32.92"),
        table.replace("Three months ended", "Six months ended"),
        table.replace("64,259      62,613", "-           64,259"),
    ]:
        assert result(ROW, bad) is None


def test_bank_reconciles_total_income_and_preprovision_profit_as_reported():
    # Kotak Mahindra Bank's actual 19 October 2024 NSE results, standalone page 4.
    table = """Standalone results (₹ Crore)
                    Quarter ended            Half year ended
     Particulars    30-Sep-24    30-Jun-24   30-Sep-24
1       Interest earned              13,216.27       12,746.11
3       Total income (1+2)           15,900.46       15,675.15
7       Operating profit (3-6)
        (Profit before provisions and       5,099.25        5,254.11
        contingencies)
14      Net Profit 12-13)             3,343.72        6,249.82
        - Basic (not annualised)         16.82           31.44
        - Diluted (not annualised)       16.82           31.44
"""
    row = {
        "basis": "standalone",
        "period_end": "2024-09-30",
        "tags": {"revenue": "TotalIncome"},
        "revenue": 15900.46e7,
        "operating_profit": 5099.25e7,
        "net_profit": 3343.72e7,
        "eps": 16.82,
    }
    receipt = result(row, table)
    assert receipt["operating_profit_method"] == "printed"
    assert receipt["cells"]["bank_income"][0]["value"] == "15900.46"
    assert result(dict(row, revenue=13216.27e7), table) is None
    assert result(dict(row, tags={}), table) is None


def test_first_issue_fields_require_issuer_unit_and_numeric_column():
    from thesistally.fundamental_pdf_match import extract_original

    def extract(text):
        row = {
            "period_end": "2024-09-30",
            "basis": "consolidated",
            "metadata": {"companyName": "Example Limited"},
        }
        return extract_original(row, [{"pdf_sha256": "original", "pages": [text]}])

    table = """EXAMPLE LIMITED
Consolidated Statement of Financial Results (INR crore)
                            Quarter ended                    Half year ended
Particulars                30-Sep-2024      30-Jun-2024       30-Sep-2024
Revenue from operations         123.45           90.00          220.00
Profit for the period                            55.00           60.00
Basic EPS                         4.50            3.45            7.95
"""
    result = extract(table)
    assert result["values"] == {
        "revenue": 1234500000.0,
        "eps": 4.5,
        "net_profit": None,
        "operating_profit": None,
    }
    assert extract(table.replace("EXAMPLE LIMITED", "SUBSIDIARY LIMITED")) is None
    assert (
        extract(table.replace("30-Sep-2024      30-Jun-2024", "30-Jun-2024      30-Sep-2024"))
        is None
    )
    assert extract(table.replace("INR crore", "USD million")) is None
    assert extract(table.replace("INR crore", "crore")) is None


def test_ocr_only_corroborates_four_structured_fields(tmp_path):
    import gzip

    from thesistally.discovery_registry import encoded
    from thesistally.fundamental_ocr import VERSION, capsule, corroborate, layout

    assert "120" in layout([{"x": 0.5, "y": 0.4, "height": 0.01, "text": "120"}])
    path = capsule(tmp_path, "abcd")
    path.parent.mkdir(parents=True)
    printed = TABLE.replace("Basic and diluted EPS", "Earnings per share – Basic and diluted")
    path.write_bytes(
        gzip.compress(encoded({"pdf_sha256": "abcd", "method": VERSION, "pages": {"4": printed}}))
    )
    source = {"source_url": "notice", "pdfs": [{"pdf_sha256": "abcd"}]}
    assert corroborate(tmp_path, ROW, source, "notice")["page"] == 4
    assert corroborate(tmp_path, ROW, source, "notice", available=set()) is None
    assert corroborate(tmp_path, ROW, source, "notice", available={"abcd"})["page"] == 4
    assert corroborate(tmp_path, {**ROW, "eps": None}, source, "notice") is None
    assert corroborate(tmp_path, {**ROW, "eps": 999}, source, "notice") is None


def test_stacked_current_year_is_selected_by_horizontal_alignment():
    from datetime import date

    from thesistally.fundamental_pdf_match import _column_header

    header = [
        "                     Quarter ended   Preceding       Corresponding",
        "                     September 30, quarter ended     quarter ended",
        "Particulars              2025      June 30, 2025     September 30,",
    ]
    period, (left, right) = _column_header(header)
    assert period == date(2025, 9, 30) and left < 26 < right < 43
    header[-1] = "Particulars                        June 30, 2025     September 30,"
    assert _column_header(header) is None


def test_direct_original_cannot_use_eps_or_profit_from_a_single_business_scope():
    from thesistally.fundamental_pdf_match import extract_original

    table = """EXAMPLE LIMITED
Consolidated Statement of Financial Results (INR crore)
                            Quarter ended                    Half year ended
Particulars                30-Sep-2024      30-Jun-2024       30-Sep-2024
Revenue from operations         123.45           90.00          220.00
Profit after tax from continuing operations             55.00           60.00
EPS from continuing operations
Basic                             4.50            3.45            7.95
"""
    row = {
        "basis": "consolidated",
        "period_end": "2024-09-30",
        "metadata": {"companyName": "Example Limited"},
    }
    proof = extract_original(row, [{"pdf_sha256": "original", "pages": [table]}])
    assert proof["values"]["eps"] is None
    assert proof["values"]["net_profit"] is None
    assert proof["values"]["revenue"] == 1234500000


def test_roman_statement_row_number_does_not_become_part_of_the_fact_label():
    labelled = TABLE.replace("Revenue from operati ons", "IV   Revenue from operati ons")
    labelled = labelled.replace(
        "Basic and diluted EPS", "VII   Earnings per share – Basic and diluted"
    )
    assert result(ROW, labelled)["values"]["eps"] == ROW["eps"]


def test_foreign_currency_invalidates_numeric_agreement_and_inferred_units_are_disclosed():
    table = TABLE.replace("Basic and diluted EPS", "Earnings per share – Basic and diluted")
    for currency in ("GBP", "JPY", "USD", "AED", "EUR", "$", "£", "¥"):
        assert result(ROW, table.replace("INR Crores", currency + " Crores")) is None
    assert result(ROW, table)["currency_evidence"] == "printed_rupee_legend"
    assert (
        result(ROW, table.replace("INR Crores", "Crores"))["currency_evidence"]
        == "printed_scale_and_structured_inr_matches"
    )


def test_split_audited_heading_does_not_mean_australian_dollars():
    table = TABLE.replace("Basic and diluted EPS", "Earnings per share – Basic and diluted")
    assert result(ROW, "Aud ited*\n" + table) is not None
    assert result(ROW, table.replace("INR Crores", "AUD Crores")) is None


def test_company_logo_symbol_does_not_override_original_currency_legend():
    table = TABLE.replace("Basic and diluted EPS", "Earnings per share – Basic and diluted")
    assert (
        result(ROW, "$         Company logo\n" + table)["currency_evidence"]
        == "printed_rupee_legend"
    )
    assert result(ROW, "Scanned letterhead~' $\n" + table) is not None
    assert result(ROW, table.replace("INR Crores", "INR Crores / $ in millions")) is None
    assert result(ROW, table.replace("INR Crores", "millions of $")) is None
