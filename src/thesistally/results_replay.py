"""Research-only paired D40/D20 walk-forward on coverage-eligible result windows."""

import fcntl
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd

from . import (
    baseline_accounts,
    baseline_metrics,
    baseline_qualification,
    dip_coverage,
    equity_tax,
    evaluation,
    market,
    pilot,
    range_cash,
    range_replay,
    steady_replay,
    steady_tax,
)
from .journal import UnpricedPosition, journal, process
from .results_replay_orders import STRATEGY, candidates, settings
from .results_replay_tax import benchmark_tax, book_tax

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
REGISTRATION = "af36fef"
PREREG_HASH = "114455e79524903a67395648d45837a6d3e6dc2ef39773c275e0a79157e99101"
VIEW = Path("range-bouncers/inputs/babf7a7862a0fb38e23e")
PINS = {
    str(
        VIEW / "certified.json"
    ): "c25950123bbfaa1a18fae5ad523e01a05365ea6218304623285c1d9ceaebee60",
    str(
        VIEW / "market/corporate_actions.json"
    ): "a6c801c744ccb7e6deee364e36e845cd57e3a9a59a23a30edfb29e1fb2549c03",
    str(
        VIEW / "market/dip-bonus-actions.json"
    ): "beb8261f859ef2a8c050a4c0361a07e36faab2e46495349606a148b0e71eb96a",
    "range-bouncers/cash-events.json": "4d9b93761d3c2f200b135a212975e3c34e1b9bdd4bc9bc19cb6d0a0d698d1de0",
    "stage3/benchmark.json": "0c86d2e5d7b561cb2ecd1e5cefb513a37ed7a394437537e8f9d6676f98285419",
    "stage3/benchmark.csv": "cc869e48f7049102cf7a3ef770614ff02e05afcf912415e48849b476f4ee8f41",
}
BOOKS = ("funded", "equal_risk")


def digest(path):
    return market.sha(path.read_bytes())


def configurations():
    return {f"D{n}-{book}": settings(n, book) for n in (40, 20) for book in BOOKS}


def record(path, value):
    if path.exists():
        if json.loads(path.read_bytes()) != json.loads(json.dumps(value, allow_nan=False)):
            raise ValueError(f"Refusing to replace a previous D result at {path}")
        return
    pilot.write_json(path, value)


def fingerprint():
    modules = [
        "results_replay",
        "results_replay_orders",
        "results_replay_tax",
        "journal",
        "corporate",
        "dip_bonus",
        "range_replay",
        "range_cash",
        "signals",
        "paper",
        "pilot",
        "market",
        "exchange",
        "baseline_qualification",
        "baseline_accounts",
        "baseline_metrics",
        "equity_tax",
        "steady_tax",
        "evaluation",
        "ranking",
        "brake",
        "conventions",
        "dip_coverage",
        "stage3_market",
    ]
    return {
        p.name: digest(p)
        for p in [
            *(PACKAGE / (m + ".py") for m in modules),
            equity_tax.DEFAULT_CONFIG,
            PACKAGE / "revisions/r2.yaml",
            baseline_qualification.CONFIG_PATH,
        ]
    }


