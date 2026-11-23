from datetime import date

import pytest

from thesistally.fundamental_formal_audit import bind
from thesistally.fundamental_notice_proof import candidates
from thesistally.fundamental_nse import digest
from thesistally.fundamental_period import refers_only_to_other_ends


def test_reviewed_clock_must_match_original_bulletin_and_pdf():
    raw = b"%PDF-1.7 issuer-statement"
    url = "https://nsearchives.nseindia.in/corporate/issuer.pdf"
    item = {
        "source_url": url,
        "announced_at": "2025-07-10T15:02:01+05:30",
        "description": "Record Date",
        "period_end": "2025-06-30",
        "isin": "TEST",
        "symbol": "ACME",
        "sequence": "7",
        "text": "Quarter's board packet",
        "catalogue_sha256": "source-digest",
        "catalogue_url": "monthly",
        "basis_evidence": "both",
    }
    bulletin = {"url": "monthly", "sha256": "source-digest"}
    original = {
        "attchmntFile": url,
        "sm_isin": "TEST",
        "symbol": "ACME",
        "seq_id": "7",
        "desc": "Record Date",
        "attchmntText": item["text"],
        "exchdisstime": "10-Jul-2025 15:02:01",
    }
    decision = {
        "source_url": url,
        "retrieved_url": url,
        "sha256": digest(raw),
        "timing_status": "possible_original",
        "reason": "Earlier attachment may contain original results.",
    }
    bound = bind(item, decision, original, bulletin, raw)
    assert bound["declared_basis"] is None
    later = {
        "isin": "TEST",
        "symbol": "ACME",
        "basis": "standalone",
        "filed_at": "2025-07-15T13:02:01+05:30",
        "period_end": "2025-06-30",
    }
    assert candidates(later, [bound]) == [bound]
    with pytest.raises(ValueError, match="archive"):
        bind(
            item,
            dict(decision, retrieved_url="https://example.com/issuer.pdf"),
            original,
            bulletin,
            raw,
        )
    with pytest.raises(ValueError, match="signed PDF"):
        bind(item, decision, original, bulletin, raw + b"x")
    with pytest.raises(ValueError, match="bulletin"):
        bind(
            dict(item, announced_at="2025-07-10T15:01:00+05:30"), decision, original, bulletin, raw
        )


def test_unexpected_category_candidate_does_not_certify_prospective_or_other_quarter():
    r = {
        "isin": "TEST",
        "symbol": "ACME",
        "basis": "consolidated",
        "filed_at": "2025-08-30T14:00:00+05:30",
        "period_end": "2025-06-30",
    }
    n = {
        "isin": "TEST",
        "symbol": "ACME",
        "release_language": "possible_original_financial_packet",
        "announced_at": "2025-07-18T15:20:00+05:30",
        "description": "Dividend",
        "text": "Board approved an interim dividend.",
        "source_url": "https://archives.nseindia.com/corporate/financialresults2025.pdf",
    }
    assert candidates(r, [n]) == [n]
    published = dict(
        n,
        description="Publish Audited Results",
        text="Issuer's original quarterly statement is enclosed",
    )
    assert candidates(r, [published]) == [published]
    assert not candidates(
        r, [dict(n, text="Meeting will be held tomorrow to consider the accounts")]
    )
    assert not candidates(
        r, [dict(n, text="Financial results for the quarter ended March 31, 2025")]
    )
    assert not refers_only_to_other_ends(
        "Quarter ended March 31, 2025 and period ended for FY 2025-26", date(2025, 6, 30)
    )


def test_missing_known_trading_day_does_not_reuse_older_security_identity(tmp_path):
    from thesistally.fundamental_identity import date_witness

    historic = tmp_path / "market"
    historic.mkdir()
    dated = tmp_path / "supplement" / "2026-10-30"
    dated.mkdir(parents=True)
    (dated / "sources.json").write_text("{}")
    result, calendar = date_witness(
        historic,
        [("ACME", "TEST", "2026-11-02", "2026-11-03")],
        supplement=dated.parent,
        calendar_days=[date(2026, 11, 2)],
    )
    assert result == {}
    assert calendar == ["2026-10-30", "2026-11-02"]
