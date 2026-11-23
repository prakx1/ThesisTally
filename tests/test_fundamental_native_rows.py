import gzip
import json
from pathlib import Path

import pytest

from thesistally.discovery_registry import digest
from thesistally.fundamental_native_rows import corroborate

STUDY = Path(__file__).resolve().parents[1] / "data/fundamentals/nse/native-row-study"


def fixture(symbol):
    sample = json.loads((STUDY / "index.json").read_bytes())
    example = next(row for row in sample["issuers"] if row["symbol"] == symbol)
    descriptions = list(example["witnesses"].values())
    assert len({(x["page"], x["pdf_sha256"], x["page_text_sha256"]) for x in descriptions}) == 1
    proof = descriptions[0]
    raw = gzip.decompress((STUDY / (proof["page_text_sha256"] + ".txt.gz")).read_bytes())
    assert digest(raw) == proof["page_text_sha256"]
    return example["filing"], {"pdf_sha256": proof["pdf_sha256"], "pages": [raw.decode()]}


@pytest.mark.parametrize(
    ("sym", "fields"),
    [
        ("UPL", {"revenue", "net_profit", "operating_profit", "eps"}),
        ("AARTIIND", {"revenue", "operating_profit"}),
        ("TRENT", {"revenue", "operating_profit", "net_profit"}),
    ],
)
def test_independent_values_in_original_quarter_cells(sym, fields):
    f, p = fixture(sym)
    result = corroborate(f, [p])
    assert {k for k, v in result["values"].items() if v is not None} == fields
    for k in fields:
        assert result["values"][k] == f[k]
        proof = result["field_page_evidence"][k]
        assert proof["page_text_sha256"] == digest(p["pages"][0].encode())
        assert proof["quarter"]["dates"][0] == f["period_end"]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("UPL Limited", "Sample Chemicals Limited"),
        ("AUDITED CONSOLIDATED", "AUDITED STANDALONE"),
        ("INR in Crores", "GBP in Crores"),
        ("31.03.2022", "31.03.2020"),
        ("Quarter           Quarter", "Year              Quarter"),
    ],
)
def test_ambiguous_or_incorrect_issuer_scope_year_quarter_and_currency(old, new):
    f, p = fixture("UPL")
    assert old in p["pages"][0]
    p["pages"][0] = p["pages"][0].replace(old, new)
    assert corroborate(f, [p]) is None


def test_amount_scale_and_eps_face_value_are_separately_scoped():
    f, p = fixture("UPL")
    p["pages"][0] = p["pages"][0].replace("INR in Crores", "in Crores")
    assert corroborate(f, [p])["values"] == {
        "revenue": None,
        "operating_profit": None,
        "net_profit": None,
        "eps": 18.04,
    }
    p["pages"][0] = p["pages"][0].replace("each (Rs)", "each")
    assert corroborate(f, [p]) is None


def test_conflicting_original_pages_are_not_resolved_using_the_later_amount():
    f, p = fixture("TRENT")
    assert "4,817.68" in p["pages"][0]
    other = dict(p, pdf_sha256="a" * 64, pages=[p["pages"][0].replace("4,817.68", "4,717.68")])
    v = corroborate(f, [p, other])["values"]
    assert (
        v["revenue"] is None
        and v["operating_profit"] is None
        and v["net_profit"] == f["net_profit"]
    )


def test_rounded_or_malformed_original_is_not_replaced_by_expected_xbrl():
    f, p = fixture("UPL")
    f["eps"] += 0.01
    p["pages"][0] = p["pages"][0].replace("15,861", "15,86I")
    v = corroborate(f, [p])["values"]
    assert v["revenue"] is None and v["operating_profit"] is None and v["eps"] is None
    assert v["net_profit"] == f["net_profit"]


@pytest.mark.parametrize("qualifier", ["annualised", "per ADR", "for preference shares"])
def test_basic_eps_qualifier_on_value_row_is_checked(qualifier):
    from thesistally.fundamental_native_rows import _eps_label

    heading = "Earnings per equity share (INR)"
    assert not _eps_label(f"basic earnings per equity share ({qualifier}) (rs)", heading)
    assert _eps_label("basic earnings per equity share (not annualised) (rs)", heading)
