"""Finish an isolated results-study acquisition offline, resuming its immutable evidence."""

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time

from .discovery_guard import REPO, output_root
from .discovery_registry import digest, encoded, write_once
from .fundamental_legacy_optics import extractor_version
from .fundamental_processing import packed
from .fundamental_sources import run as source


def new_witnesses(root, receipt):
    key = receipt["frozen_ledger_inputs_sha256"]
    frozen = packed(root / "ledger-inputs" / (key + ".json.gz"), key)
    directories = {
        "rendered_eps": "notice-rendered-eps/v1",
        "rendered_money": "notice-rendered-money/v2",
        "legacy_original_optics": "legacy-original-receipts/" + extractor_version(),
    }
    changes = {}
    for name, folder in directories.items():
        available = {str(p.relative_to(root)) for p in (root / folder).glob("*/*.json")}
        previous = set(frozen[name])
        if not previous.issubset(available):
            raise ValueError("Previously archived original evidence is missing")
        changes[name] = len(available - previous)
    if (root / "xbrl-optical-completions").is_dir() or "reviewed_xbrl_optical_editions" in frozen:
        from .fundamental_xbrl_optics import freeze

        _, paths = freeze(root)
        previous = set(frozen.get("reviewed_xbrl_optical_editions", []))
        if not previous.issubset(paths):
            raise ValueError("Rebuild the numerical ledger with the current optical evaluator")
        changes["reviewed_xbrl_optical_editions"] = len(set(paths) - previous)
    return changes


def step(module, *args):
    print("Starting", module, *args, flush=True)
    subprocess.run(
        [sys.executable, "-B", "-m", "thesistally." + module, *args],
        cwd=REPO,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=True,
    )


def finish(data, *, workers):
    root = data / "nse"
    marker = root / "source-downloads-terminal.json"
    if not marker.exists():
        raise ValueError(
            "The official-source downloader has not handed off the finished acquisition"
        )
    handover = marker.read_bytes()
    transfer = json.loads(handover)
    if transfer.get("network_only") is not True:
        raise ValueError("Expected the downloader's network-only handoff")
    census_path = (root / transfer["source_census_file"]).resolve(strict=True)
    if not census_path.is_relative_to(root.resolve()):
        raise ValueError("Source census lies outside the official acquisition")
    packed(census_path, transfer["source_census_sha256"])
    # An in-flight OCR watcher will finish its final scan and exit; the explicit
    # offline pass below covers newly recovered ZIP members as well.
    (data / "ocr-build").mkdir(exist_ok=True)
    (data / "ocr-build/downloads-finished").touch()
    step("fundamental_ocr", "--workers", str(workers))
    seen = set()
    while True:
        source("ledger")
        receipt = json.loads((root / "filings-receipt.json").read_bytes())
        start = receipt["frozen_ledger_inputs_sha256"]
        if start in seen:
            raise ValueError("Original verification did not advance the frozen evidence set")
        seen.add(start)
        step("fundamental_rendered_build")
        step("fundamental_money_build")
        step("fundamental_legacy_optics_build", "--workers", str(workers))
        added = new_witnesses(root, receipt)
        print("New original evidence since numeric edition", receipt["sha256"], added, flush=True)
        # Additional official facts can expose new statements or disputed cells.
        # Never use newly found pages in a ledger that predates them.
        if not any(added.values()):
            break
    if marker.read_bytes() != handover:
        raise ValueError("The underlying download handoff changed during optical review")
    source("manifest")
    source("checkpoint")
    step("results_worker", "coverage")
    step("fundamental_processing")
    coverage_sha = digest((data / "coverage.json").read_bytes())
    processing = data / "processing-history" / (coverage_sha + ".json")
    step("results_worker", "signals")
    step("results_worker", "replay")
    evidence = {
        "download_handoff_sha256": digest(handover),
        "processing": str(processing.relative_to(data)),
        "processing_sha256": digest(processing.read_bytes()),
        "coverage_sha256": coverage_sha,
        "signal_receipt_sha256": digest((data / "signals-receipt.json").read_bytes()),
    }
    write_once(
        data / "completed-studies" / (digest(encoded(evidence)) + ".json"), encoded(evidence)
    )
    print("Ready for independent review", json.dumps(evidence, sort_keys=True), flush=True)


def completed_modern_sources(root):
    """Allow a successor process to wait for exactly the two scoped data jobs."""
    scope = json.loads((root / "terminal-xbrl-scope.json").read_bytes())
    completed = set()
    for name, plan in scope["cohorts"].items():
        for file in (root / "xbrl-optical-completions" / name).glob("*.json"):
            marker = json.loads(file.read_bytes())
            if (marker["plan"], marker["evaluator"]) == (plan, scope["evaluator"]):
                if marker["edition_sha256"] != file.stem:
                    raise ValueError("Modern source marker points to a different edition")
                completed.add(name)
    return completed == set(scope["cohorts"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-for-downloads", action="store_true")
    parser.add_argument(
        "--wait-for-modern-sources",
        action="store_true",
        help="Wait for the signed modern source plans and any active original-page pass",
    )
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if args.workers < 1 or not (REPO / ".git").is_file():
        raise ValueError("Run from the isolated worktree with positive worker count")
    data = output_root(workstream="fundamentals")
    if args.wait_for_modern_sources:
        print("Waiting for the signed modern statement checks", flush=True)
        while not completed_modern_sources(data / "nse"):
            time.sleep(45)
    with (data / ".finish.lock").open("a") as lock:
        fcntl.flock(
            lock, fcntl.LOCK_EX if args.wait_for_modern_sources else fcntl.LOCK_EX | fcntl.LOCK_NB
        )
        if args.wait_for_downloads:
            print("Waiting for the official source handoff", flush=True)
            while not (data / "nse/source-downloads-terminal.json").exists():
                time.sleep(45)
        finish(data, workers=args.workers)


if __name__ == "__main__":
    main()
