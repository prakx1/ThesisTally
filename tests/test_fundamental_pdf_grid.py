from thesistally.fundamental_pdf_grid import corroborate_grid


def table():
    # KPIT's original 31 Dec 2023 consolidated statement put the year below
    # each month, and centered the quarter heading over several numeric columns.
    rows = [
        "KPIT Technologies Limited",
        "Statement of unaudited consolidated financial results",
        "INR million (except per share)",
        "Particulars                                    Quarter ended",
    ]
    for label, a, b in [
        ("", "31 December", "30 September"),
        ("", "2023", "2023"),
        ("Revenue from operations", "12,569.62", "11,991.57"),
        ("Finance costs", "157.51", "135.70"),
        ("Total expenses", "10,641.66", "10,208.86"),
        ("Profit for the period/year", "1,567.54", "1,414.01"),
        ("Basic EPS", "5.73", "5.20"),
    ]:
        rows.append(f"{label:34}{a:>15}{b:>20}")
    return "\n".join(rows)


def corroborate(page, **overrides):
    filing = {
        "metadata": {"companyName": "KPIT Technologies Limited"},
        "basis": "consolidated",
        "period_end": "2023-12-31",
        "revenue": 12_569_620_000,
        "operating_profit": 2_085_470_000,
        "net_profit": 1_567_540_000,
        "eps": 5.73,
        **overrides,
    }
    return corroborate_grid(filing, [{"pdf_sha256": "original", "pages": [page]}])


def test_split_dates_and_first_columns_bind_to_four_corroborated_values():
    result = corroborate(table())
    assert result["page"] == 1
    assert result["values"]["eps"] == 5.73
    assert result["comparison_column_date"] == "2023-09-30"


def test_comparison_columns_different_basis_and_bad_issuer_cannot_supply_firsts():
    page = table()
    assert corroborate(page.replace("31 December", "30 September", 1)) is None
    assert corroborate(page.replace("30 September", "31 December")) is None
    assert corroborate(page.replace("Quarter ended", "Nine months ended")) is None
    assert corroborate(page.replace("consolidated", "standalone")) is None
    assert corroborate(page.replace("KPIT Technologies Limited", "Another Company Limited")) is None
    assert corroborate(page.replace("5.73", "    "), eps=5.20) is None
    assert corroborate(page, net_profit=0) is None
