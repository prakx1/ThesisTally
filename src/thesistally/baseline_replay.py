"""Stage 2 fixed-rule replay. The final forward test remains separate from this study."""

import argparse
import fcntl
import json
import sqlite3
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
from datetime import date
from pathlib import Path

from . import evaluation, market, pilot, provenance
from .baseline_funnel import stop_failures
from .journal import exclusion, journal
from .signals import load_session, rules, signal_atr14


def fixed_books():
    revisions = {name: rules(revision=name) for name in ("r2", "r1")}
    # Prepare one cross-section per signal close only when both rule files use
    # identical data, detector, stop, ranking and planned-target parameters.
    for key in ("trend", "momentum", "vcp", "filters", "exits", "ranking_model"):
        if revisions["r2"].get(key) != revisions["r1"].get(key):
            raise market.MarketError(f"Baseline cannot share different {key} parameters.")
    if any(
        cfg["risk"]["capital"] != 1_000_000 or cfg["risk"]["fraction"] != 0.005
        for cfg in revisions.values()
    ):
        raise market.MarketError("The baseline requires ₹10 lakh capital and 0.5% reference risk.")
    return [
        (f"{revision}-{book}", cfg)
        for revision, cfg in revisions.items()
        for book in ("portfolio", "equal-risk")
    ]


def training_session(args):
    root, day, cfg = args
    regime, candidates, _ = load_session(root, day, cfg)
    reasons = Counter()
    bounds = Counter()
    for c in candidates:
        if c.strategy != "high_momentum":
            continue
        reason = exclusion(c, cfg, regime) or "eligible"
        reasons[reason] += 1
        if reason == "stop_out_of_bounds":
            atr = signal_atr14(c.features, cfg)
            bounds[" + ".join(stop_failures(c.entry, c.stop, atr, cfg["filters"]))] += 1
    return str(day), {
        "detector_funnel": regime["detector_funnel"]["high_momentum"],
        "signal_dispositions": reasons,
        "stop_bounds": bounds,
    }


def training_census(root, folder, days, cfg):
    # Training has no funded trades, so never open a portfolio or create orders here.
    path = folder / "training-momentum.sqlite"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS counts(day TEXT PRIMARY KEY, data TEXT NOT NULL)")
        existing = [row[0] for row in db.execute("SELECT day FROM counts ORDER BY day")]
        if existing != [str(d) for d in days[: len(existing)]]:
            raise market.MarketError("Training signal census has a nonconsecutive prefix.")
        todo = [(root, d, cfg) for d in days[len(existing) :]]
        if todo:
            with ProcessPoolExecutor(max_workers=4) as pool:
                for day, row in pool.map(training_session, todo, chunksize=3):
                    with db:
                        db.execute("INSERT INTO counts VALUES(?,?)", (day, json.dumps(row)))
        totals = defaultdict(Counter)
        by_year = {}
        for day, raw in db.execute("SELECT day,data FROM counts ORDER BY day"):
            year = by_year.setdefault(day[:4], defaultdict(Counter))
            for category, counts in json.loads(raw).items():
                totals[category].update(counts)
                year[category].update(counts)
        return {
            "start": str(days[0]),
            "end": str(days[-1]),
            "sessions": len(days),
            "totals": dict(totals),
            "by_year": by_year,
        }


def run_window(root, folder, name, days, train, books, quarantine, *, candidate_subset=None):
    directory = folder / name
    directory.mkdir(exist_ok=True)
    terms = root / "corporate_actions.json"
    if terms.exists():
        local = directory / terms.name
        if not local.exists():
            local.write_bytes(terms.read_bytes())
        if local.read_bytes() != terms.read_bytes():
            raise market.MarketError("Window settlement terms differ from the certified source.")
    path = directory / "journal.sqlite"
    if (
        directory.resolve() != directory
        or path.is_symlink()
        or (path.exists() and path.stat().st_nlink != 1)
    ):
        raise market.MarketError("A window must use its own independent ledger.")
    book_ids = {key for key, _ in books}
    edge = {key for key in book_ids if key.endswith("-equal-risk")}
    with closing(journal(path)) as db:
        if {r[0] for r in db.execute("SELECT id FROM runs")} - book_ids:
            raise market.MarketError("Window ledger already contains another account.")
        for run, _ in books:
            pilot.prefix(db, run, days)
        gaps = evaluation.replay_window(
            db, root, days, books, unconstrained_runs=edge, candidate_subset=candidate_subset
        )
        result = {}
        for run, cfg in books:
            count = pilot.prefix(db, run, days)
            audit = pilot.audit_ledger(db, run, days, cfg, root, quarantine)
            result[run] = {
                "expected": len(days),
                "scanned": count,
                "complete": count == len(days) and not audit["discrepancies"],
                "valuation_gap": gaps.get(run),
                "audit": audit,
            }
    return {
        "name": name,
        "training": train,
        "test": [str(days[0]), str(days[-1])],
        "sessions": [str(d) for d in days],
        "journal": str(path.relative_to(folder)),
        "books": result,
    }


