import json

import pytest

from thesistally import fundamental_legacy_optics as optics
from thesistally import fundamental_legacy_raster
from thesistally.discovery_registry import encoded, write_once


def test_legacy_receipt_reproduces_each_input_page_and_never_uses_an_unverified_later_field(
    tmp_path, monkeypatch
):
    filing = {
        "isin": "test",
        "basis": "standalone",
        "period_end": "2014-03-31",
        "source_url": "structured.html",
        "revenue": 1_000.0,
        "operating_profit": 300,
        "net_profit": 250,
        "eps": 2.5,
    }
    package = {
        "sha256": "archive",
        "source_url": "exchange/original.zip",
        "pdfs": [{"pdf_sha256": "original", "pages": ["one", "two", ""]}],
    }
    figures = [{"revenue": 1000.0}, {"eps": 2.5}]

    def recognize(_filing, boxes, *, image_bytes):
        index = int(image_bytes.decode())
        return {
            "values": figures[index],
            "cells": boxes,
            "basis": "standalone",
            "period_end": "2014-03-31",
        }

    monkeypatch.setattr(fundamental_legacy_raster, "observe", recognize)
    info = optics.inputs(filing, package)
    path = optics.path_for(tmp_path, info)
    pages = []
    for i in range(2):
        boxes = encoded([i])
        png = str(i).encode()
        proof = recognize(filing, json.loads(boxes), image_bytes=png)
        pages.append(optics.save_page(tmp_path, i + 1, 2400, "original", png, boxes, proof, filing))
    write_once(
        path,
        encoded({"inputs": info, "extractor_sha256": optics.extractor_version(), "pages": pages}),
    )
    assert optics.corroborate(tmp_path, filing, package, available=set()) is None
    certified = optics.corroborate(tmp_path, filing, package, available={path.stem})
    assert certified["values"] == {
        "revenue": 1000.0,
        "operating_profit": None,
        "net_profit": None,
        "eps": 2.5,
    }
    assert certified["field_page_evidence"]["revenue"]["page"] == 1
    assert certified["field_page_evidence"]["eps"]["page"] == 2
    with pytest.raises(ValueError, match="differs"):
        optics.original_values(filing, {**proof, "values": {"eps": 25}})
    with pytest.raises(ValueError, match="reporting basis"):
        optics.original_values(filing, {**proof, "basis": "consolidated"})
    assert optics.corroborate(tmp_path, {**filing, "eps": 25}, package) is None


def test_optical_sweep_resumes_exact_archived_input_or_rejects_changed_bytes(tmp_path):
    from thesistally.fundamental_legacy_optics_build import check, freeze_jobs, load_plan
    from thesistally.fundamental_media_recovery import package_path

    url = "https://exchange.example/original.pdf"
    path = package_path(tmp_path, url)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"archived package bytes")
    key, plan = freeze_jobs(tmp_path, "source-sha", [(url, [{"id": "quarter"}])], 1)
    assert load_plan(tmp_path, key) == plan
    path.write_bytes(b"different bytes")
    with pytest.raises(ValueError, match="package changed"):
        check(tmp_path, None, {}, plan["tasks"][0])


def test_reusing_legacy_optical_attempt_checks_input_and_unreadable_pages(tmp_path):
    from thesistally.fundamental_legacy_optics_build import confirmed_attempt

    attempt = tmp_path / "attempt.json"
    positive = tmp_path / "positive.json"
    info = {"notice_url": "official", "source_members": [["abc", 2]]}
    counts = {
        "basis_quarters_checked": 1,
        "with_verified_fields": 0,
        "basis_quarters_with_unreadable_pages": 1,
    }
    gap = {"source_url": "official", "pdf_sha256": "abc", "page": 1}
    record = {
        "inputs": info,
        "counts": counts,
        "physical_pages_checked": 0,
        "unreadable_pages": [gap],
    }
    attempt.write_bytes(encoded(record))
    assert confirmed_attempt(attempt, info, positive, [("abc", 1)]) == counts
    with pytest.raises(ValueError, match="different inputs"):
        confirmed_attempt(attempt, {**info, "notice_url": "wrong"}, positive, [("abc", 1)])
    attempt.write_bytes(encoded({**record, "unreadable_pages": []}))
    with pytest.raises(ValueError, match="unreadable-page"):
        confirmed_attempt(attempt, info, positive, [("abc", 1)])
    attempt.write_bytes(encoded({**record, "unreadable_pages": [{**gap, "page": 3}]}))
    with pytest.raises(ValueError, match="source identity"):
        confirmed_attempt(attempt, info, positive, [("abc", 1)])


def test_final_record_accounts_for_every_physical_page_and_duplicate_archive_member(tmp_path):
    from thesistally.fundamental_legacy_optics_build import confirmed_attempt, physical_members

    docs = {
        "pdfs": [
            {"pdf_sha256": "a", "pages": ["a", "b", "c", "d", ""]},
            {"pdf_sha256": "b", "pages": ["1"]},
            {"pdf_sha256": "b", "pages": ["1"]},
        ]
    }
    members = physical_members(docs)
    assert members == [("a", 4), ("b", 1), ("b", 1)]
    info = {"notice_url": "url", "source_members": [["a", 5], ["b", 1], ["b", 1]]}
    positive, attempt = tmp_path / "positive", tmp_path / "attempt"
    counts = {"basis_quarters_checked": 1, "with_verified_fields": 0}
    body = {"inputs": info, "counts": counts, "physical_pages_checked": 6, "unreadable_pages": []}
    attempt.write_bytes(encoded(body))
    assert confirmed_attempt(attempt, info, positive, members) == counts
    for n in (0, 5, 7):
        attempt.write_bytes(encoded({**body, "physical_pages_checked": n}))
        with pytest.raises(ValueError, match="page counts"):
            confirmed_attempt(attempt, info, positive, members)
    gap = {"source_url": "url", "pdf_sha256": "b", "page": 1}
    counts["basis_quarters_with_unreadable_pages"] = 1
    for gaps in (
        [gap, gap],
        [{**gap, "pdf_sha256": "a", "page": 5}],
        [{**gap, "source_occurrence": 3}],
    ):
        attempt.write_bytes(
            encoded({**body, "physical_pages_checked": 6 - len(gaps), "unreadable_pages": gaps})
        )
        with pytest.raises(ValueError, match="page counts"):
            confirmed_attempt(attempt, info, positive, members)
    attempt.write_bytes(
        encoded(
            {
                **body,
                "physical_pages_checked": 4,
                "unreadable_pages": [gap, {**gap, "source_occurrence": 2}],
            }
        )
    )
    assert confirmed_attempt(attempt, info, positive, members) == counts
