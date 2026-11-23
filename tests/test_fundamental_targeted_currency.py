import gzip
import json

import pytest

from thesistally.fundamental_targeted_currency import (
    ACCESSION,
    DATA,
    ORIGINAL_URL,
    PDF,
    STRUCTURED_URL,
    XML,
    corroborate_targeted_currency,
    explicit_unit,
)


def source():
    row = {
        "id": ACCESSION,
        "isin": "INE029A01011",
        "period_end": "2021-12-31",
        "basis": "standalone",
        "metadata": {"companyName": "Bharat Petroleum Corporation Limited"},
        "structured_sha256": XML,
        "source_url": STRUCTURED_URL,
        "tags": {"revenue": "RevenueFromOperations"},
        "revenue": 1_185_367_600_000,
    }
    text = gzip.decompress((DATA / "page4-native.txt.gz").read_bytes()).decode()
    docs = [{"pdf_sha256": PDF, "pages": ["", "", "", text]}]
    return (
        row,
        docs,
        {"source_url": ORIGINAL_URL, "announced_at": "2022-01-31T17:22:05+05:30"},
        {"sha256": PDF, "source_url": ORIGINAL_URL},
    )


def test_original_scoped_cell_needs_three_currency_views_and_matching_xbrl():
    row, docs, rel, p = source()
    observed = corroborate_targeted_currency(row, docs, rel, p)
    assert observed["values"] == {
        "revenue": 1_185_367_600_000,
        "operating_profit": None,
        "net_profit": None,
        "eps": None,
    }
    assert observed["field_page_evidence"]["revenue"]["cell"]["raw_cell"] == "1,18,536.76"
    assert observed["field_page_evidence"]["revenue"]["quarter"]["dates"] == [
        "2021-12-31",
        "2021-09-30",
    ]
    for altered in (
        {"revenue": row["revenue"] + 1},
        {"basis": "consolidated"},
        {"isin": "another"},
        {"structured_sha256": "changed"},
        {"metadata": {"companyName": "Another legal entity"}},
        {"tags": {"revenue": "OtherIncome"}},
    ):
        assert corroborate_targeted_currency({**row, **altered}, docs, rel, p) is None
    assert (
        corroborate_targeted_currency(row, docs, {**rel, "announced_at": "2022-01-31"}, p) is None
    )
    docs[0]["pages"][3] += " "
    with pytest.raises(ValueError, match="different source page"):
        corroborate_targeted_currency(row, docs, rel, p)


def test_crore_alone_a_foreign_glyph_or_weak_or_misplaced_glyph_is_insufficient():
    view = json.loads((DATA / "evidence.json").read_bytes())["views"][0]
    assert explicit_unit(view)
    h = view["symbols"]["hin"][1]
    for changed in (
        {"character": "2"},
        {"character": "$"},
        {"confidence": 55},
        {"bounds": [0, 2, 3, 4]},
    ):
        original = h.copy()
        h.update(changed)
        assert not explicit_unit(view)
        h.update(original)
    view["symbols"]["eng"][5]["character"] = "x"
    assert not explicit_unit(view)
