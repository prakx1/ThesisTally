"""Publish Strategy D's two book verdicts with their immutable replay evidence."""

import fcntl
import gzip
import json
import os
import sys
from pathlib import Path

from .discovery_guard import REPO, confined, output_root
from .discovery_registry import digest, encoded, families, proposed, write_once

FAMILY = "results-d-v1"


def withdrawal(root, coverage_hash):
    path = root / "review/withdrawals" / (coverage_hash + ".json")
    if path.is_file():
        record = json.loads(path.read_bytes())
        if record["coverage_sha256"] != coverage_hash:
            raise ValueError("Withdrawal refers to a different coverage release")
        return record
    return None


def require_completed_acquisition(root, coverage_hash):
    """Final register events refer to an edition whose official source collection finished."""
    raw = (root / "coverage-history" / (coverage_hash + ".json")).read_bytes()
    if digest(raw) != coverage_hash:
        raise ValueError("Coverage edition changed before registration")
    nse = json.loads(raw)["filing_sources"]["nse"]
    if not nse.get("source_acquisition", {}).get("complete"):
        raise ValueError("Interim acquisition: original announcement collection is incomplete")
    census_hash = nse["source_census_sha256"]
    folder = root / "nse/checkpoints" / nse["sha256"] / census_hash
    census_bytes = gzip.decompress((folder / "source-index.json.gz").read_bytes())
    if digest(census_bytes) != census_hash:
        raise ValueError("Official source census differs from the coverage edition")
    census = json.loads(census_bytes)
    if (
        census["notice_acquisition"] != nse["source_acquisition"]
        or census["filings_sha256"] != nse["sha256"]
    ):
        raise ValueError("Coverage and attachment census refer to different filings")
    states = census.get("structured_acquisition", {})
    from .fundamental_manifest import acquisition_terminal

    if (
        not states
        or not census.get("structured_acquisition_complete")
        or not acquisition_terminal(states)
    ):
        raise ValueError("Interim acquisition: structured results collection is incomplete")
    return nse


def check_modern_publication_scope(root, stages):
    path = root / "nse/terminal-xbrl-scope.json"
    if not path.exists():
        return
    raw = path.read_bytes()
    scope = json.loads(raw)
    review = stages.get("modern_originals", {})
    evidence = review.get("cohorts", {})
    required = {"independent-earlier-release", "modern-statement-pages"}
    if (
        review.get("scope_sha256") != digest(raw)
        or review.get("reference_filing_edition") != scope["source_edition"]
        or set(scope["cohorts"]) != required
        or set(evidence) != required
    ):
        raise ValueError(
            "The final source receipt omits the signed modern original statement scope"
        )
    for name in required:
        item = evidence[name]
        edition = item["edition"]
        if len(edition) != 64 or any(c not in "0123456789abcdef" for c in edition):
            raise ValueError("Invalid completed modern source edition")
        marker = root / "nse/xbrl-optical-completions" / name / (edition + ".json")
        registered = marker.read_bytes()
        completion = json.loads(registered)
        if (
            digest(registered) != item["marker_sha256"]
            or item["plan"] != scope["cohorts"][name]
            or completion["plan"] != item["plan"]
            or completion["edition_sha256"] != edition
            or completion["evaluator"] != scope["evaluator"]
        ):
            raise ValueError("Completed modern statement evidence differs from the signed plans")


def processing_evidence(root, coverage_hash, nse):
    path = root / "processing-history" / (coverage_hash + ".json")
    if not path.exists():
        raise ValueError("Original document review is still pending for this coverage edition")
    raw = path.read_bytes()
    stages = json.loads(raw)
    if (
        stages["coverage_sha256"] != coverage_hash
        or stages["source_census_sha256"] != nse["source_census_sha256"]
        or stages["filings_sha256"] != nse["sha256"]
        or stages["legacy"]["ledger"] != nse["sha256"]
        or not all(k in stages for k in ("original_fields", "scanned_pages", "bse_fallback"))
    ):
        raise ValueError("Source processing receipt refers to another edition or omits a source")
    check_modern_publication_scope(root, stages)
    return {"path": str(path.relative_to(root.parent.parent)), "sha256": digest(raw)}


