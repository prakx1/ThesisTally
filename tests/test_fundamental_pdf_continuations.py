"""Regression for ABB's March 2026 two-page current quarter statement."""

from thesistally.fundamental_pdf_continuations import corroborate_continuations


def row(label, value, comparison):
    return f"{label:<49}{value:>9}        {comparison:>9}"


HEADER = "\n".join(
    (
        " " * 49 + "Quarter          Preceding",
        " " * 49 + " ended        quarter ended",
        " " * 49 + "March 31,     December 31,",
        " " * 49 + "  2026            2025",
        " " * 49 + "Unaudited      Refer note 5",
    )
)
PRIMARY = "\n".join(
    (
        "ABB India Limited",
        "Statement of consolidated financial results for the quarter ended March 31, 2026",
        "Part I                                          In Crores",
        HEADER,
        row("Total revenue from operations", "3,184.06", "3,423.16"),
        row("Finance costs", "3.81", "5.28"),
        row("Total expenses", "2,821.83", "2,940.51"),
        row("Profit for the period/year (7 + 9)", "1,783.65", "432.85"),
    )
)
CONT = "\n".join(
    (
        "Part I (contd...)",
        HEADER,
        "15 Earnings / (loss) per share ('EPS')",
        "Continuing operations",
        row("a) Basic (in T)", "16.14", "19.84"),
        "Discontinued operation",
        row("a) Basic (in ?)", "68.04", "0.59"),
        "Continuing and Discontinued operations",
        row("a) Basic (in ?)", "84.18", "20.43"),
        "See accompanying notes to the consolidated financial results",
        "Part II",
        row("a) Basic (in ?)", "6.0", "5.0"),
    )
)
FILING = {
    "metadata": {"companyName": "ABB India Limited"},
    "period_end": "2026-03-31",
    "basis": "consolidated",
    "revenue": 31_840_600_000,
    "operating_profit": 3_660_400_000,
    "net_profit": 17_836_500_000,
    "eps": 84.18,
}


def extract(primary=PRIMARY, continuation=CONT, **changes):
    return corroborate_continuations(
        {**FILING, **changes}, [{"pdf_sha256": "test-digest", "pages": [primary, continuation]}]
    )


def test_split_statement_uses_combined_eps_and_records_original_pages():
    proof = extract()
    assert proof["pages_used"] == [1, 2]
    assert proof["cells"]["eps"] == [
        {"page": 2, "row": 13, "label": "a) Basic (in ?)", "value": "84.18"}
    ]
    assert proof["values"] == {
        key: FILING[key] for key in ("eps", "net_profit", "operating_profit", "revenue")
    }
    assert extract(eps=16.14) is None


def test_unrelated_pages_or_unidentified_entity_cannot_contribute_values():
    assert extract(continuation=CONT.replace("Part I (contd...)", "")) is None
    assert extract(continuation=CONT.replace("consolidated", "standalone")) is None
    assert extract(continuation=CONT.replace("March 31,", "March 30,")) is None
    assert extract(continuation=CONT.replace("  2026", "  2025")) is None
    assert (
        extract(
            continuation=CONT.replace(
                "Part I (contd...)", "Part I (contd...)\nOther Issuer Limited"
            )
        )
        is None
    )
    assert extract(primary=PRIMARY.replace("ABB India Limited", "Different Issuer Limited")) is None
    assert extract(primary=PRIMARY.replace("In Crores", "USD In Crores")) is None


def test_comparison_column_or_later_segment_cannot_supply_missing_facts():
    assert extract(continuation=CONT.replace("84.18", "     ")) is None
    assert (
        extract(
            continuation=CONT.replace(
                "Continuing and Discontinued operations", "Continuing operations"
            )
        )
        is None
    )
    assert extract(continuation=CONT.replace("March 31,", "June 30,") + "\n" + HEADER) is None
    assert extract(operating_profit=1) is None