def certify_inputs(root, receipt):
    """Reject core-only, stale or partly materialized Stage 2 archives."""
    audit = json.loads(receipt.read_bytes())
    if audit.get("complete") is not True:
        raise market.MarketError("Data preflight has unresolved blockers.")
    for field, folder in (
        ("earlier_controls", "earlier-controls"),
        ("legacy_surveillance", "legacy-surveillance"),
    ):
        record = audit.get(field, {})
        path = root.parent / folder / "coverage.json"
        if (
            record.get("complete") is not True
            or not path.is_file()
            or market.sha(path.read_bytes()) != record.get("coverage_sha256")
        ):
            raise market.MarketError(f"Missing or stale Stage 2 {field} certification")
    for field, path, digest in (
        ("static_opening_bands", root / "sources/static-bands/coverage.json", "coverage_sha256"),
        ("earnings_by_isin", root / "sources/earnings-by-isin/evidence.json", "source_sha256"),
        ("corporate_actions_by_isin", root / "sources/adjusted/action-aliases.json", "sha256"),
    ):
        record = audit.get(field, {})
        if (
            record.get("complete") is not True
            or not path.is_file()
            or market.sha(path.read_bytes()) != record.get(digest)
        ):
            raise market.MarketError(f"Missing or stale Stage 2 {field} certification")
    if audit["earnings_by_isin"].get("sessions") != len(audit["sessions"]):
        raise market.MarketError("Incomplete Stage 2 earnings coverage audit")
    universe = root / "universes" / "nifty200_membership.csv"
    canonical = audit.get("canonical_membership", {})
    if (
        not canonical.get("preserved_all_identity_intervals")
        or not universe.is_file()
        or market.sha(universe.read_bytes()) != canonical.get("published_sha256")
    ):
        raise market.MarketError("Missing or stale Stage 2 membership certification")
    primary = audit.get("primary_archives", {})
    if primary.get("sessions") != len(audit["sessions"]) or not primary.get(
        "source_manifests_sha256"
    ):
        raise market.MarketError("Missing Stage 2 daily equity and index archive reconciliation")
    if not audit.get("warmup_sources", {}).get("source_manifests_sha256") or not audit.get(
        "membership", {}
    ).get("anchor_archive", {}).get("sha256"):
        raise market.MarketError("Missing Stage 2 warm-up or pre-period weight anchor evidence")
    return pilot.preflight_inputs(root, receipt, expected_start=date(2014, 1, 1))


def replay(root, receipt):
    root, receipt = root.resolve(), receipt.resolve()
    if root.name != "market" or root.parent.name != "baseline" or receipt.parent != root.parent:
        raise market.MarketError(
            "Use the independent baseline/market root and its preflight receipt."
        )
    days, proof = certify_inputs(root, receipt)
    books = fixed_books()
    configs = {
        key.split("-")[0]: {"rule_revision": cfg["revision"], "config_hash": cfg["config_hash"]}
        for key, cfg in books
    }
    source = provenance.snapshot(root.parent)
    study = "full-history-walk-forward-v1"
    identity = market.sha(
        json.dumps([study, proof, configs, source["code_hash"]], sort_keys=True).encode()
    )
    folder = root.parent / "runs" / identity[:20]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "replay.json"
    partitions = evaluation.walk_windows(days, 3, 1, None)
    quarantine = json.loads(receipt.read_bytes()).get("quarantine", [])
    with (folder / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        metadata = folder / "identity.json"
        if metadata.is_file():
            current = json.loads(metadata.read_text())
            if current["identity"] != identity:
                raise market.MarketError("Replay identity differs from the existing ledger.")
        else:
            if (
                any(folder.glob("*/journal.sqlite"))
                or (folder / "training-momentum.sqlite").exists()
            ):
                raise market.MarketError("Existing study ledger has no matching identity record.")
            current = {"identity": identity, "study": study, **source, **proof, "configs": configs}
            pilot.write_json(metadata, current)
        first_test = partitions[0][1][0]
        training = training_census(root, folder, [d for d in days if d < first_test], books[0][1])
        report = {
            **current,
            "primary": "r2",
            "final_test": "forward_since_freeze",
            "start": str(days[0]),
            "end": str(days[-1]),
            "sessions": len(days),
            "training_momentum": training,
            "windows": [],
            "complete": False,
        }
        for name, test, train in partitions:
            window_quarantine = [q for q in quarantine if str(test[0]) <= q["day"] <= str(test[-1])]
            window = run_window(root, folder, name, test, train, books, window_quarantine)
            report["windows"].append(window)
            pilot.write_json(path, report)
            print(
                name,
                test[0],
                test[-1],
                {key: item["scanned"] for key, item in window["books"].items()},
                flush=True,
            )
        _, final = certify_inputs(root, receipt)
        if final != proof or provenance.snapshot(root.parent)["code_hash"] != source["code_hash"]:
            raise market.MarketError(
                "The source or certified replay inputs changed during the run."
            )
        report["complete"] = all(
            book["complete"] for w in report["windows"] for book in w["books"].values()
        )
        pilot.write_json(path, report)
        return path, report["complete"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/baseline/market"))
    parser.add_argument("--preflight", type=Path, default=Path("data/baseline/preflight.json"))
    args = parser.parse_args()
    file, complete = replay(args.root, args.preflight)
    print(file, flush=True)
    raise SystemExit(0 if complete else 1)