def publish(report_path):
    path = Path(report_path).resolve(strict=True)
    if path.parent.parent != REPO / "data/fundamentals/replays" or path.name != "report.json":
        raise ValueError("Expected a content-addressed fundamentals replay")
    raw = path.read_bytes()
    report = json.loads(raw)
    family = families()[FAMILY]
    if (
        path.parent.name != report["identity"][:20]
        or digest(json.dumps(report["proof"], sort_keys=True).encode()) != report["identity"]
        or not family["registration_commit"].startswith(report["proof"]["preregistration_commit"])
    ):
        raise ValueError("Replay identity or registration does not match")
    if withdrawn := withdrawal(path.parent.parent.parent, report["proof"]["coverage"]):
        raise ValueError(f"Withdrawn coverage release: {withdrawn['reason']}")
    data = path.parent.parent.parent
    coverage_hash = report["proof"]["coverage"]
    nse = require_completed_acquisition(data, coverage_hash)
    processing = processing_evidence(data, coverage_hash, nse)
    root = REPO / "data/discovery"
    proposed(root, family)
    for card in family["hypotheses"]:
        books = {}
        for book in ("funded", "equal_risk"):
            key = f"D{card['holding_sessions']}-{book}"
            q = report["qualification"][key]
            books[book] = {"qualified": q["qualified"], "verdict": q["verdict"]}
        if any(q["qualified"] for q in books.values()):
            status = "passed"
        elif all(q["verdict"] == "not enough data" for q in books.values()):
            status = "proposed"
        else:
            status = "failed"
        event = {
            "id": card["id"],
            "family": FAMILY,
            "status": status,
            "evaluation": "insufficient data" if status == "proposed" else "evaluated",
            "books": books,
            "source_processing": processing,
            "filing_coverage_eligible_years": report["eligible_years"],
            "evidence": path.relative_to(REPO).as_posix(),
            "evidence_sha256": digest(raw),
            "registration_commit": family["registration_commit"],
            "registration_sha256": family["registration_sha256"],
        }
        directory = root / "register" / card["id"]
        with (directory / ".study.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            prior = sorted(directory.glob("*-study.json"))
            if not any(p.read_bytes() == encoded(event) for p in prior):
                write_once(directory / f"02-{len(prior) + 1:06d}-study.json", encoded(event))
    return report["identity"]


def recorded(card):
    root = REPO / "data/discovery/register" / card
    events = sorted(root.glob("*-study.json"))
    if not events:
        raise ValueError("Registered D study has no published replay yet")
    result = json.loads(events[-1].read_bytes())
    evidence = (REPO / result["evidence"]).read_bytes()
    if result["id"] != card or digest(evidence) != result["evidence_sha256"]:
        raise ValueError("Strategy D evidence changed after publication")
    processing = result.get("source_processing")
    if processing and digest((REPO / processing["path"]).read_bytes()) != processing["sha256"]:
        raise ValueError("Source processing evidence changed after publication")
    if withdrawn := withdrawal(
        REPO / "data/fundamentals", json.loads(evidence)["proof"]["coverage"]
    ):
        return {
            **result,
            "evaluation": "evidence withdrawn",
            "status": "proposed",
            "withdrawal": withdrawn,
        }
    return result


if __name__ == "__main__":
    if not (REPO / ".git").is_file() or len(sys.argv) != 2:
        raise SystemExit("Run from the research worktree with the path to report.json")
    root = output_root()
    args = [
        sys.executable,
        "-B",
        "-c",
        "from thesistally.results_registry import publish; import sys; print(publish(sys.argv[1]))",
        sys.argv[1],
    ]
    result = confined(
        args,
        root,
        env={**os.environ, "PYTHONPATH": str(REPO / "src"), "TMPDIR": str(root)},
        check=False,
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    sys.exit(result.returncode)