def read_inputs(fundamentals, archive):
    raw = subprocess.check_output(
        ["git", "-C", str(REPO), "show", REGISTRATION + ":docs/PREREGISTRATION.md"]
    )
    if market.sha(raw) != PREREG_HASH:
        raise ValueError("Strategy D registration receipt is missing")
    for filename, sha in PINS.items():
        if digest(archive / filename) != sha:
            raise ValueError(f"Historical market receipt mismatch: {filename}")
    market_meta = json.loads((archive / VIEW / "certified.json").read_bytes())
    coverage_path = fundamentals / "coverage.json"
    sig_path = fundamentals / "signals.parquet"
    receipt_path = fundamentals / "signals-receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    if (
        digest(sig_path) != receipt["signals_sha256"]
        or digest(coverage_path) != receipt["coverage_sha256"]
    ):
        raise ValueError("Strategy D signals or coverage changed after certification")
    from .results_signals import verify_filing_products

    canonical = verify_filing_products(fundamentals, coverage_path.read_bytes())
    if (
        market.sha(canonical) != receipt["source_sha256"]
        or digest(PACKAGE / "results_signals.py") != receipt["signal_method_sha256"]
        or not receipt["registration_commit"].startswith(REGISTRATION)
    ):
        raise ValueError(
            "Signal method, filing data or registration differs from the decision receipt"
        )
    coverage = json.loads(coverage_path.read_bytes())["windows"]
    days = sorted(map(date.fromisoformat, market_meta["session_manifests"]))
    windows = evaluation.walk_windows(days, 3, 1, None)
    expected = {str(test[0].year) for _, test, _ in windows}
    if expected - set(coverage) or any(
        type(coverage[y]["coverage_eligible"]) is not bool for y in expected
    ):
        raise ValueError("Missing annual coverage determination")
    proof = {
        "preregistration_commit": REGISTRATION,
        "preregistration_sha256": PREREG_HASH,
        "inputs": PINS,
        "signals": receipt["signals_sha256"],
        "coverage": receipt["coverage_sha256"],
        "signal_receipt": digest(receipt_path),
        "source": fingerprint(),
        "configs": {k: v["config_hash"] for k, v in configurations().items()},
    }
    return proof, coverage, windows


def empty_book():
    return {
        "replayed": False,
        "complete": False,
        "trades": None,
        "mean_gross_r": None,
        "mean_net_r": None,
        "mean_net_r_ci90": None,
        "max_drawdown_pct": None,
        "strategy_return_pct": None,
        "benchmark_return_pct": None,
        "after_tax": None,
        "loss_diagnosis": None,
        "entry_coverage": None,
    }


def measured(
    db,
    run_id,
    cfg,
    scanned_days,
    training_end,
    year,
    audit,
    valuation_gap,
    archive_root,
    benchmark,
    cash,
    notices,
    total_sessions,
):
    book = run_id.split("-", 1)[1]
    alloc = "portfolio" if book == "funded" else "unconstrained"
    all_rows = baseline_metrics.read_calls(db, run_id, year, scanned_days, cfg)
    rows = [r for r in all_rows if r["allocation"] == alloc]
    closed = [r for r in rows if r["mature"] and r["status"] == "closed"]
    qc = baseline_qualification.validation_config()
    stats = baseline_metrics.summary(closed)
    entry = (
        dip_coverage.entry_coverage(db, run_id, scanned_days[-1], alloc) if scanned_days else None
    )
    complete = (
        len(scanned_days) == total_sessions and not audit["discrepancies"] and not valuation_gap
    )
    result = {
        **empty_book(),
        "replayed": True,
        "complete": complete,
        "scanned_sessions": len(scanned_days),
        "expected_sessions": total_sessions,
        "audit": audit,
        "valuation_gap": valuation_gap,
        "entry_coverage": entry,
    }
    # Partial economic measurements cannot masquerade as annual returns.
    if not complete:
        return result, rows
    gate = {
        d: "on" if g else "off"
        for d, g in db.execute(
            "SELECT day,json_extract(regime,'$.enabled') FROM scans WHERE run=? ORDER BY day",
            (run_id,),
        )
    }
    capital = cfg["risk"]["capital"]
    args = (db, run_id, scanned_days, gate, benchmark, capital)
    account = (
        baseline_accounts.funded_marks(*args, strategies=(STRATEGY,))
        if book == "funded"
        else baseline_accounts.independent_marks(*args, rows, strategies=(STRATEGY,))
    )["portfolio"]
    tri = 100 * (benchmark[scanned_days[-1]] / benchmark[training_end] - 1)
    net = 100 * account["net_pnl_inr"] / capital
    path = [benchmark[training_end], *(benchmark[d] for d in scanned_days)]
    high = path[0]
    benchmark_drawdown = 0.0
    for v in path:
        high = max(high, v)
        benchmark_drawdown = max(benchmark_drawdown, 100 * (1 - v / high))
    account.update(
        benchmark_anchor_close=training_end,
        nifty200_buy_hold_return_pct=tri,
        excess_return_pp=net - tri,
    )
    try:
        taxes = book_tax(
            db,
            run_id,
            book,
            archive_root,
            benchmark,
            training_end,
            scanned_days[-1],
            capital + account["net_pnl_inr"],
            cfg,
            cash,
            notices,
        )
    except ValueError as e:
        # A missing entitlement, FMV or record date cannot silently become a zero tax.
        taxes = {
            "strategy": None,
            "gap": str(e),
            "tri": benchmark_tax(
                benchmark,
                date.fromisoformat(training_end),
                date.fromisoformat(scanned_days[-1]),
                capital,
                equity_tax.load_config(),
            ),
        }
    result.update(
        trades=len(closed),
        all_observed=baseline_metrics.summary(rows),
        mature=stats,
        mean_gross_r=stats["mean_gross_r"],
        mean_net_r=stats["mean_net_r"],
        mean_net_r_ci90=baseline_qualification.clustered_mean_ci(closed, qc),
        loss_diagnosis=baseline_metrics.loss_diagnosis(closed, qc),
        account=account,
        strategy_return_pct=net,
        benchmark_return_pct=tri,
        benchmark_drawdown_pct=benchmark_drawdown,
        max_drawdown_pct=account["max_drawdown_pct"]
        if book == "funded"
        else account["max_peak_to_trough_loss_pct_of_reference"],
        reference_only=book == "equal_risk",
        after_tax=taxes,
    )
    return result, rows


