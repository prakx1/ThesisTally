"""Read-only, conditional ceiling for remaining local extraction work on one filing edition."""

import argparse
import gzip
import json
from collections import defaultdict
from pathlib import Path

from .discovery_guard import REPO
from .discovery_registry import digest, encoded
from .fundamental_coverage_report import product_csv
from .fundamental_legacy_optics_build import requests
from .fundamental_processing import packed, scanned_pages
from .results_registry import require_completed_acquisition, withdrawal


def recovery_counts(windows, quarters, html, quarantine):
    """All possible outstanding reads succeed in time. Each eligible pair counts once."""
    groups = defaultdict(dict)
    all_pairs = set()
    for row in quarters:
        key = row["isin"], row["period_end"]
        if key in all_pairs:
            raise ValueError("Duplicate eligible ISIN-quarter")
        all_pairs.add(key)
        year = str(row["filing_season_year"])
        if year not in windows:
            continue
        if row["covered_by_window_end"] not in ("True", "False"):
            raise ValueError("Missing as-of-window coverage flag")
        groups[year][key] = row["covered_by_window_end"] == "True"

    result = {}
    for year, w in windows.items():
        pairs = groups[year]
        if {p[1] for p in pairs} != set(w["periods"]):
            raise ValueError("Quarter cohorts differ from the filing-season window")
        covered = {key for key, certified in pairs.items() if certified}
        if (len(pairs), len(covered)) != (w["eligible"], w["covered"]):
            raise ValueError("Quarter-level records differ from the signed window")
        missing = pairs.keys() - covered
        legacy, original = missing & html, missing & quarantine
        potential = legacy | original
        result[year] = {
            "eligible": len(pairs),
            "observed_complete_by_window_end": len(covered),
            "uncovered_with_legacy_work": len(legacy),
            "uncovered_with_quarantined_field_work": len(original),
            "uncovered_in_both_queues": len(legacy & original),
            "additional_unique_pairs_if_all_succeed": len(potential),
            "fixed_nse_work_ceiling": len(covered) + len(potential),
            "fixed_nse_work_ceiling_fraction": (len(covered) + len(potential)) / len(pairs),
            # The historical BSE catalogue could not establish a bound on the identities
            # or periods of missing first publications. An unknown clock can change
            # coverage even for a stock with a later verified NSE publication.
            "additional_uncertified_bse_pairs_possible_beyond_nse_ceiling": len(
                missing - potential
            ),
            "both_exchanges_ceiling_with_unbounded_bse": len(pairs),
        }
    return result


def diagnostic(data, coverage_id):
    """Verify source closure and worklist identities; print data only, without mutation."""
    nse = require_completed_acquisition(data, coverage_id)
    if (
        withdrawal(data, coverage_id)
        or (data / "nse/withdrawals" / (nse["sha256"] + ".json")).is_file()
    ):
        raise ValueError("Withdrawn coverage or underlying numerical source edition")
    coverage = json.loads((data / "coverage-history" / (coverage_id + ".json")).read_bytes())
    root = data / "nse"
    folder = root / "checkpoints" / nse["sha256"] / nse["source_census_sha256"]
    ix = (folder / "index.json").read_bytes()
    if digest(ix) != nse["checkpoint_index_sha256"]:
        raise ValueError("Coverage source checkpoint changed")
    index = json.loads(ix)
    receipt_bytes = gzip.decompress((folder / "filings-receipt.json.gz").read_bytes())
    if digest(receipt_bytes) != index["filings_receipt_sha256"]:
        raise ValueError("Original numerical source receipt changed")
    source = json.loads(receipt_bytes)
    if source["sha256"] != nse["sha256"] or source["field_reviews"] != index["field_reviews"]:
        raise ValueError("Source and quarantined-field receipts refer to different editions")
    if (root / "filings-receipt.json").read_bytes() != receipt_bytes:
        raise ValueError(
            "The installed unsampled-worklist generator has a different numerical edition"
        )
    for name, expected in {
        "attachment-plan.json": source["attachments_plan_sha256"],
        "dated-alias-attachment-plan.json": source["dated_alias_corpus_sha256"][
            "dated-alias-attachment-plan.json"
        ],
    }.items():
        if digest((root / name).read_bytes()) != expected:
            raise ValueError("Legacy worklist uses a different structured acquisition plan")
    frozen_sha = source["frozen_ledger_inputs_sha256"]
    frozen = packed(root / "ledger-inputs" / (frozen_sha + ".json.gz"), frozen_sha)
    # This existing check reads stored generic OCR receipts. It does not render,
    # recognize, fetch or mark any original packet as complete.
    generic_evidence = scanned_pages(root, frozen)
    source_id, jobs = requests(root, None)
    if (
        source_id != source["sha256"]
        or (root / "filings-receipt.json").read_bytes() != receipt_bytes
    ):
        raise ValueError("Ledger moved while enumerating all eligible original HTML reads")
    reviews = source["field_reviews"]
    rejected = packed(root / reviews["file"], reviews["sha256"])
    if len(rejected) != reviews["observations"]:
        raise ValueError("Quarantined first-field record count differs from source")
    html = {(f["isin"], f["period_end"]) for _, group in jobs for f in group}
    fields = {(f["isin"], f["period_end"]) for f in rejected}
    rows = product_csv(data, coverage, "quarter-coverage.csv")
    return {
        "interpretation": "Optimistic arithmetic for this signed source edition and these two remaining worklists only. Every touched stock-quarter is assumed fully recoverable before its window closed. This does not establish actual coverage, tradeability, strategy qualification or a reason to stop collection.",
        "bse_limitation": "With no exhaustive historical BSE catalogue, uncertified BSE releases can apply to any unresolved eligible stock-quarter. A narrower both-exchange upper bound cannot be established.",
        "coverage_sha256": coverage_id,
        "nse_filings_sha256": source_id,
        "source_census_sha256": nse["source_census_sha256"],
        "frozen_ledger_inputs_sha256": frozen_sha,
        "generic_page_audit_sha256": digest(encoded(generic_evidence)),
        "legacy_worklist_sha256": digest(encoded(jobs)),
        "diagnostic_program_sha256": digest(Path(__file__).read_bytes()),
        "legacy_generator_sha256": digest(
            Path(__file__).with_name("fundamental_legacy_optics_build.py").read_bytes()
        ),
        "legacy_source_packets": len(jobs),
        "quarantined_field_rows_sha256": reviews["sha256"],
        "windows": recovery_counts(coverage["windows"], rows, html, fields),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "coverage_sha256",
        help="Immutable coverage-history SHA-256 for the completed public acquisition",
    )
    args = parser.parse_args()
    if len(args.coverage_sha256) != 64 or any(
        c not in "0123456789abcdef" for c in args.coverage_sha256
    ):
        raise ValueError("Invalid coverage receipt hash")
    try:
        result = diagnostic(REPO / "data/fundamentals", args.coverage_sha256)
    except (ValueError, FileNotFoundError) as problem:
        parser.exit(2, f"{problem}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
