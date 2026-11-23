from thesistally.fundamental_timeline import first_fact_timeline

EARLY = "2014-02-02T15:28:00+05:30"
LATE = "2014-02-02T15:31:02+05:30"


def edition(t, facts, absent=()):
    return {
        "announced_at": t,
        "proof": {
            "values": facts,
            "exhaustive_body": bool(absent),
            "absent_fields": list(absent),
            "notice_sha256": "0" * 64,
            "notice_url": "https://www.nseindia.in/official",
        },
    }


def attest(n):
    return n.get("proof"), None if n.get("proof") else "earlier_document_unavailable"


def test_original_fields_have_publication_clocks_and_declared_absence():
    early = edition(EARLY, {"net_profit": 110}, ["eps", "operating_profit"])
    later = edition(LATE, {"net_profit": 109, "eps": 7, "operating_profit": 120, "revenue": 600})
    row, reason = first_fact_timeline([early, later], attest)
    assert reason is None
    assert row["values"] == {"revenue": None, "operating_profit": 120, "net_profit": 110, "eps": 7}
    assert row["fact_announced_at"] == {"operating_profit": LATE, "net_profit": EARLY, "eps": LATE}
    assert row["complete_available_at"] is None
    assert row["announced_at"] == LATE and row["initial_disclosure_at"] == EARLY
    assert row["editions"][1][1]["values"]["net_profit"] == 109


def test_unknown_intervening_pdf_prevents_backfilling_earlier_eps():
    early = edition(EARLY, {"net_profit": 110}, ["eps", "operating_profit", "revenue"])
    unknown = {"announced_at": "2014-02-02T15:30:00+05:30"}
    later = edition(LATE, {"net_profit": 110, "eps": 7})
    row, _ = first_fact_timeline([early, unknown, later], attest)
    assert row["values"]["eps"] is None
    assert row["remaining_fields_gap"] == "earlier_document_unavailable"


def test_unknown_pdf_and_conflicting_simultaneous_originals():
    earlier = {"announced_at": EARLY}
    later = edition(LATE, {"eps": 4})
    assert first_fact_timeline([earlier, later], attest) == (None, "earlier_document_unavailable")
    other = edition(LATE, {"eps": 5})
    assert first_fact_timeline([later, other], attest) == (
        None,
        "conflicting_original_fields_at_same_clock",
    )


def test_earlier_category_requires_same_recognised_earnings_attachment_for_quarter():
    from thesistally.fundamental_notice_proof import candidates

    f = {"symbol": "CO", "isin": "INE000A12345", "period_end": "2013-12-31", "filed_at": LATE}
    normal = {
        "isin": f["isin"],
        "symbol": "CO",
        "text": "Quarterly financial results for the quarter ended 31 December 2013.",
        "description": "Financial Results",
        "announced_at": LATE,
        "source_url": "https://archives.nseindia.com/board.pdf",
        "sequence": "123",
    }
    earlier = {
        **normal,
        "announced_at": EARLY,
        "description": "Dividend",
        "text": "Dividend published",
        "sequence": "456",
        "release_language": "identical_earnings_attachment",
        "later_matching_notice": normal,
    }
    assert candidates(f, [earlier, normal]) == [earlier, normal]
    assert candidates(f, [earlier]) == []


def test_original_text_layer_decimal_and_table_errors_are_unscored_without_substitution():
    from thesistally.fundamental_field_review import verify_text_fields

    for key, first, later in [
        ("eps", 834, 8.34),
        ("eps", 68, 6.8),
        ("revenue", 10_000_000, 19_300_000_000),
    ]:
        facts, review = verify_text_fields(
            {key: first, "net_profit": 132}, {"net_profit": 132, key: later}, "a" * 64
        )
        assert facts[key] is None and facts["net_profit"] == 132
        assert (
            review[key]["earlier_text_candidate"] == first
            and review[key]["structured_value"] == later
        )
        assert review[key]["status"] == "conflicting_original_text_and_structured"
    facts, review = verify_text_fields({"eps": 834}, {"eps": 834}, None)
    assert facts["eps"] is None and review["eps"]["status"] == "no_independent_numeric_confirmation"


def test_other_scope_requires_both_exchange_submission_and_readable_attachment():
    from thesistally.fundamental_scope import corroborated_other, declared_other

    body = "An issuer has submitted to the Exchange the Standalone Financial Results for the period ended June 30, 2012."
    note = {"text": body}
    pdfs = [
        {
            "pages": [
                "Financial accounting statement submitted by the listed issuer. " * 3
                + "STANDALONE financial results.\n",
                "",
            ]
        }
    ]
    assert declared_other(note, "consolidated") == "standalone"
    assert corroborated_other(note, pdfs, "consolidated")
    assert not corroborated_other(note, [{"pages": [""]}], "consolidated")
    assert not corroborated_other(
        note, [{"pages": [pdfs[0]["pages"][0] + " consolidated"]}], "consolidated"
    )
    assert not declared_other({"text": body + " Also consolidated."}, "consolidated")


