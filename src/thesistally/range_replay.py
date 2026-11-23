"""Replay the preregistered C midpoint and top-quintile books on the certified close archive."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import subprocess
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd

from . import conventions, corporate, evaluation, exchange, market, pilot, signals, stage3_market
from .journal import UnpricedPosition, journal, process

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
STRATEGY = "range_bouncers"
TARGETS = {"C50": 0.5, "C80": 0.8}
BOOKS = ("funded", "range_equal", "other_equal")


def registered_books():
    result = {}
    for variant, fraction in TARGETS.items():
        cfg = signals.rules(PACKAGE / "revisions" / f"range-{variant.lower()}.yaml")
        if cfg["range_bouncers"]["target_fraction"] != fraction or cfg["exits"]["max_days"] != 20:
            raise market.MarketError("Range execution differs from the preregistration")
        for book in BOOKS:
            result[f"{variant}-{book}"] = cfg
    return result


def make_candidates(rows, by_isin, contexts, cfg):
    candidates = []
    for row in rows.itertuples(index=False):
        if row.cohort not in ("other", "range"):
            continue
        if by_isin.get(row.isin) != row.symbol or not row.selection_day <= row.day:
            raise market.MarketError(f"Range selection identity/date mismatch {row.day} {row.isin}")
        if (
            not all(
                math.isfinite(v)
                for v in (
                    row.close,
                    row.low60,
                    row.high60,
                    row.atr14,
                    row.turnover,
                    row.position60,
                    row.rsi2,
                )
            )
            or not row.turnover >= cfg["filters"]["min_turnover_inr"]
            or row.atr14 <= 0
            or not row.low60 <= row.close <= row.high60
            or row.high60 <= row.low60
            or row.position60 > 0.15 + 1e-12
            or row.rsi2 >= 10
        ):
            raise market.MarketError(f"Invalid observable range order at {row.day} {row.isin}")
        info = contexts[row.symbol]
        features = {
            "cohort": row.cohort,
            "selection_day": row.selection_day,
            "rsi2": row.rsi2,
            "atr14": row.atr14,
            "low60": row.low60,
            "high60": row.high60,
            "position60": row.position60,
            "median_turnover": row.turnover,
            "ranking_model_version": "range-position-isin-v1",
            "quarantined": info.get("quarantined", False),
            "surveillance": info["surveillance"],
            "earnings_coverage": info.get("earnings_coverage", "known"),
            "earnings_in_days": info["earnings_in_days"],
        }
        target = row.low60 + cfg["range_bouncers"]["target_fraction"] * (row.high60 - row.low60)
        candidates.append(
            signals.Candidate(
                row.symbol,
                row.isin,
                row.sector,
                STRATEGY,
                row.close,
                row.low60 - row.atr14,
                target,
                features,
            )
        )
    return candidates


def session(root, day, previous, raw, following, cfg, cash_events):
    # The shared as-of gate reconciles the exchange close against the dated member panel.
    passed, _ = stage3_market.session(root, day, lookbacks=())
    folder = root / "sessions" / str(day)
    members = market.read_members((folder / "members.csv").read_bytes())
    by_isin = dict(zip(members["isin"], members.symbol, strict=True))
    terms = corporate.actions(root)
    wanted = {c["isin"] for c in following} | set(raw["isin"])
    for isin in list(wanted):
        seen = set()
        while isin in terms and str(day) >= terms[isin]["suspended"]:
            if isin in seen:
                raise market.MarketError("Circular compulsory-action register")
            seen.add(isin)
            isin = terms[isin].get("successor_isin")
            if isin is None:
                break
            wanted.add(isin)
    controls = exchange.read_context(root, day, verify_sources=True)
    flags = exchange.price_quarantines(root, day)
    quotes = pd.read_parquet(
        folder / "bars.parquet",
        filters=[("day", "=", day)],
        columns=["symbol", "open", "high", "low", "close", "volume"],
    ).set_index("symbol")
    frames = {
        "NIFTY 50": pd.DataFrame(index=[previous, day]),
        "NIFTY 200": pd.DataFrame({"close": [quotes.at["NIFTY 200", "close"]]}, index=[day]),
    }
    for isin in wanted & by_isin.keys():
        symbol = by_isin[isin]
        info = controls[symbol]
        exchange.apply_quarantine(info, flags, isin)
        f = quotes.loc[[symbol]].set_axis([day])
        f.attrs["exchange"] = info
        frames[symbol] = f
    signals.attach_following(root, day, cfg, following, frames, by_isin)
    from .range_cash import reconcile

    # Off-index/renamed holders can have several keys pointing to one frame. A
    # scheme's frame quotes its successor, even when journalled under the parent.
    translated = set()
    current = [(isin, frames[by_isin[isin]]) for isin in wanted & by_isin.keys()]
    followed = [(isin, frames[isin]) for isin in {c["isin"] for c in following}]
    for source_isin, frame in [*current, *followed]:
        if id(frame) in translated:
            continue
        translated.add(id(frame))
        info = frame.attrs["exchange"]
        active_isin = info.get("compulsory_action", {}).get("successor_isin") or source_isin
        info = conventions.cash_dividend_notices(info, allow_short_dividend=True)
        frame.attrs["exchange"] = reconcile(info, day, active_isin, cash_events)
    for isin, frame in current:
        controls[by_isin[isin]] = frame.attrs["exchange"]
    for r in raw.itertuples(index=False):
        if ("results_strategy" in cfg or r.cohort in ("other", "range")) and not math.isclose(
            r.close, quotes.at[r.symbol, "close"], abs_tol=1e-6
        ):
            raise market.MarketError(
                f"Signal and certified execution close disagree {day} {r.isin}"
            )
    return {"enabled": passed}, frames, by_isin, controls


def run_window(args):
    root, path, signal_file, cash_hash, name, days, training = args
    from .range_cash import load_events

    cash_events = (
        load_events(signal_file.parent / "cash-events.json", cash_hash) if cash_hash else {}
    )
    directory = path / name
    directory.mkdir(exist_ok=True)
    ledger = directory / "journal.sqlite"
    if ledger.is_symlink() or (ledger.exists() and ledger.stat().st_nlink != 1):
        raise market.MarketError("Range accounts need their own annual journal")
    # The shared receivables ledger reads held-issuer settlement terms beside its journal.
    source_terms = root / "corporate_actions.json"
    if source_terms.exists():
        local = directory / source_terms.name
        if not local.exists():
            local.write_bytes(source_terms.read_bytes())
        if local.read_bytes() != source_terms.read_bytes():
            raise market.MarketError("Window settlement terms differ from the certified view")
    books = registered_books()
    data = pd.read_parquet(
        signal_file, filters=[("day", ">=", str(days[0])), ("day", "<=", str(days[-1]))]
    )
    daily = {day: group for day, group in data.groupby("day")}
    empty = data.iloc[:0]
    previous = date.fromisoformat(training[-1])
    gaps = {}
    with closing(journal(ledger)) as db:
        prefixes = {run: pilot.prefix(db, run, days) for run in books}
        if {r[0] for r in db.execute("SELECT id FROM runs")} - set(books):
            raise market.MarketError("Unexpected range study ledger")
        for number, day in enumerate(days):
            pending = [run for run in books if number >= prefixes[run] and run not in gaps]
            if pending:
                items = [c for run in pending for c in evaluation.open_calls(db, run, day)]
                rows = daily.get(str(day), empty)
                regime, frames, by_isin, controls = session(
                    root, day, previous, rows, items, books[pending[0]], cash_events
                )
                decisions = {
                    v: make_candidates(rows, by_isin, controls, books[v + "-funded"])
                    for v in TARGETS
                }
                for run in pending:
                    variant, book = run.split("-", 1)
                    cohort = "other" if book == "other_equal" else "range"
                    candidates = [c for c in decisions[variant] if c.features["cohort"] == cohort]
                    try:
                        process(
                            db,
                            run,
                            day,
                            regime,
                            candidates,
                            frames,
                            books[run],
                            unconstrained=book != "funded",
                        )
                    except UnpricedPosition as exc:
                        gaps[run] = exc.detail
            previous = day
        results = {}
        for run, cfg in books.items():
            n = pilot.prefix(db, run, days)
            audit = pilot.audit_ledger(db, run, days, cfg, root)
            results[run] = {
                "expected": len(days),
                "scanned": n,
                "complete": n == len(days) and not audit["discrepancies"],
                "valuation_gap": gaps.get(run),
                "audit": audit,
            }
    window = {
        "name": name,
        "training": training,
        "test": [str(days[0]), str(days[-1])],
        "sessions": list(map(str, days)),
        "journal": str(ledger.relative_to(path)),
        "books": results,
    }
    pilot.write_json(directory / "window.json", window)
    print(
        name,
        {k: (v["scanned"], v["audit"]["discrepancies"]) for k, v in results.items()},
        flush=True,
    )
    return window


def code_identity():
    modules = [
        "range_replay",
        "range_orders",
        "range_cash",
        "range_market",
        "signals",
        "stage3_market",
        "market",
        "exchange",
        "evaluation",
        "journal",
        "paper",
        "dip_bonus",
        "conventions",
        "dip_coverage",
        "corporate",
        "pilot",
        "brake",
        "ranking",
        "historical_sessions",
        "baseline_actions",
        "baseline_static_bands",
        "trading_calendar",
        "research_journal",
    ]
    paths = [PACKAGE / (m + ".py") for m in modules] + [
        PACKAGE / "revisions" / f for f in ("r2.yaml", "range-c50.yaml", "range-c80.yaml")
    ]
    return {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def replay(data, workers=4):
    from .range_market import prepare_market

    data = data.resolve()
    base = data / "range-bouncers"
    root, receipt = prepare_market(data)
    signals_file = base / "signals.parquet"
    manifest_path = base / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    if market.sha(signals_file.read_bytes()) != manifest["products"]["signals.parquet"]["sha256"]:
        raise market.MarketError("Range signals do not match their selection receipt")
    if manifest["snapshot_sha256"] != receipt["snapshot_sha256"]:
        raise market.MarketError("Selection data and execution market snapshots differ")
    source = code_identity()
    proof = {
        "preregistration_sha256": market.sha((REPO / "docs/PREREGISTRATION.md").read_bytes()),
        "preregistration_commit": "8a67849",
        "market": receipt,
        "signals_sha256": manifest["products"]["signals.parquet"]["sha256"],
        "cash_events_sha256": manifest["cash_events_sha256"],
        "signal_manifest_sha256": market.sha(manifest_path.read_bytes()),
        "source_files": source,
        "configs": {k: v["config_hash"] for k, v in registered_books().items()},
    }
    identity = market.sha(json.dumps(proof, sort_keys=True).encode())
    folder = base / "runs" / identity[:20]
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        meta_file = folder / "identity.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_bytes())
            if meta["identity"] != identity or meta["proof"] != proof:
                raise market.MarketError(
                    "Existing range study cannot be resumed under different settings"
                )
        else:
            if any(folder.glob("walk-*/journal.sqlite")):
                raise market.MarketError("Range ledger has no matching study identity")
            commit = subprocess.check_output(
                ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
            ).strip()
            meta = {"identity": identity, "code_commit": commit, "proof": proof}
            pilot.write_json(meta_file, meta)
        partitions = evaluation.walk_windows(market.sessions(root), 3, 1, None)
        tasks = [
            (root, folder, signals_file, manifest["cash_events_sha256"], *p) for p in partitions
        ]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            windows = list(pool.map(run_window, tasks))
        if (
            source != code_identity()
            or market.sha(signals_file.read_bytes()) != proof["signals_sha256"]
        ):
            raise market.MarketError("Execution source or input cache changed during C replay")
        report = {
            **meta,
            "windows": windows,
            "complete": all(v["complete"] for w in windows for v in w["books"].values()),
        }
        pilot.write_json(folder / "replay.json", report)
    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    print(replay(args.data, args.workers), flush=True)
