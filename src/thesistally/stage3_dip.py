"""Run and report both registered RSI thresholds using the shared paper engine."""

import argparse
import csv
import fcntl
import io
import json
import math
import sqlite3
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import date
from pathlib import Path

from . import (
    convention_market,
    conventions,
    dip,
    dip_coverage,
    dip_holder_market,
    evaluation,
    market,
    provenance,
    stage3_market,
)
from .baseline_accounts import funded_marks, independent_marks
from .baseline_metrics import describe_strategy, read_calls
from .baseline_qualification import qualification, validation_config
from .baseline_replay import run_window
from .pilot_data import write_json
from .signals import rules

VARIANTS = ("dip-rsi10", "dip-rsi5")
BOOKS = {"funded": "-portfolio", "equal_risk": "-equal-risk"}


def registered_books(*, revised=False):
    configs = [rules(revision=variant) for variant in VARIANTS]
    parameters = []
    for cfg, threshold in zip(configs, (10, 5), strict=True):
        comparable = deepcopy(cfg)
        if comparable["dip"].pop("rsi_below") != threshold:
            raise market.MarketError("The registered RSI thresholds have changed.")
        for field in ("version", "revision", "config_hash"):
            comparable.pop(field)
        parameters.append(comparable)
    if parameters[0] != parameters[1]:
        raise market.MarketError("The RSI variants cannot share different features or controls.")
    if revised:
        for cfg in configs:
            cfg["entry_band_convention"] = conventions.ENTRY_BAND_VERSION
            cfg["strategy_config_hash"] = cfg["config_hash"]
            cfg["revision"] += "@" + conventions.ENTRY_BAND_VERSION
            cfg["config_hash"] = market.sha(json.dumps(cfg, sort_keys=True).encode())
    return [
        (variant + suffix, cfg)
        for variant, cfg in zip(VARIANTS, configs, strict=True)
        for suffix in BOOKS.values()
    ]


def subset(candidates, cfg):
    # Pool uses the wider primary threshold; strict cut on the same frozen RSI
    # reproduces the detector from a standalone forward scan under either config.
    return [c for c in candidates if c.features["rsi2"] < cfg["dip"]["rsi_below"]]


def window_account(db, run, book, window, cfg, benchmark, prior_gate):
    scans = list(
        db.execute(
            "SELECT day,json_extract(regime,'$.enabled') FROM scans WHERE run=? ORDER BY day",
            (run,),
        )
    )
    days = [s[0] for s in scans]
    gate = {s[0]: "on" if s[1] else "off" for s in scans}
    meta = window["books"][run]
    if days != window["sessions"][: meta["scanned"]] or (
        meta["complete"] and len(days) != len(window["sessions"])
    ):
        raise market.MarketError(f"{run} reported coverage differs from the journal.")
    ledger_cfg = db.execute(
        "SELECT v.config FROM runs r JOIN rule_configs v ON v.revision=r.rules WHERE r.id=?", (run,)
    ).fetchone()
    if days and (not ledger_cfg or json.loads(ledger_cfg[0]) != cfg):
        raise market.MarketError(f"{run} stored settings differ from the registered revision.")
    prior_gate_by_day = dict(zip(days, ["on" if prior_gate else "off", *list(gate.values())]))
    rows = read_calls(db, run, window["name"], days, cfg)
    for row in rows:
        if row["status"] == "closed":
            row["exit_gate"] = gate[row["exit_day"]]
    capital = cfg["risk"]["capital"]
    args = db, run, days, prior_gate_by_day, benchmark, capital
    if book == "equal_risk":
        marks = independent_marks(*args, rows, strategies=(dip.STRATEGY,))
        chosen = rows
    else:
        marks = funded_marks(*args, strategies=(dip.STRATEGY,))
        chosen = [r for r in rows if r["allocation"] == "portfolio"]
    account = marks["portfolio"]
    if days and not math.isclose(
        account["net_pnl_inr"], marks[dip.STRATEGY]["net_pnl_inr"], rel_tol=0, abs_tol=1e-5
    ):
        raise market.MarketError(f"{run}: single-strategy marks diverge from portfolio.")
    anchor = window["training"][-1]
    account["benchmark_anchor_close_day"] = anchor
    if days:
        compared = (benchmark[days[-1]] / benchmark[anchor] - 1) * 100
        account["nifty200_buy_hold_return_pct"] = compared
        if book == "funded":
            account["excess_return_pp"] = account["return_pct"] - compared
    event_types = (
        "exit_deferred",
        "exit_unknown_price_band",
        "corporate_action",
        "quarantine_release",
    )
    matched = "portfolio" if book == "funded" else "unconstrained"
    events = {
        r[0]: {"events": r[1], "distinct_calls": r[2]}
        for r in db.execute(
            """
            SELECT e.kind,count(*),count(DISTINCT c.id) FROM calls c JOIN events e ON e.call_id=c.id
            WHERE c.run=? AND c.allocation=? AND e.kind IN (?,?,?,?) GROUP BY e.kind
            """,
            (run, matched, *event_types),
        )
    }
    coverage = {
        "scheduled": window["test"],
        "observed": [days[0], days[-1]] if days else [],
        "scanned": len(days),
        "expected": len(window["sessions"]),
        "complete": meta["complete"],
        "valuation_gap": meta["valuation_gap"],
        "ledger_audit_discrepancies": meta["audit"]["discrepancies"],
        "ledger_audit_examples": meta["audit"]["examples"],
        "gate_sessions": dict(Counter(gate.values())),
        "journalled_plans": len(rows),
        "allocation_dispositions": dict(Counter(r["allocation"] for r in rows)),
        "events": {k: events.get(k, {"events": 0, "distinct_calls": 0}) for k in event_types},
    }
    if cfg.get("entry_band_convention"):
        coverage["entry_coverage"] = dip_coverage.entry_coverage(
            db, run, window["sessions"][-1], matched
        )
    return chosen, account, coverage