def run_year(args):
    archive, signals_path, directory, name, days, train, annual = args
    year = str(days[0].year)
    meta = {
        "name": name,
        "year": year,
        "training": train,
        "start": str(days[0]),
        "end": str(days[-1]),
        "scheduled_sessions": len(days),
        "first_eligible_decision_close": str(days[0]),
        "first_possible_entry_open": str(days[1]) if len(days) > 1 else None,
        "benchmark_anchor_prior_close": train[-1],
        "filing_coverage": annual,
    }
    configs = configurations()
    if not annual["coverage_eligible"]:
        return {
            **meta,
            "status": "incomplete_filing_coverage",
            "books": {k: empty_book() for k in configs},
        }, {}
    directory = directory / name
    directory.mkdir(exist_ok=True)
    root = archive / VIEW / "market"
    certificate = json.loads((archive / VIEW / "certified.json").read_bytes())["session_manifests"]
    cash = range_cash.load_events(
        archive / "range-bouncers/cash-events.json", PINS["range-bouncers/cash-events.json"]
    )
    bench = steady_replay.read_benchmark(archive / "stage3/benchmark.csv")
    notices = steady_tax.record_dates(root)
    terms = directory / "corporate_actions.json"
    original = (root / "corporate_actions.json").read_bytes()
    if not terms.exists():
        terms.write_bytes(original)
    if terms.read_bytes() != original:
        raise ValueError("Window's corporate terms changed")
    data = pd.read_parquet(
        signals_path, filters=[("day", ">=", str(days[0])), ("day", "<=", str(days[-1]))]
    )
    if data.duplicated(["day", "isin"]).any() or not set(data.day).issubset(map(str, days)):
        raise ValueError("D decision census has duplicate ISIN or non-session")
    daily = dict(tuple(data.groupby("day")))
    empty = data.iloc[:0]
    prior = date.fromisoformat(train[-1])
    gaps = {}
    ledger = directory / "journal.sqlite"
    if ledger.is_symlink() or (ledger.exists() and ledger.stat().st_nlink != 1):
        raise ValueError("D requires a private annual journal")
    with closing(journal(ledger)) as db:
        prefixes = {k: pilot.prefix(db, k, days) for k in configs}
        if {r[0] for r in db.execute("SELECT id FROM runs")} - set(configs):
            raise ValueError("Foreign run in D journal")
        for i, day in enumerate(days):
            active = [k for k in configs if i >= prefixes[k] and k not in gaps]
            if digest(root / "sessions" / str(day) / "manifest.json") != certificate[str(day)]:
                raise ValueError(f"Unverified market session {day}")
            if active:
                following = [c for k in active for c in evaluation.open_calls(db, k, day)]
                rows = daily.get(str(day), empty)
                if not rows.empty and not (rows.usable_session == str(prior)).all():
                    raise ValueError(f"Result decision is not U+1 on {day}")
                gate, frames, members, controls = range_replay.session(
                    root, day, prior, rows, following, configs[active[0]], cash
                )
                signals = candidates(rows, members, controls)
                for k in active:
                    try:
                        process(
                            db,
                            k,
                            day,
                            gate,
                            signals,
                            frames,
                            configs[k],
                            unconstrained=k.endswith("equal_risk"),
                        )
                    except UnpricedPosition as e:
                        gaps[k] = e.detail
            prior = day
        by_book = {}
        records = {}
        for k, cfg in configs.items():
            n = pilot.prefix(db, k, days)
            seen = list(map(str, days[:n]))
            audit = pilot.audit_ledger(db, k, days, cfg, root)
            by_book[k], records[k] = measured(
                db,
                k,
                cfg,
                seen,
                train[-1],
                year,
                audit,
                gaps.get(k),
                root,
                bench,
                cash,
                notices,
                len(days),
            )
            observed = by_book[k]["entry_coverage"]
            if observed and observed["raw_signals"] != len(data[data.day.isin(seen)]):
                raise ValueError("D signal and execution censuses disagree")
    result = {**meta, "status": "replayed", "books": by_book, "journal_sha256": digest(ledger)}
    record(directory / "window.json", result)
    return result, records


