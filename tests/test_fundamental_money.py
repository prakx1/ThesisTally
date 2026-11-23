from decimal import Decimal

from thesistally.fundamental_money_witness import deciding_witness
from thesistally.fundamental_rendered_money import monetary_scalar, observe


def box(text, x, y):
    return {"text": text, "x": x, "y": y, "width": 0.04, "height": 0.008}


def statement():
    boxes = [
        box("SRF Limited", 0.4, 0.95),
        box("Consolidated financial results", 0.16, 0.89),
        box("(Rs. in Crores, except per share data)", 0.7, 0.86),
        box("Quarter ended", 0.59, 0.84),
        box("Year ended", 0.8, 0.84),
        box("31-Mar-23", 0.51, 0.83),
        box("31-Dec-22", 0.59, 0.83),
    ]
    for y, label, a, b in [
        (0.78, "Revenue from operations", None, None),
        (0.77, "Product revenues", "3,755.00", "3,460.00"),
        (0.75, "Total Revenue from operations", "3,778.09", "3,469.66"),
        (0.66, "e) Finance costs", "10.00", "13.00"),
        (0.59, "Total expenses", "3,011.13", "2,800.00"),
        (0.52, "Net Profit after tax for the period", "562.45", "510.00"),
        (0.49, "Profit attributable to non-controlling interests", "200.00", "210.00"),
        (0.47, "Net Profit after tax for the period", "362.45", "300.00"),
    ]:
        boxes.append(box(label, 0.08, y))
        if a is not None:
            boxes.extend([box(a, 0.52, y), box(b, 0.60, y)])
    return boxes


def test_early_quarter_uses_total_statutory_rows_before_profit_attribution():
    row = {
        "basis": "consolidated",
        "period_end": "2023-03-31",
        "metadata": {"companyName": "SRF Limited"},
    }
    page = statement()
    expected = {
        "revenue": "37780900000.00",
        "net_profit": "5624500000.00",
        "operating_profit": "7769600000.00",
    }
    for field, value in expected.items():
        receipt = observe(row, page, field)
        assert receipt["value_inr"] == value and receipt["currency_explicit"]
    assert observe(row, [b for b in page if b["text"] != "3,778.09"], "revenue") is None
    assert observe(row, [*page, box("in USD million", 0.72, 0.87)], "net_profit") is None
    assert observe(row, [*page, box("Japanese yen in crores", 0.72, 0.87)], "net_profit") is None
    assert observe(row, [*page, box("Discontinued operations", 0.08, 0.55)], "net_profit") is None
    assert monetary_scalar("1,33,821.05") == Decimal("133821.05")
    assert monetary_scalar("1,93") is None


def test_first_value_can_differ_from_later_filing_only_on_two_corroborating_original_rasters():
    a = {"currency_explicit": True, "value_inr": "5624500000", "unit_inr": 10_000_000}
    b = {**a, "value_inr": "56245000"}
    assert (
        deciding_witness([a, a], 5624500000, 56245000)
        == "agrees_with_original_text_at_two_raster_sizes"
    )
    assert deciding_witness([None, a], 5624500000, 56245000) is None
    assert deciding_witness([a, b], 5624500000, 56245000) is None
    assert deciding_witness([{**a, "currency_explicit": False}, a], 5624500000, None) is None
    assert deciding_witness([a, None], 0, 5624500000) == "agrees_with_structured_filing"
    assert deciding_witness([{**a, "currency_explicit": False}, None], 0, 5624500000) is None


def test_original_basic_eps_needs_an_explicit_per_share_currency_when_structured_is_absent():
    row = {
        "basis": "consolidated",
        "period_end": "2023-03-31",
        "metadata": {"companyName": "SRF Limited"},
    }
    basic = [
        box("Earnings per share in Rs. (face value Rs. 10)", 0.08, 0.42),
        box("Basic", 0.08, 0.405),
        box("18.97", 0.52, 0.405),
        box("12.23", 0.60, 0.405),
    ]
    observed = observe(row, [*statement(), *basic], "eps")
    assert observed["value_inr"] == "18.97"
    assert (
        deciding_witness([observed, observed], 18.97, None)
        == "agrees_with_original_text_at_two_raster_sizes"
    )
    assert (
        deciding_witness([observed, observed], 18.97, 19.1)
        == "agrees_with_original_text_at_two_raster_sizes"
    )
    for title in (
        "Earnings per share (face value Rs. 10)",
        "Earnings per share in USD (Rs. equivalent)",
    ):
        assert observe(row, [*statement(), box(title, 0.08, 0.42), *basic[1:]], "eps") is None
