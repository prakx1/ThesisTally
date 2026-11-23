from datetime import datetime

from thesistally.fundamental_coverage import (
    _ratio,
    first_session,
    measure,
    reconcile,
    stock_quarters,
)

SESSIONS = ["2020-03-31", "2020-05-08", "2020-05-11", "2020-05-12"]
ROW = {
    "isin": "A",
    "symbol": "OLD",
    "period_end": "2020-03-31",
    "exchange": "BSE",
    "revenue": 200,
    "operating_profit": 20,
    "net_profit": 10,
    "eps": 2.0,
    "basis": "standalone",
    "announced_at": "2020-05-08T15:30:00+05:30",
    "first_reported": True,
    "source_url": "https://www.bseindia.com/filing.xml",
    "sha256": "f" * 64,
}


def test_session_exact_close_after_close_and_weekend():
    for day, target in [
        ("2020-05-08T15:30:00", "2020-05-08"),
        ("2020-05-08T15:30:01", "2020-05-11"),
        ("2020-05-09T10:00:00", "2020-05-11"),
        ("2020-05-12T16:30:00", None),
    ]:
        assert first_session(datetime.fromisoformat(day + "+05:30"), SESSIONS) == target
    assert first_session(datetime.fromisoformat("2019-07-05T15:00:00+05:30"), SESSIONS) is None


def test_first_publication_never_uses_later_values_and_keeps_both_reporting_bases():
    rows = [
        dict(ROW, eps=None),
        dict(ROW, announced_at="2020-05-08T15:32:01+05:30"),
        dict(ROW, first_reported=False, eps=11),
        dict(ROW, basis="consolidated", announced_at="2020-05-09T16:15:00+05:30"),
    ]
    original, gaps = reconcile(rows, SESSIONS)
    assert [(r["basis"], r["eps"], r["usable_session"]) for r in original] == [
        ("consolidated", 2.0, "2020-05-11"),
        ("standalone", None, "2020-05-08"),
    ]
    assert "missing_one_or_more_quarterly_facts" in gaps["A", "2020-03-31"]
    orig, g = reconcile([ROW, dict(ROW, eps=12.5)], SESSIONS)
    assert not orig and "conflicting_first_publication" in g["A", "2020-03-31"]


def test_filing_season_denominator_uses_dated_isin_and_excludes_future_publications():
    sessions = ["2019-12-30", "2020-03-31", "2020-06-30", "2020-09-30", "2020-12-31", "2021-01-02"]

    def member(sym, isin, start, end):
        return {
            "symbol": sym,
            "isin": isin,
            "from_date": start,
            "to_date": end,
            "source_url": "official",
        }

    members = [
        member("OLD", "A", "2019-01-01", "2020-08-01"),
        member("NEW", "A", "2020-08-02", "2021-01-03"),
        member("B", "B", "2019-01-01", "2019-12-29"),
    ]
    picks = list(stock_quarters(members, sessions))
    assert len(picks) == 5 and picks[0]["symbol"] == "OLD"
    assert picks[3]["symbol"] == "NEW"
    late = dict(ROW, period_end="2019-12-31", announced_at="2021-01-02T11:00:00+05:30")
    _, _, years, windows, _ = measure(members, sessions, [late, ROW])
    assert years["2019"]["covered"] == 1
    assert windows["2020"]["eligible"] == 4 and windows["2020"]["covered"] == 1
    assert _ratio(4, 5)["coverage_eligible"] and not _ratio(79, 100)["coverage_eligible"]


def test_an_earlier_unresolved_primary_prevents_a_later_secondary_from_becoming_first():
    dated = dict(ROW, announced_at="2020-05-11T14:00:00+05:30")
    early = dict(ROW, first_reported=False, exchange="NSE")
    later = dict(dated, first_reported=False)
    orig, gaps = reconcile([dated, early], SESSIONS)
    assert not orig and "earlier_unresolved_publication" in gaps["A", "2020-03-31"]
    assert len(reconcile([ROW, later], SESSIONS)[0]) == 1


def test_earlier_unresolved_notice_of_a_delayed_table_also_blocks_later_secondary():
    delayed = dict(
        ROW,
        first_reported=False,
        exchange="NSE",
        announced_at="2020-05-12T18:00:00+05:30",
        first_notice={"announced_at": "2020-05-08T14:00:00+05:30"},
    )
    original, gaps = reconcile([ROW, delayed], SESSIONS)
    assert not original and "earlier_unresolved_publication" in gaps["A", "2020-03-31"]


def test_content_archive_is_idempotent_and_detects_tampering(tmp_path):
    import gzip
    import hashlib

    import pytest

    from thesistally.fundamental_coverage import _archive

    raw = b'{"eps":2}\n'
    sha = hashlib.sha256(raw).hexdigest()
    _archive(tmp_path, sha, raw)
    _archive(tmp_path, sha, raw)
    (tmp_path / (sha + ".gz")).write_bytes(gzip.compress(b"revised"))
    with pytest.raises(ValueError, match="differs"):
        _archive(tmp_path, sha, raw)