def run(fundamentals, archive, *, workers=3):
    fundamentals = Path(fundamentals).resolve(strict=True)
    archive = Path(archive).resolve(strict=True)
    if fundamentals != REPO / "data/fundamentals" or not (REPO / ".git").is_file():
        raise ValueError("Use the isolated Strategy D worktree fundamentals directory")
    proof, coverage, windows = read_inputs(fundamentals, archive)
    identity = market.sha(json.dumps(proof, sort_keys=True).encode())
    dest = fundamentals / "replays" / identity[:20]
    dest.mkdir(parents=True, exist_ok=True)
    with (dest / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record(dest / "identity.json", {"identity": identity, "proof": proof})
        jobs = [
            (
                archive,
                fundamentals / "signals.parquet",
                dest,
                name,
                days,
                train,
                coverage[str(days[0].year)],
            )
            for name, days, train in windows
        ]
        if workers == 1 or not any(j[-1]["coverage_eligible"] for j in jobs):
            outcomes = list(map(run_year, jobs))
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                outcomes = list(pool.map(run_year, jobs))
        results = {w["year"]: w for w, _ in outcomes}
        qcfg = baseline_qualification.validation_config()
        qualification = {}
        for k in configurations():
            allowed = {
                w["year"]: w["books"][k]["complete"]
                for w, _ in outcomes
                if w["filing_coverage"]["coverage_eligible"]
                and not (w["books"][k].get("entry_coverage") or {}).get("coverage_excluded")
            }
            rows = [
                row
                for _, by_book in outcomes
                for row in by_book.get(k, [])
                if row["window"] in allowed
            ]
            qual = baseline_qualification.qualification(rows, allowed, qcfg)
            qual["excluded_years"] = [year for year in results if year not in allowed]
            qualification[k] = qual
        check, _, _ = read_inputs(fundamentals, archive)
        if check != proof:
            raise ValueError("D inputs or code changed during replay")
        report = {
            "identity": identity,
            "proof": proof,
            "code_commit": subprocess.check_output(
                ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
            ).strip(),
            "eligible_years": [
                y for y, w in results.items() if w["filing_coverage"]["coverage_eligible"]
            ],
            "by_year_and_window": results,
            "qualification": qualification,
        }
        path = dest / "report.json"
        # Identical code and inputs can be reproduced after unrelated documentation commits.
        if path.exists():
            report["code_commit"] = json.loads(path.read_bytes())["code_commit"]
        record(path, report)
    return path


if __name__ == "__main__":
    from .discovery_worker import archive_root

    print(run(REPO / "data/fundamentals", archive_root()), flush=True)
