import json
from datetime import date

import pytest

from thesistally import baseline_static_bands as bands
from thesistally.market import MarketError, sha


def test_exact_numeric_range_has_no_rounding_convention():
    assert bands.exact_limits("100.25", "20") == [80.2, 120.3]
    assert bands.exact_limits("2057.45", "10") is None
    assert bands.exact_limits("182", "No Band") is None
    assert bands.exact_limits("NaN", "5") is None


def test_static_evidence_is_specific_to_the_opening_date(tmp_path):
    folder = tmp_path / "sources/static-bands"
    folder.mkdir(parents=True)
    day = date(2020, 1, 2)
    known = {"STOCK": {"isin": "I", "floor": 80.2, "ceiling": 120.3}}
    p = folder / (str(day) + ".json")
    p.write_text(json.dumps({"bounds": known}))
    (folder / "coverage.json").write_text(json.dumps({"days": {str(day): sha(p.read_bytes())}}))
    assert bands.overlay(tmp_path, date(2020, 1, 1)) == {}
    assert bands.overlay(tmp_path, day) == known
    p.write_text("{}")
    with pytest.raises(MarketError, match="Unverified"):
        bands.overlay(tmp_path, day)