def covered_qualification(rows, coverage, stats):
    kept = {
        w: c["complete"]
        for w, c in coverage.items()
        if not c["entry_coverage"]["coverage_excluded"]
    }
    excluded = sorted(set(coverage) - set(kept))
    q = qualification([r for r in rows if r["window"] in kept], kept, stats)
    observed = sum(c["complete"] for c in coverage.values())
    q.update(
        scheduled_windows=len(coverage),
        coverage_excluded_windows=excluded,
        breadth_denominator=len(kept),
        observed_mature_closes_including_exclusions=sum(
            r["mature"] and r["status"] == "closed" for r in rows
        ),
    )
    q["criteria"]["completed_replays"].update(
        value=observed, threshold=len(coverage), passed=observed == len(coverage)
    )
    q["incomplete_windows"] = [w for w, c in coverage.items() if not c["complete"]]
    if observed < len(coverage):
        q.update(qualified=False, verdict="not enough data")
    return q


def analyze(folder, windows, benchmark, prior_gates, *, revised=False):
    stats = validation_config()
    years = sorted({d[:4] for w in windows for d in w["sessions"]})
    results = {}
    configs = dict(registered_books(revised=revised))
    for variant in VARIANTS:
        cfg = configs[variant + BOOKS["funded"]]
        results[variant] = {}
        for book, suffix in BOOKS.items():
            rows, accounts, coverage = [], {}, {}
            for w in windows:
                path = folder / w["journal"]
                with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
                    db.row_factory = sqlite3.Row
                    chosen, marks, sample = window_account(
                        db, variant + suffix, book, w, cfg, benchmark, prior_gates[w["name"]]
                    )
                rows.extend(chosen)
                accounts[w["name"]] = marks
                coverage[w["name"]] = sample
            completion = {name: w["complete"] for name, w in coverage.items()}
            metrics = describe_strategy(
                rows, accounts, completion, years, stats, qualify=not revised
            )
            if revised:
                q = covered_qualification(rows, coverage, stats)
                metrics.update(qualification=q, verdict=q["verdict"])
                for name, sample in coverage.items():
                    year = sample["scheduled"][0][:4]
                    if year != sample["scheduled"][-1][:4]:
                        raise market.MarketError(
                            "Split entry coverage by year before reporting a cross-year test window"
                        )
                    metrics["by_year"][year]["entry_coverage"] = sample["entry_coverage"]
                    metrics["by_window"][name]["entry_coverage"] = sample["entry_coverage"]
            metrics["coverage_by_window"] = coverage
            results[variant][book] = metrics
    return results


