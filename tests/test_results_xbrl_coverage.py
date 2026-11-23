from datetime import datetime

import pytest

from thesistally.fundamental_coverage import METRICS
from thesistally.results_xbrl_coverage import assess, candidates, original_in_basis

DAYS = ["2025-05-09", "2025-05-12", "2025-05-13"]
URL = "https://nsearchives.nseindia.com/corporate/filing.xml"
SHA = "a" * 64


def filing(label, date="2025-05-09T15:30:00+05:30", basis="standalone", **updates):
    return {
        "id": label,
        "isin": "INE001A01036",
        "period_end": "2025-03-31",
        "basis": basis,
        "filed_at": date,
        "announced_at": "2025-04-10T09:00:00+05:30",
        "filing_status": "uncertified",
        "exchange_filing_type": None,
        "structured_document_sha256": SHA,
        "structured_sha256": SHA,
        "structured_url": URL,
        "structured_values": dict.fromkeys(METRICS, 0.0),
        **updates,
    }


def test_xbrl_clock_ignores_pdf_times_and_skips_accounting_projections():
    source = filing("formal", date="2025-05-09T15:30:01+05:30")
    projection = {
        **source,
        "id": "formal/edition-1",
        "original_record": "formal",
        "filed_at": "2025-04-10T09:00:00+05:30",
    }
    [group] = candidates(
        [source, projection, filing("html", structured_url=URL + ".html")]
    ).values()
    assert group == [source]
    assert not assess(group, DAYS[0], DAYS)["covered"]
    assert assess(group, DAYS[1], DAYS)["usable_session"] == DAYS[1]
    assert (
        assess([filing("sat", date="2025-05-10T10:00:00+05:30")], DAYS[1], DAYS)["usable_session"]
        == DAYS[1]
    )
    assert assess([filing("fri")], DAYS[0], DAYS)["covered"]


def test_incomplete_earliest_cannot_be_backfilled_from_a_revision_or_later_original():
    first = filing(
        "missing",
        structured_document_sha256=None,
        structured_sha256=None,
        structured_parse_gap="structured_attachment_unavailable",
    )
    revision = filing("revision", date="2025-05-12T12:00:00+05:30", exchange_filing_type="Revision")
    later = filing("new", date="2025-05-13T12:00:00+05:30")
    outcome = assess([first, revision, later], DAYS[-1], DAYS)
    assert outcome["complete_any_xml_edition"] and not outcome["covered"]
    assert outcome["reason"] == "structured_attachment_unavailable"
    assert not assess([revision], DAYS[-1], DAYS)["covered"]
    assert not assess(
        [filing("correction", exchange_revision_remark="corrigendum")], DAYS[-1], DAYS
    )["covered"]


def test_complete_one_basis_only_and_conflicting_simultaneous_versions():
    partial = filing(
        "con", basis="consolidated", structured_values={**dict.fromkeys(METRICS, 2), "eps": None}
    )
    standalone = filing("single")
    assert assess([partial, standalone], DAYS[0], DAYS)["basis"] == "standalone"
    assert (
        assess([filing("full-con", basis="consolidated"), standalone], DAYS[0], DAYS)["basis"]
        == "consolidated"
    )
    assert not assess([partial, filing("single", structured_values={"eps": 3})], DAYS[0], DAYS)[
        "covered"
    ]
    assert original_in_basis([standalone, filing("other", structured_values={"eps": 100})]) == (
        None,
        "conflicting_simultaneous_first_xbrls",
    )


def test_only_a_certified_xml_with_its_official_clock_can_count():
    for invalid in (
        filing("bad-hash", structured_sha256="d" * 64),
        filing("unverified", structured_parse_gap="Issuer ISIN disagrees with certificate"),
        filing("foreign", structured_url="https://example.com/quarter.xml"),
        filing("undated", filed_at=datetime(2025, 5, 9, 12).isoformat()),
    ):
        with pytest.raises(ValueError):
            candidates([invalid])
    mismatch = filing(
        "wrong-security",
        structured_sha256=None,
        structured_parse_gap="Issuer ISIN disagrees with certificate",
    )
    [group] = candidates([mismatch]).values()
    assert assess(group, DAYS[0], DAYS)["archived_xml_by_cutoff"]
    assert not assess(group, DAYS[0], DAYS)["covered"]