def test_ledger_loads_archived_plaintext_hash_and_rejects_changed_convenience_pointer(tmp_path):
    import gzip
    import hashlib
    import json

    import pytest

    from thesistally.fundamental_coverage import ledger_bytes

    source = tmp_path / "nse"
    (source / "vintages").mkdir(parents=True)
    raw = b'{"eps":3}\n'
    sha = hashlib.sha256(raw).hexdigest()
    (source / "filings-receipt.json").write_text(json.dumps({"sha256": sha}))
    (source / "vintages" / (sha + ".jsonl.gz")).write_bytes(gzip.compress(raw))
    assert ledger_bytes(tmp_path, "nse", sha) == raw
    (source / "withdrawals").mkdir()
    marker = source / "withdrawals" / (sha + ".json")
    marker.write_text(json.dumps({"reason": "QA release withdrawn"}))
    with pytest.raises(ValueError, match="withdrawn"):
        ledger_bytes(tmp_path, "nse", sha)
    marker.unlink()
    (source / "filings.jsonl").write_bytes(b"changed")
    with pytest.raises(ValueError, match="certified SHA"):
        ledger_bytes(tmp_path, "nse", sha)


def test_full_coverage_rebuild_preserves_both_vintages_and_reproduces_published_hashes(tmp_path):
    import gzip
    import hashlib
    import json

    from thesistally.fundamental_coverage import build

    membership = tmp_path / "members.csv"
    membership.write_text(
        "symbol,isin,from_date,to_date,source_url\n"
        "OLD,A,2020-01-01,2020-12-31,official\n"
        "TRANSIENT,B,2020-01-01,2020-02-01,official\n"
    )
    cert = tmp_path / "calendar.json"
    cert.write_text(
        json.dumps(
            {
                "complete": True,
                "sessions": SESSIONS,
                "end": SESSIONS[-1],
                "canonical_membership": {
                    "published_sha256": hashlib.sha256(membership.read_bytes()).hexdigest()
                },
            }
        )
    )
    data = tmp_path / "fundamentals"
    nse = data / "nse"
    (nse / "vintages").mkdir(parents=True)
    (data / "bse").mkdir()
    (data / "bse" / "filings.jsonl").write_text("")
    digests = []
    for eps in (2.0, None):
        raw = (json.dumps(dict(ROW, eps=eps)) + "\n").encode()
        sha = hashlib.sha256(raw).hexdigest()
        (nse / "vintages" / (sha + ".jsonl.gz")).write_bytes(gzip.compress(raw))
        (nse / "filings-receipt.json").write_text(json.dumps({"sha256": sha}))
        receipt = build(data, membership, cert)
        assert (
            receipt["membership_isins"] == 2 and receipt["isins_with_quarter_end_membership"] == 1
        )
        assert receipt["eligible"] == 1 and receipt["covered"] == int(eps is not None)
        with (data / "stock-year-coverage.csv").open() as source:
            import csv

            stock_years = list(csv.DictReader(source))
        assert [(r["isin"], r["year"], r["eligible"], r["covered"]) for r in stock_years] == [
            ("A", "2020", "1", str(int(eps is not None)))
        ]
        for name, expected in receipt["products"].items():
            assert hashlib.sha256((data / name).read_bytes()).hexdigest() == expected
        digests.append(hashlib.sha256((data / "coverage.json").read_bytes()).hexdigest())
    assert len(set(digests)) == 2
    for digest in digests:
        history = json.loads((data / "coverage-history" / (digest + ".json")).read_text())
        key = history["products"]["first-releases.jsonl"]
        assert (
            hashlib.sha256(
                gzip.decompress((data / "coverage-history/objects" / (key + ".gz")).read_bytes())
            ).hexdigest()
            == key
        )


def test_notice_only_gap_links_and_earlier_probes_are_compared_with_later_regular_filings():
    members = [
        {
            "isin": "A",
            "symbol": "OLD",
            "from_date": "2020-01-01",
            "to_date": "2020-12-31",
            "source_url": "official",
        }
    ]
    probe = {
        "isin": "A",
        "period_end": "2020-03-31",
        "basis": "standalone",
        "first_notice_at": "2020-05-08T14:00:00+05:30",
        "first_notice_url": "https://nsearchives.nseindia.com/first.pdf",
        "gap": "first_release_attachment_unavailable",
    }
    orig, positions, *_ = measure(members, SESSIONS, [ROW], [probe])
    assert not orig and not positions[0]["covered"]
    assert positions[0]["candidate_notice_url"] == probe["first_notice_url"]
    assert {"attachment_unavailable", "unverified_original_or_timing"} == set(
        positions[0]["gap_groups"].split("|")
    )
    assert (
        measure(members, SESSIONS, [])[1][0]["gap_groups"] == "no_attributed_formal_or_board_record"
    )


