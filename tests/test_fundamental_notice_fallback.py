from datetime import date

import pytest

from thesistally.fundamental_notice_fallback import corroborate, dated
from thesistally.fundamental_notice_market import intervals, overlap, within_range


def test_issuer_archive_and_independent_vintage_must_match_including_attachment():
    a, b = date(2025, 5, 1), date(2025, 5, 3)
    row = {
        "symbol": "OLD",
        "seq_id": "1",
        "an_dt": "02-May-2025 10:11:12",
        "desc": "Results",
        "attchmntFile": "first.pdf",
    }
    many = dated([row], "OLD", a, b)
    assert corroborate(many, dated([row], "OLD", a, b), a, b)
    assert not corroborate(many, dated([dict(row, attchmntFile="changed.pdf")], "OLD", a, b), a, b)
    with pytest.raises(ValueError, match="Issuer filter"):
        dated([row], "NEW", a, b)


def test_market_overlap_matches_issuer_identity_and_archives_actual_contiguous_periods():
    a, b = date(2025, 5, 1), date(2025, 5, 5)
    row = {
        "symbol": "DELISTED",
        "seq_id": "8",
        "an_dt": "03-May-2025 07:00:00",
        "desc": "Original",
        "attchmntFile": "a.pdf",
    }
    broad = within_range([row], a, b)
    assert overlap(broad, broad, a, b)
    assert not overlap(broad, within_range([dict(row, symbol="OTHER")], a, b), a, b)
    assert intervals(
        [
            {"start": "2025-01-01", "end": "2025-01-31"},
            {"start": "2025-02-01", "end": "2025-02-28"},
            {"start": "2025-04-01", "end": "2025-04-30"},
        ]
    ) == [["2025-01-01", "2025-02-28"], ["2025-04-01", "2025-04-30"]]
