from datetime import date

from thesistally import legacy_evidence as evidence
from thesistally.legacy_surveillance import GSM, LT, ST, component


def test_only_all_known_components_can_clear():
    assert evidence.compose((False, False, False)) is False
    assert evidence.compose((False, None, False)) is None
    assert evidence.compose(iter([False, None, False])) is None
    assert evidence.compose((None, None, True)) is True


def test_gsm_requires_dated_id_and_explicit_negative_on_unique_row():
    raw = b"Symbol,Series,Security Name,Band,Remarks\nA,EQ,x,5,-\nB,BE,y,2,GSM - Stage 0\nC,EQ,z,5,unclassified\nA,EQ,x,2,GSM STAGE - I\nD,BE,x,2,-\nE,BE,x,5,-\n"
    ids = {
        ("A", "EQ"): "a",
        ("B", "EQ"): "b",
        ("C", "EQ"): "c",
        ("D", "EQ"): "d",
        ("D", "BZ"): "different",
    }
    assert evidence.gsm_table(raw, ids, set(ids.values())) == {"b": True}


def test_incomplete_annex_can_only_prove_positives():
    header = {"A": "Sr. No.", "D": "ISIN"}
    valid = {"A": "1", "D": "INE216A01022"}
    assert evidence.parse_table([header, valid]) == ({"INE216A01022"}, True, [])
    for invalid in (
        {"A": "2", "C": "no ISIN"},
        {"A": "3", "D": "INE463A01038"},
        {"A": "2", "D": "INE216A01023"},
    ):
        assert evidence.parse_table([header, valid, invalid])[1] is False


def test_dated_asm_consolidated_vs_changes_and_special_lists(monkeypatch):
    target = date(2022, 4, 1)
    sheets = {
        "Annexure": [{"A": "Short - Term ASM framework w.e.f.April 1st, 2022"}],
        "Consolidated": [
            {"A": "Consolidated list of securities shortlisted in ASM Framework"},
            {"A": "Sr. No.", "D": "ISIN"},
            {"A": "1", "D": "INE216A01022"},
        ],
        "Consolidated - ASM (IBC)": [
            {
                "A": "Consolidated list of securities in ASM for Insolvency Resolution Process w.e.f. April 2, 2022"
            }
        ],
    }
    monkeypatch.setattr(evidence, "workbooks", lambda _: [sheets])
    assert evidence.asm_status(b"", ST, target) == ({"INE216A01022"}, True, [])
    assert not evidence.asm_status(b"", ST, date(2022, 4, 2))[1]
    sheets.pop("Consolidated")
    assert not evidence.asm_status(b"", ST, target)[1]


def test_subject_classifier():
    for title, expected in (
        ("Applicability of Short Term Additional Surveillance Measure (ST-ASM)", ST),
        ("Applicability of Additional Surveillance Measure (ASM)", LT),
        ("Graded Surveillance Measure (GSM) – Updated list of companies", GSM),
        ("Additional Surveillance Measure (ASM) under IBC", None),
    ):
        assert component({"sub": title}) == expected


def test_conflicting_same_date_policy_and_workbook_fail_closed(tmp_path, monkeypatch):
    from thesistally import legacy_snapshot as snapshots

    path = tmp_path / "raw"
    path.mkdir()
    (path / "SURV1.zip").touch()
    notices = [
        {"sub": "Applicability of Additional Surveillance Measure (ASM)", "circFilename": f}
        for f in ("SURV1.zip", "SURV2.pdf")
    ]
    monkeypatch.setattr(snapshots, "checked_raw", lambda *_: b"")
    monkeypatch.setattr(snapshots, "asm_status", lambda *_: ({"INE216A01022"}, True, []))
    ids, complete, _, reasons = snapshots.asm_on_day(tmp_path, LT, notices, date(2018, 5, 31))
    assert ids == set() and not complete
    assert "same-day" in reasons[0]


def test_quarterly_superset_never_clears_after_unresolved_order(tmp_path):
    from thesistally.legacy_snapshot import QuarterlyGSM

    state = QuarterlyGSM(tmp_path)
    state.pool = {"INE216A01022"}

    def apply(title):
        state.apply({"sub": title, "circFilename": "SURV1.pdf", "cirDate": "20171002"})

    apply("Securities moving out of Graded Surveillance Measure (GSM) - Update")
    apply("Graded Surveillance Measure (GSM) - Periodic relaxation of Surveillance action")
    assert state.pool == {"INE216A01022"}
    apply("SEBI directions with respect to listed Shell Companies")
    assert state.pool is None


def test_legacy_gsm_punctuation_and_unrecognised_remarks():
    src = b"Symbol,Series,Security Name,Band,Remarks\nA,EQ,x,5,GSM - Stage II\nB,EQ,x,5,GSM STAGE - 1\nC,EQ,x,5,GSM STAGE -IV\nD,EQ,x,5,\n"
    lookup = {(sym, "EQ"): sym for sym in "ABCD"}
    assert evidence.gsm_table(src, lookup, set("ABCD")) == dict.fromkeys("ABC", True)


def test_changes_only_prove_listed_isin_on_effective_open(monkeypatch):
    sheets = {
        "Annexure": [
            {"A": "List of securities shortlisted in ASM Framework w.e.f. March 26, 2018"},
            {"A": "Sr. No.", "D": "ISIN"},
            {"A": "1", "D": "INE216A01022"},
            {"A": "List of securities to be excluded from ASM Framework w.e.f. March 26, 2018"},
            {"A": "Sr. No.", "D": "ISIN"},
            {"A": "1", "D": "INE467B01029"},
        ]
    }
    monkeypatch.setattr(evidence, "workbooks", lambda _: [sheets])
    ids, complete, _ = evidence.asm_status(b"", LT, date(2018, 3, 26))
    assert ids == {"INE216A01022"}
    assert not complete
    assert evidence.asm_status(b"", LT, date(2018, 3, 27))[0] == set()


def test_pdf_stage_letter_requires_complete_numbered_single_list(monkeypatch):
    from thesistally import legacy_snapshot as snapshots

    letter = "The following securities shall be moved to stage II of GSM.\nSr. No. Symbol   Security Name  ISIN\n1  ABC    Company    INE216A01022\nTrading in these securities shall move to...\n"
    monkeypatch.setattr(snapshots, "pdf_text", lambda _: letter)
    assert snapshots.stage_pdf(b"") == {"INE216A01022"}
    letter += "Second, attached group: INE467B01029"
    assert snapshots.stage_pdf(b"") is None