def study(root, *, revised=True):
    root = root.resolve()
    base = root / "stage3"
    receipt = convention_market.certify(root)
    days = sorted(map(date.fromisoformat, receipt["session_manifests"]))
    benchmark_raw = (base / "benchmark.csv").read_bytes()
    benchmark_source = json.loads((base / "benchmark.json").read_bytes())
    if market.sha(benchmark_raw) != benchmark_source["sha256"]:
        raise market.MarketError("The benchmark does not match its source receipt.")
    bench = {
        r["day"]: float(r["close"]) for r in csv.DictReader(io.StringIO(benchmark_raw.decode()))
    }
    books = registered_books(revised=revised)
    prereg = Path(__file__).resolve().parents[2] / "docs/PREREGISTRATION.md"
    source = provenance.snapshot(base)
    holders = dip_holder_market.split_receipt(root, revised=revised)
    proof = {
        "strategy": dip.STRATEGY,
        "primary": VARIANTS[0],
        "preregistration_sha256": market.sha(prereg.read_bytes()),
        "qualification": validation_config(),
        "snapshot_sha256": receipt["snapshot_sha256"],
        "holder_action_sha256": market.sha(json.dumps(holders, sort_keys=True).encode()),
        "parent_snapshot_sha256": receipt["parent_snapshot_sha256"],
        "benchmark_sha256": benchmark_source["sha256"],
        "benchmark_series": benchmark_source["series"],
        "code_hash": source["code_hash"],
        "configs": {
            k: {
                "revision": c["revision"],
                "sha256": c["config_hash"],
                "base_r2_sha256": c["base_sha256"],
            }
            for k, c in books
        },
    }
    if revised:
        proof["entry_band_convention"] = conventions.ENTRY_BAND_VERSION
        proof["coverage_limit"] = {
            "numerator": 1,
            "denominator": 10,
            "exclude_if": "strictly_greater",
        }
        proof["underlying_strategy_config_hashes"] = {
            k: c["strategy_config_hash"] for k, c in books
        }
    identity = market.sha(json.dumps(proof, sort_keys=True).encode())
    folder = base / "runs" / ("dip-" + identity[:20])
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        identity_path = folder / "identity.json"
        if identity_path.exists():
            meta = json.loads(identity_path.read_bytes())
            if meta["proof"] != proof or meta["identity"] != identity:
                raise market.MarketError("Existing dip window belongs to a different study.")
        else:
            if any(folder.glob("walk-*/journal.sqlite")):
                raise market.MarketError("A previous ledger has no study receipt.")
            meta = {"identity": identity, "proof": proof, **source}
            write_json(identity_path, meta)
        market_root = dip_holder_market.holder_market(root, folder, holders)
        write_json(folder / "holder-actions.json", holders)
        windows, priors = [], {}
        for name, test, train in evaluation.walk_windows(days, 3, 1, None):
            prior = date.fromisoformat(train[-1])
            if not all(str(d) in bench for d in [prior, *test]):
                raise market.MarketError(f"{name}: missing a benchmark observation")
            priors[name], _ = stage3_market.session(base / "market", prior, lookbacks=())
            w = run_window(
                market_root, folder, name, test, train, books, (), candidate_subset=subset
            )
            windows.append(w)
            write_json(folder / "replay.json", {**meta, "windows": windows})
            print(
                name,
                {run: (r["scanned"], r["audit"]["discrepancies"]) for run, r in w["books"].items()},
                flush=True,
            )
        result = {
            **meta,
            "windows": [w["name"] for w in windows],
            "benchmark": {k: v for k, v in benchmark_source.items() if k != "sources"},
            "holder_terms": holders,
            "variants": analyze(folder, windows, bench, priors, revised=revised),
            "complete": all(m["complete"] for w in windows for m in w["books"].values()),
        }
        if convention_market.certify(root)["snapshot_sha256"] != proof["snapshot_sha256"] or (
            market.sha((base / "benchmark.csv").read_bytes()) != proof["benchmark_sha256"]
            or provenance.snapshot(base)["code_hash"] != proof["code_hash"]
        ):
            raise market.MarketError("Research inputs changed while the study was running.")
        destination = root / "backtests" / ("stage3-dip-" + identity[:20] + ".json")
        destination.parent.mkdir(exist_ok=True)
        write_json(destination, result)
        print(destination, flush=True)
    return destination, result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument(
        "--archived-entry-rule",
        action="store_true",
        help="Use the prior Stage 3 data convention and qualification.",
    )
    args = parser.parse_args()
    _, report = study(args.root, revised=not args.archived_entry_rule)
    raise SystemExit(0 if report["complete"] else 1)
