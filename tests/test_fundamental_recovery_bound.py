import json
from hashlib import sha256

import pytest

from thesistally.fundamental_recovery_bound import diagnostic, recovery_counts

DEC = "2016-12-31"
MAR = "2017-03-31"


def test_bound_counts_each_eligible_issuer_quarter_once_in_its_filing_season():
    def row(issuer, period, covered):
        return {
            "isin": issuer,
            "period_end": period,
            "covered_by_window_end": str(covered),
            "filing_season_year": "2017",
        }

    records = [
        row("A", DEC, True),
        row("A", MAR, False),
        row("B", DEC, False),
        row("C", MAR, False),
        row("D", MAR, False),
    ]
    known = {("A", DEC), ("A", MAR), ("B", DEC), ("x", DEC)}
    quarantine = {("A", MAR), ("C", MAR)}
    result = recovery_counts(
        {"2017": {"periods": [DEC, MAR], "eligible": 5, "covered": 1}}, records, known, quarantine
    )["2017"]
    assert result["additional_unique_pairs_if_all_succeed"] == 3
    assert result["uncovered_in_both_queues"] == 1
    assert result["fixed_nse_work_ceiling"] == 4
    assert result["observed_complete_by_window_end"] == 1
    assert result["both_exchanges_ceiling_with_unbounded_bse"] == 5
    assert result["additional_uncertified_bse_pairs_possible_beyond_nse_ceiling"] == 1


def test_wrong_cohort_duplicate_pairs_or_wrong_observed_count_invalidate_bound():
    row = {
        "isin": "A",
        "period_end": DEC,
        "covered_by_window_end": "False",
        "filing_season_year": "2017",
    }
    w = {"2017": {"eligible": 1, "covered": 0, "periods": [DEC]}}
    with pytest.raises(ValueError, match="Duplicate"):
        recovery_counts(w, [row, row], set(), set())
    with pytest.raises(ValueError, match="cohorts differ"):
        recovery_counts(w, [{**row, "period_end": MAR}], set(), set())
    with pytest.raises(ValueError, match="signed window"):
        recovery_counts(w, [{**row, "covered_by_window_end": "True"}], set(), set())


def test_incomplete_source_is_rejected_without_scanning_packets_or_creating_files(tmp_path):
    raw = json.dumps(
        {"filing_sources": {"nse": {"source_acquisition": {"complete": False}}}}
    ).encode()
    key = sha256(raw).hexdigest()
    path = tmp_path / "coverage-history" / (key + ".json")
    path.parent.mkdir()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="Interim acquisition"):
        diagnostic(tmp_path, key)
    assert list(tmp_path.rglob("*.json")) == [path]