def test_sparse_optical_fields_keep_independent_original_pages_and_clock():
    from thesistally.fundamental_field_review import merge_original_fields
    from thesistally.fundamental_timeline import first_fact_timeline

    date = "2014-05-02T14:00:00+05:30"
    a = {
        "values": {"eps": 4.5, "net_profit": 200, "revenue": None, "operating_profit": None},
        "page": 5,
        "pdf_sha256": "documentA",
        "notice_url": "original",
        "notice_sha256": "package",
    }
    optical = {
        "values": {"eps": 4.5, "revenue": 700, "net_profit": None, "operating_profit": None},
        "field_page_evidence": {
            "revenue": {"page": 7, "pdf_sha256": "documentB"},
            "eps": {"page": 6, "pdf_sha256": "documentA"},
        },
    }
    merged = merge_original_fields(a, optical)
    result, error = first_fact_timeline([{"announced_at": date}], lambda n: (merged, None))
    assert not error and result["values"] == {
        "eps": 4.5,
        "net_profit": 200,
        "revenue": 700,
        "operating_profit": None,
    }
    assert result["fact_sources"]["eps"]["page"] == 5
    assert result["fact_sources"]["revenue"]["page"] == 7
    assert result["fact_sources"]["revenue"]["pdf_sha256"] == "documentB"
    assert result["fact_announced_at"]["revenue"] == date
    conflict = merge_original_fields(a, {**optical, "values": {**optical["values"], "eps": 45}})
    assert conflict["values"]["eps"] is None and conflict["values"]["revenue"] == 700
    assert conflict["original_field_conflicts"]["eps"] == {"primary": 4.5, "optical": 45}


def test_source_backed_eps_absence_stops_at_opaque_cotimed_and_unexamined_notices():
    from thesistally.fundamental_notice_proof import audited_through

    begin = "2014-02-02T10:00:00+05:30"
    unknown = "2014-02-04T15:29:00+05:30"
    later = "2014-02-06T10:00:00+05:30"
    summary = edition(begin, {"net_profit": 123}, ["eps"])
    opaque = {"announced_at": unknown}
    full = edition(later, {"eps": 4.5})
    for events, end in (
        ([summary, opaque, full], unknown),
        ([summary, full], later),
        ([summary], later),
    ):
        result, _ = first_fact_timeline(events, attest, audited_through=later)
        assert (result["eps_absence_from"], result["eps_absence_before"]) == (begin, end)
        assert result["eps_absence_evidence"][0]["sha256"] == "0" * 64
    mixed, _ = first_fact_timeline(
        [summary, {"announced_at": begin}, full], attest, audited_through=later
    )
    assert mixed["eps_absence_before"] is None
    unknown_scope, _ = first_fact_timeline([summary, full], attest)
    assert unknown_scope["eps_absence_before"] is None
    row = {"period_end": "2013-12-31", "filed_at": later}
    tape = {"from_date": "2011-01-01", "to_date": "2014-02-05", "total": 2}
    assert audited_through(tape, row) is None
    assert audited_through({**tape, "to_date": "2014-02-06"}, row) == later


def test_future_board_schedule_never_becomes_an_opposite_basis_earnings_filing(monkeypatch):
    from thesistally import fundamental_notice_only as source

    monkeypatch.setattr(
        source,
        "members",
        lambda: [
            {
                "symbol": "SHREECEM",
                "isin": "INE070A01015",
                "from_date": "2014-01-01",
                "to_date": "2026-11-20",
            }
        ],
    )
    base = {
        "symbol": "SHREECEM",
        "isin": "INE070A01015",
        "issuer_name": "Shree Cement Limited",
        "catalogue_url": "https://www.nseindia.in/official",
        "sequence": "102999831",
        "description": "Updates",
        "announced_at": "2018-10-11T18:41:00+05:30",
    }
    agenda = {
        **base,
        "text": "A meeting of the Board of Directors will be held on 12 November 2018 to consider and approve the standalone financial results for the quarter ended 30 September 2018. The trading window will remain closed.",
    }
    assert source.supplement([], {"SHREECEM": [agenda]}) == []
    mixed = {
        **agenda,
        "text": agenda["text"]
        + " The company has submitted the financial results for the quarter ended 30 September 2018.",
    }
    assert {r["basis"] for r in source.supplement([], {"SHREECEM": [mixed]})} == {
        "standalone",
        "consolidated",
    }
    exact = {**mixed, "declared_basis": "standalone"}
    assert [r["basis"] for r in source.supplement([], {"SHREECEM": [exact]})] == ["standalone"]


def test_fy_range_and_split_printed_dates_do_not_replace_earlier_board_result_with_late_xbrl():
    from datetime import date

    from thesistally.fundamental_notice_proof import candidates, period_match
    from thesistally.fundamental_period import explicit_end_dates

    def first(text, year, sym):
        filing = {
            "symbol": sym,
            "isin": "SECURITY",
            "basis": "consolidated",
            "period_end": f"{year}-03-31",
            "filed_at": f"{year}-06-01T15:00:00+05:30",
        }
        n = {
            "symbol": sym,
            "description": "Outcome of Board Meeting",
            "text": text,
            "announced_at": f"{year}-04-28T13:00:35+05:30",
            "source_url": "https://archives.nseindia.com/outcome.pdf",
        }
        return candidates(filing, [n])

    k = "Outcome of Board Meeting held on April 28, 2025 - Financial Results for the quarter & year ended 2024-25."
    assert first(k, 2025, "KPITTECH") and period_match(k, date(2025, 3, 31))
    assert not period_match(k, date(2025, 6, 30))
    m = "Audited Financial Results (Consolidated and Standalone) for the quarter and year ended 3 1 March 2026."
    assert first(m, 2026, "MAZDOCK") and explicit_end_dates(m) == {date(2026, 3, 31)}
    v = "Approval of Annual Accounts, Recommendation of dividend for 2025-26 and Appointment of Director."
    assert first(v, 2026, "VOLTAS")
    assert not first(
        "Published financial results for the quarter ended June 30, 2025.", 2026, "VOLTAS"
    )
    assert first("Financial results for the period ended in FY 2025-26.", 2026, "VOLTAS")

    assert first(
        "Financial results for the period ended FY 2025-26 and restated results "
        "for the quarter ended June 30, 2025.",
        2026,
        "VOLTAS",
    )
    assert not first(
        "Financial results for the quarter ended June 30, 2025 and the period ended "
        "September 30, 2025.",
        2026,
        "VOLTAS",
    )
