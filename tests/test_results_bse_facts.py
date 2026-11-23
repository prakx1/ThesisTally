"""Small XBRL instances with the same contexts and transformations as BSE's originals."""

import pytest

from thesistally.results_bse_facts import verify_facts

IDENTITY = {
    "symbol": "HDFCBANK",
    "isin": "INE040A01034",
    "scrip_code": "500180",
    "basis": "consolidated",
    "period_end": "2020-06-30",
}
NS = """xmlns:x="http://www.xbrl.org/2003/instance" xmlns:iso="http://www.xbrl.org/2003/iso4217"
 xmlns:f="http://www.bseindia.com/xbrl/fin/2018-03-31/in-bse-fin"
 xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12" """
CONTEXT = """<x:context id="OneD"><x:entity><x:identifier>500180</x:identifier></x:entity>
 <x:period><x:startDate>2020-04-01</x:startDate><x:endDate>2020-06-30</x:endDate></x:period></x:context>
 <x:unit id="rs"><x:measure>iso:INR</x:measure></x:unit>
 <x:unit id="eps"><x:divide><x:unitNumerator><x:measure>iso:INR</x:measure></x:unitNumerator>
 <x:unitDenominator><x:measure>x:shares</x:measure></x:unitDenominator></x:divide></x:unit>"""
IDS = [
    ("Symbol", "HDFCBANK"),
    ("ISIN", "INE040A01034"),
    ("ScripCode", "500180"),
    ("NatureOfReportStandaloneConsolidated", "Consolidated"),
    ("ReportingQuarter", "First quarter"),
]
BANK = [
    ("Income", "rs", "366985900000"),
    ("OperatingProfitBeforeProvisionAndContingencies", "rs", "136840200000"),
    ("ProfitLossForThePeriod", "rs", "69408800000"),
    ("BasicEarningsPerShareAfterExtraordinaryItems", "eps", "12.6"),
]


def bank_xml(ids=IDS, amounts=BANK):
    records = [f'<f:{name} contextRef="OneD">{text}</f:{name}>' for name, text in ids] + [
        f'<f:{name} contextRef="OneD" unitRef="{unit}">{text}</f:{name}>'
        for name, unit, text in amounts
    ]
    return f"<x:xbrl {NS}>{CONTEXT}{''.join(records)}</x:xbrl>".encode()


def test_reported_bank_uses_income_and_profit_before_provisions():
    values, why = verify_facts(bank_xml(), **IDENTITY)
    assert why == "verified" and values["eps"] == 12.6 and values["revenue"] == 366985900000
    assert values["operating_profit"] == 136840200000 and values["net_profit"] == 69408800000
    assert values["tags"]["quarter_context"] == "OneD" and values["format"] == "xbrl"


@pytest.mark.parametrize(
    "before, after",
    [
        (b"INE040A01034", b"INE040A01035"),
        (b'<f:ISIN contextRef="OneD">', b'<f:ISIN contextRef="TwoD">'),
        (b"Consolidated", b"Standalone"),
        (b"2020-04-01", b"2019-07-01"),
        (b"2020-06-30", b"2020-09-30"),
        (b"<x:identifier>500180", b"<x:identifier>500696"),
        (b"iso:INR", b"iso:USD"),
        (b"http://www.xbrl.org/2003/iso4217", b"https://unregistered.example/iso4217"),
        (b"x:shares", b"x:pure"),
        (
            b"<x:period>",
            b"<x:segment><x:explicitMember>segment</x:explicitMember></x:segment><x:period>",
        ),
    ],
)
def test_near_matches_or_uncertified_units_are_not_quarters(before, after):
    values, why = verify_facts(bank_xml().replace(before, after), **IDENTITY)
    assert values is None and why != "verified"


def test_annual_zero_placeholders_cannot_qualify_and_year_to_date_cannot_substitute():
    ids = [(k, "Yearly" if k == "ReportingQuarter" else v) for k, v in IDS]
    zero = [(k, unit, "0") for k, unit, _ in BANK]
    xml = bank_xml(ids, zero).replace(
        b"</x:xbrl>", b"""<f:Income contextRef="FourD" unitRef="rs">10000</f:Income></x:xbrl>"""
    )
    assert verify_facts(xml, **IDENTITY)[0] is None
    # Zero is permissible when actually published as the quarter's performance.
    assert verify_facts(bank_xml(IDS, zero), **IDENTITY)[0]["eps"] == 0


def inline():
    tags = [f'<ix:nonNumeric name="f:{k}" contextRef="OneD">{v}</ix:nonNumeric>' for k, v in IDS]
    tags.append(
        '<ix:nonNumeric name="f:DateOfEndOfReportingPeriod" contextRef="OneD" format="ixt:datedaymonthyear">30-06-2020</ix:nonNumeric>'
    )
    for name, num, attributes, unit in [
        ("RevenueFromOperations", "73,188.00", 'scale="7" format="ixt:numdotdecimalin"', "rs"),
        ("Expenses", "55,887.00", 'scale="7" format="ixt:numdotdecimalin"', "rs"),
        ("FinanceCosts", "252.00", 'scale="7"', "rs"),
        ("ProfitLossForPeriod", "34.00", 'scale="7" sign="-"', "rs"),
        (
            "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
            "0.05",
            'sign="-"',
            "eps",
        ),
    ]:
        tags.append(
            f'<ix:nonFraction name="f:{name}" contextRef="OneD" unitRef="{unit}" {attributes}>{num}</ix:nonFraction>'
        )
    return f'<html xmlns="http://www.w3.org/1999/xhtml" {NS}><body>{CONTEXT}{"".join(tags)}</body></html>'.encode()


def test_signed_scaled_indian_inline_facts_and_operating_profit_arithmetic():
    v, why = verify_facts(inline(), **IDENTITY)
    assert why == "verified" and v["format"] == "ixbrl"
    assert v["revenue"] == 731880000000 and v["operating_profit"] == 175530000000
    assert v["net_profit"] == -340000000 and v["eps"] == -0.05
    assert v["tags"]["operating_profit"] == "RevenueFromOperations-Expenses+FinanceCosts"


@pytest.mark.parametrize(
    "before, after",
    [
        (b"numdotdecimalin", b"unsupported-transform"),
        (b"http://www.xbrl.org/2013/inlineXBRL", b"http://example.test/plain-html"),
        (b"http://www.bseindia.com/xbrl/", b"http://example.test/xbrl/"),
        (b"30-06-2020", b"31-03-2020"),
    ],
)
def test_untagged_html_and_incompatible_transform_or_period_are_rejected(before, after):
    assert verify_facts(inline().replace(before, after), **IDENTITY)[0] is None
