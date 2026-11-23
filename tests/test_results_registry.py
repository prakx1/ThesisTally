import gzip
import json

import pytest

from thesistally import results_registry as registry
from thesistally.discovery_registry import digest, encoded


def coverage_edition(root, notice_complete, structured):
    acquisition = {"complete": notice_complete}
    census = encoded(
        {
            "notice_acquisition": acquisition,
            "structured_acquisition": structured,
            "structured_acquisition_complete": True,
            "filings_sha256": "f" * 64,
        }
    )
    source_sha = digest(census)
    folder = root / "nse/checkpoints" / ("f" * 64) / source_sha
    folder.mkdir(parents=True)
    (folder / "source-index.json.gz").write_bytes(gzip.compress(census, mtime=0))
    coverage = encoded(
        {
            "filing_sources": {
                "nse": {
                    "sha256": "f" * 64,
                    "source_acquisition": acquisition,
                    "source_census_sha256": source_sha,
                }
            }
        }
    )
    directory = root / "coverage-history"
    directory.mkdir(exist_ok=True)
    (directory / (digest(coverage) + ".json")).write_bytes(coverage)
    return digest(coverage)


@pytest.mark.parametrize(
    ("notice_complete", "structured"),
    [
        (False, {"archived": 1}),
        (True, {"awaiting_healthy_network_retry": 1}),
        (True, {"pending": 1}),
        (True, {}),
    ],
)
def test_interim_source_cannot_publish_final_verdict(tmp_path, notice_complete, structured):
    coverage = coverage_edition(tmp_path, notice_complete, structured)
    with pytest.raises(ValueError, match="Interim acquisition"):
        registry.require_completed_acquisition(tmp_path, coverage)


def test_incomplete_study_keeps_proposal_and_verifies_evidence(tmp_path, monkeypatch):
    family = {
        "family": registry.FAMILY,
        "hypotheses": [{"id": "results-d40", "holding_sessions": 40}],
        "registration_commit": "bbbbbbbeee",
        "registration_sha256": "ab",
    }
    monkeypatch.setattr(registry, "REPO", tmp_path)
    monkeypatch.setattr(registry, "families", lambda: {registry.FAMILY: family})
    coverage = coverage_edition(
        tmp_path / "data/fundamentals", True, {"archived": 2, "unavailable": 1}
    )
    proof = {"preregistration_commit": "bbbbbbb", "coverage": coverage}
    identity = digest(json.dumps(proof, sort_keys=True).encode())
    path = tmp_path / "data/fundamentals/replays" / identity[:20] / "report.json"
    path.parent.mkdir(parents=True)
    report = {
        "identity": identity,
        "proof": proof,
        "eligible_years": [],
        "qualification": {
            k: {"qualified": False, "verdict": "not enough data"}
            for k in ("D40-funded", "D40-equal_risk")
        },
    }
    path.write_bytes(encoded(report))
    root = tmp_path / "data/fundamentals"
    with pytest.raises(ValueError, match="review is still pending"):
        registry.publish(path)
    nse = registry.require_completed_acquisition(root, coverage)
    processing = root / "processing-history" / (coverage + ".json")
    processing.parent.mkdir()
    processing.write_bytes(
        encoded(
            {
                "coverage_sha256": coverage,
                "source_census_sha256": nse["source_census_sha256"],
                "filings_sha256": "f" * 64,
                "legacy": {"ledger": "f" * 64},
                "original_fields": {},
                "scanned_pages": {},
                "bse_fallback": {},
            }
        )
    )
    registry.publish(path)
    registry.publish(path)
    result = registry.recorded("results-d40")
    assert result["status"] == "proposed" and result["evaluation"] == "insufficient data"
    withdrawal = tmp_path / "data/fundamentals/review/withdrawals" / (coverage + ".json")
    withdrawal.parent.mkdir(parents=True)
    withdrawal.write_bytes(encoded({"coverage_sha256": coverage, "reason": "EPS mismatch"}))
    with pytest.raises(ValueError, match="Withdrawn coverage release: EPS mismatch"):
        registry.publish(path)
    assert registry.recorded("results-d40")["evaluation"] == "evidence withdrawn"
    path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="evidence changed"):
        registry.recorded("results-d40")


def test_publishing_against_new_source_scope_needs_both_original_page_plans(tmp_path):
    root = tmp_path / "data/fundamentals"
    scope = {
        "source_edition": "post-download",
        "evaluator": "reader",
        "cohorts": {
            "independent-earlier-release": "control",
            "modern-statement-pages": "nominated",
        },
    }
    path = root / "nse/terminal-xbrl-scope.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(encoded(scope))

    def write(stages):
        file = root / "processing-history/coverage.json"
        file.parent.mkdir(exist_ok=True)
        file.write_bytes(encoded(stages))

    nse = {"sha256": "filings", "source_census_sha256": "census"}
    stages = {
        "coverage_sha256": "coverage",
        "source_census_sha256": "census",
        "filings_sha256": "filings",
        "legacy": {"ledger": "filings"},
        "original_fields": {},
        "scanned_pages": {},
        "bse_fallback": {},
    }
    write(stages)
    with pytest.raises(ValueError, match="omits the signed"):
        registry.processing_evidence(root, "coverage", nse)
    review = {
        "scope_sha256": digest(path.read_bytes()),
        "reference_filing_edition": "post-download",
        "cohorts": {},
    }
    for i, (name, plan) in enumerate(scope["cohorts"].items()):
        edition = str(i) * 64
        marker = root / "nse/xbrl-optical-completions" / name / (edition + ".json")
        marker.parent.mkdir(parents=True)
        marker.write_bytes(
            encoded({"plan": plan, "edition_sha256": edition, "evaluator": "reader"})
        )
        review["cohorts"][name] = {
            "edition": edition,
            "plan": plan,
            "marker_sha256": digest(marker.read_bytes()),
        }
    stages["modern_originals"] = review
    write(stages)
    assert registry.processing_evidence(root, "coverage", nse)["path"].endswith("coverage.json")
    review["cohorts"]["modern-statement-pages"]["plan"] = "different"
    write(stages)
    with pytest.raises(ValueError, match="differs from the signed"):
        registry.processing_evidence(root, "coverage", nse)