def test_staggered_first_fields_respect_earlier_summary_instead_of_backdating_eps_or_coverage():
    early = ROW["announced_at"]
    eps_time = "2020-05-11T14:25:00+05:30"
    last = "2020-05-12T16:05:00+05:30"
    atomic = dict(
        ROW,
        id="linked-entry-1",
        announced_at=eps_time,
        initial_disclosure_at=early,
        fact_announced_at={
            "net_profit": early,
            "eps": eps_time,
            "revenue": eps_time,
            "operating_profit": last,
        },
        complete_available_at=last,
    )
    summary = dict(
        ROW,
        first_reported=False,
        filing_status="initial_summary",
        original_record="linked-entry-1",
        original_fields=["net_profit"],
        revenue=None,
        eps=None,
        operating_profit=None,
    )
    members = [
        {
            "isin": "A",
            "symbol": "OLD",
            "from_date": "2019-12-30",
            "to_date": "2026-12-31",
            "source_url": "exchange",
        }
    ]
    originals, quarters, *_ = measure(members, SESSIONS, [summary, atomic])
    assert len(originals) == 1 and originals[0]["usable_session"] == "2020-05-11"
    assert originals[0]["fact_usable_sessions"] == {
        "net_profit": "2020-05-08",
        "eps": "2020-05-11",
        "revenue": "2020-05-11",
        "operating_profit": None,
    }
    assert (
        quarters[0]["known_eps"]
        and not quarters[0]["covered"]
        and not quarters[0]["known_operating_profit"]
    )
    _, later, *_ = measure(members, [*SESSIONS, "2020-05-13"], [summary, atomic])
    assert later[0]["complete_usable_session"] == "2020-05-13" and later[0]["covered"]
    assert later[0]["eps_announced_at"] == eps_time and later[0]["net_profit_announced_at"] == early
    assert later[0]["operating_profit_announced_at"] == last
    assert later[0]["eps_basis"] == "standalone" and later[0]["eps_source_url"] == ROW["source_url"]
    unproven = dict(summary, filing_status="uncertified")
    assert not reconcile([unproven, atomic], SESSIONS)[0]
    assert not reconcile([dict(summary, original_record="other-edition"), atomic], SESSIONS)[0]
    assert not reconcile([dict(summary, net_profit=7), atomic], SESSIONS)[0]
    intervening = dict(
        summary, announced_at="2020-05-10T14:02:00+05:30", filing_status="uncertified"
    )
    assert not reconcile([summary, intervening, atomic], SESSIONS)[0]


def test_storage_format_diagnostic_counts_stock_quarters_once_per_format():
    from thesistally.fundamental_coverage import by_filing_format

    position = {"isin": "A", "period_end": "2020-03-31", "covered": True, "gap_groups": ""}
    xml = dict(
        ROW,
        id="legacy/abc",
        structured_url="https://archives.nseindia.com/a.xml",
        complete_usable_session="2020-05-08",
    )
    notice = dict(ROW, id="notice/abc", structured_url=None)
    missing = dict(
        position, isin="B", covered=False, gap_groups="no_attributed_formal_or_board_record"
    )
    rows, totals = by_filing_format([position, missing], [xml, xml, notice], [xml])
    assert len(rows) == 3
    assert totals["legacy XBRL"]["eligible_with_format"] == 1
    assert totals["legacy XBRL"]["certified_original_in_this_format"] == 1
    assert totals["board notice only"]["covered_any_format"] == 1
    assert totals["board notice only"]["certified_original_in_this_format"] == 0
    assert totals["no attributed record"]["eligible_with_format"] == 1


def test_pin_acquisition_census_and_refuse_an_unattested_complete_flag(tmp_path):
    import gzip
    import hashlib
    import json

    import pytest

    from thesistally.fundamental_coverage import checkpoint_provenance

    raw_ledger = "a" * 64
    (tmp_path / "filings-receipt.json").write_bytes(b"original")
    counts = {"complete": False, "planned_urls": 7, "by_status": {"pending": 2, "archived": 5}}
    raw = json.dumps({"filings_sha256": raw_ledger, "notice_acquisition": counts}).encode()
    digest = hashlib.sha256(raw).hexdigest()
    sub = tmp_path / "checkpoints" / raw_ledger / digest
    sub.mkdir(parents=True)
    (sub / "source-index.json.gz").write_bytes(gzip.compress(raw))
    receipt = {
        "filings_sha256": raw_ledger,
        "filings_receipt_sha256": hashlib.sha256(b"original").hexdigest(),
        "source_census_sha256": digest,
        "source_acquisition": counts,
    }
    path = sub / "index.json"
    path.write_text(json.dumps(receipt))
    result = checkpoint_provenance(tmp_path, raw_ledger)
    assert result["source_census_sha256"] == digest and result["source_acquisition"] == counts
    receipt["source_acquisition"]["complete"] = True
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="immutable filing or acquisition evidence"):
        checkpoint_provenance(tmp_path, raw_ledger)
