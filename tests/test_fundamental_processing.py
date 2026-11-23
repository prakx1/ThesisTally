import gzip
import json

import pytest

from thesistally import fundamental_processing as processing
from thesistally.discovery_registry import digest, encoded
from thesistally.fundamental_ocr import VERSION, capsule


def test_full_legacy_receipt_requires_unsampled_worklist_of_matching_edition(tmp_path, monkeypatch):
    monkeypatch.setattr(
        processing,
        "requests",
        lambda root, sample: (
            ("ledger", []) if sample is None else pytest.fail("sampled source plan")
        ),
    )
    key, _ = processing.freeze_jobs(tmp_path, "ledger", [], None)
    with pytest.raises(ValueError, match="pending"):
        processing.full_legacy_check(tmp_path, "ledger")
    completed = tmp_path / "legacy-optical-completions" / (key + ".json")
    completed.parent.mkdir()
    completed.write_bytes(encoded({"plan": key, "ledger": "ledger", "attachments_checked": 1}))
    with pytest.raises(ValueError, match="census differs"):
        processing.full_legacy_check(tmp_path, "ledger")
    completed.write_bytes(encoded({"plan": key, "ledger": "ledger", "attachments_checked": 0}))
    assert processing.full_legacy_check(tmp_path, "ledger")["plan"] == key
    with pytest.raises(ValueError, match="numeric edition"):
        processing.full_legacy_check(tmp_path, "different")


def test_scan_seal_requires_each_expected_original_page_or_documented_error(tmp_path):
    folder = tmp_path / "notice-text"
    folder.mkdir()
    file = folder / "source.json.gz"
    pdf = digest(b"abc")
    file.write_bytes(gzip.compress(encoded({"pdfs": [{"pdf_sha256": pdf, "pages": ["", "", ""]}]})))
    receipt = capsule(tmp_path, pdf)
    native = {
        "native": [str(file.relative_to(tmp_path))],
        "ocr": [str(receipt.relative_to(tmp_path))],
    }
    with pytest.raises(ValueError, match="pending"):
        processing.scanned_pages(tmp_path, native)
    errors = tmp_path / "notice-ocr/issues"
    errors.mkdir(parents=True)
    (errors / "issue.json").write_bytes(
        encoded({"issues": [{"input": file.name, "reason": "OCR failure for " + pdf}]})
    )
    assert processing.scanned_pages(tmp_path, native)["unreadable"][0]["pages"] == [1, 2]
    receipt.parent.mkdir(parents=True)
    record = {"pdf_sha256": pdf, "method": VERSION, "pages": {"1": "text", "2": "text"}}
    receipt.write_bytes(gzip.compress(json.dumps(record).encode()))
    assert processing.scanned_pages(tmp_path, native)["scanned_documents"] == 1
    assert not processing.scanned_pages(tmp_path, native)["unreadable"]
    del record["pages"]["1"]
    receipt.write_bytes(gzip.compress(json.dumps(record).encode()))
    with pytest.raises(ValueError, match="omits an original"):
        processing.scanned_pages(tmp_path, native)


def test_numerical_edition_advances_only_with_new_original_evidence(tmp_path):
    from thesistally.fundamental_finish import new_witnesses

    frozen = encoded({"legacy_original_optics": [], "rendered_eps": [], "rendered_money": []})
    key = digest(frozen)
    folder = tmp_path / "ledger-inputs"
    folder.mkdir()
    (folder / (key + ".json.gz")).write_bytes(gzip.compress(frozen))
    receipt = {"frozen_ledger_inputs_sha256": key}
    assert not any(new_witnesses(tmp_path, receipt).values())
    source = tmp_path / "notice-rendered-money/v2/aa/test.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}")
    assert new_witnesses(tmp_path, receipt)["rendered_money"] == 1


def test_offline_handoff_rebuilds_after_new_witness_before_any_trade_calculation(
    tmp_path, monkeypatch
):
    from thesistally import fundamental_finish as runner

    steps = []
    source = tmp_path / "nse"
    source.mkdir()
    with pytest.raises(ValueError, match="has not handed off"):
        runner.finish(tmp_path, workers=1)
    census = encoded({"notice_acquisition": {"complete": True}})
    (source / "downloads.gz").write_bytes(gzip.compress(census))
    (source / "source-downloads-terminal.json").write_bytes(
        encoded(
            {
                "network_only": True,
                "source_census_file": "downloads.gz",
                "source_census_sha256": digest(census),
            }
        )
    )

    def source_step(name):
        steps.append(name)
        if name == "ledger":
            version = str(steps.count("ledger"))
            (source / "filings-receipt.json").write_bytes(
                encoded({"sha256": version, "frozen_ledger_inputs_sha256": version})
            )

    def offline_step(name, *args):
        operation = args[0] if name == "results_worker" else name
        steps.append(operation)
        if operation == "coverage":
            (tmp_path / "coverage.json").write_bytes(encoded("coverage"))
        if operation == "signals":
            (tmp_path / "signals-receipt.json").write_bytes(encoded("signals"))

        if operation == "fundamental_processing":
            coverage_sha = digest((tmp_path / "coverage.json").read_bytes())
            receipt = tmp_path / "processing-history" / (coverage_sha + ".json")
            receipt.parent.mkdir()
            receipt.write_bytes(encoded("reviewed"))

    monkeypatch.setattr(runner, "source", source_step)
    monkeypatch.setattr(runner, "step", offline_step)
    monkeypatch.setattr(runner, "new_witnesses", lambda _, r: {"money": int(r["sha256"] == "1")})
    runner.finish(tmp_path, workers=1)
    assert steps.count("ledger") == steps.count("fundamental_money_build") == 2
    assert steps[-6:] == [
        "manifest",
        "checkpoint",
        "coverage",
        "fundamental_processing",
        "signals",
        "replay",
    ]
    assert len(list((tmp_path / "completed-studies").glob("*.json"))) == 1


def test_final_worker_waits_for_the_named_original_statement_plan(tmp_path):
    from thesistally.fundamental_finish import completed_modern_sources

    (tmp_path / "terminal-xbrl-scope.json").write_bytes(
        encoded({"cohorts": {"study": "wanted"}, "evaluator": "method"})
    )
    folder = tmp_path / "xbrl-optical-completions/study"
    folder.mkdir(parents=True)
    path = folder / "edition.json"
    for plan, method in (("stale", "method"), ("wanted", "stale")):
        path.write_bytes(encoded({"edition_sha256": "edition", "plan": plan, "evaluator": method}))
        assert not completed_modern_sources(tmp_path)
    path.write_bytes(
        encoded({"edition_sha256": "edition", "plan": "wanted", "evaluator": "method"})
    )
    assert completed_modern_sources(tmp_path)
