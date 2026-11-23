import gzip
import json
from decimal import Decimal
from pathlib import Path

import pytest

from thesistally.discovery_registry import digest, encoded
from thesistally.fundamental_facts import FIELDS
from thesistally.fundamental_field_review import merge_original_fields
from thesistally.fundamental_ocr import layout
from thesistally.fundamental_review_optics import resolve
from thesistally.fundamental_xbrl_math import operating_profit
from thesistally.fundamental_xbrl_subset import selection


def test_first_release_indexed_operating_profit_in_muthoot_statement():
    f = Path(__file__).parent / "fixtures/muthoot-2026-06-first-statement-page.json.gz"
    source = json.loads(gzip.decompress(f.read_bytes()))
    assert (
        source["page"]["pdf_sha256"]
        == "65bd923c4dc66cb590b01482a5a08c56984acd29c532a1fb0c3ade24bdd53ca5"
    )
    assert (
        source["structured_sha256"]
        == "1a7b2750e8793d1234f0877f02851ee71f5feac7e6956a4aa0103c3e13766b80"
    )
    assert source["release_at"] == "2026-08-01T15:13:20+05:30"
    assert source["page"]["page"] == 16
    text = layout(source["boxes"])
    facts = source["filing"]
    proof = operating_profit(facts, text)
    assert Decimal(proof["value"]) == Decimal(str(facts["operating_profit"])) == 72_323_050_000
    labels = proof["evidence"]["cell"]["inputs"]
    assert len(labels) == 3 and all(s.get("raw_cell") and s.get("comparison") for s in labels)
    assert proof["evidence"]["scope"] == "consolidated"
    label = proof["evidence"]["printed_line"]
    assert "(IV) Total Expenses (IV)" in " ".join(label.split())
    assert operating_profit({**facts, "basis": "standalone"}, text) is None
    assert operating_profit({**facts, "period_end": "2025-06-30"}, text) is None
    assert (
        operating_profit(
            facts, text.replace(label, label.replace("Expenses (IV)", "Expenses (VI)"))
        )
        is None
    )


def test_ambiguous_source_cells_and_unreadable_members_remain_unverified():
    expected = dict.fromkeys(FIELDS, 10)
    observations = [
        {"field": "eps", "value": "9.99"},
        {"field": "eps", "value": "10"},
        {"field": "revenue", "value": "10"},
        {"field": "operating_profit", "value": "20"},
        {"field": "net_profit", "value": "10", "admissible": False},
    ]
    values, evidence, conflicts, misses = resolve(expected, observations)
    assert values == {**dict.fromkeys(FIELDS), "revenue": 10}
    assert list(evidence) == ["revenue"]
    assert sorted(conflicts["eps"]) == ["10", "9.99"] and misses == {"operating_profit": "20"}
    assert resolve(expected, observations, complete=False)[0] == dict.fromkeys(FIELDS)
    original = {"values": expected, "field_page_evidence": {"eps": {"page": 1}}}
    supplemental = {
        "values": dict.fromkeys(FIELDS),
        "original_field_conflicts": conflicts,
        "field_page_evidence": {},
    }
    merged = merge_original_fields(original, supplemental, source="reviewed_xbrl")
    assert merged["values"]["eps"] is None and "eps" not in merged["field_page_evidence"]
    assert merged["additional_reviewed_xbrl_optics"] == supplemental


def test_signed_page_selection_is_exact_and_versioned():
    members = {"A": 3, "B": 7}
    assert selection(members, None, None) == {"mode": "all_pages"}
    scope = selection(members, {"B": [2, 7]}, "statutory-headers")
    assert scope["pages"] == {"A": [], "B": [2, 7]}
    assert scope["nomination_sha256"] == digest(encoded(scope["pages"]))
    for supplied, version in (
        ({"A": [4]}, "v"),
        ({"A": [True]}, "v"),
        ({"B": [1, 1]}, "v"),
        ({"B": [3, 2]}, "v"),
        ({"C": []}, "v"),
        ({}, None),
        (None, "v"),
    ):
        with pytest.raises(ValueError):
            selection(members, supplied, version)
