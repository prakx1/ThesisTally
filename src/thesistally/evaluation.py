"""Chronological, after-cost evaluation of the same portfolio engine used for observation."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from contextlib import closing
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import baseline_qualification, history, market
from .exchange import read_context
from .journal import STRATEGIES, UnpricedPosition, journal, metrics, process, run_report
from .signals import load_session, rules


def walk_windows(days, train_years, test_years, holdout_months):
    if (
        not days
        or min(train_years, test_years) <= 0
        or (holdout_months is not None and holdout_months <= 0)
    ):
        raise market.MarketError("Window lengths and session coverage must be positive.")
    holdout = days[-1] + timedelta(days=1)
    if holdout_months is not None:
        holdout = (pd.Timestamp(holdout) - pd.DateOffset(months=holdout_months)).date()
    cursor = pd.Timestamp(days[0]) + pd.DateOffset(years=train_years)
    windows = []
    if (cursor + pd.DateOffset(years=test_years)).date() > holdout:
        raise market.MarketError("Insufficient history for a full training and test window.")
    while cursor.date() < holdout:
        end = min(cursor + pd.DateOffset(years=test_years), pd.Timestamp(holdout))
        test = [d for d in days if cursor.date() <= d < end.date()]
        if not test:
            raise market.MarketError(f"Missing test sessions from {cursor.date()}.")
        train = [
            d
            for d in days
            if (cursor - pd.DateOffset(years=train_years)).date() <= d < cursor.date()
        ]
        windows.append((f"walk-{len(windows) + 1}", test, [str(train[0]), str(train[-1])]))
        cursor = end
    if holdout_months is not None:
        windows.append(("holdout", [d for d in days if d >= holdout], None))
    return windows


def bootstrap(values, cfg):
    if not values:
        return [None, None]
    samples = cfg["bootstrap_samples"]
    rng = np.random.default_rng(cfg["bootstrap_seed"])
    x = np.asarray(values, dtype=float)
    chunk = max(1, min(256, 1000000 // len(x)))
    means = np.concatenate(
        [
            rng.choice(x, size=(min(chunk, samples - i), len(x))).mean(axis=1)
            for i in range(0, samples, chunk)
        ]
    )
    tail = (1 - cfg["confidence"]) / 2
    return np.quantile(means, [tail, 1 - tail]).tolist()


def trade_stats(rows, cfg):
    out = metrics(rows)
    closed = [r for r in rows if r["status"] == "closed"]

    def avg(key):
        return float(np.mean([r[key] for r in closed])) if closed else None

    out.update(
        win_rate=sum(r["net"] > 0 for r in closed) / len(closed) if closed else None,
        average_days=avg("age"),
        mean_mae_r=avg("mae"),
        mean_mfe_r=avg("mfe"),
        mean_r_ci=(
            baseline_qualification.clustered_mean_ci(
                [{"day": r["day"], "net_r": r["outcome_r"]} for r in closed], cfg
            )
            if cfg.get("bootstrap_unit") == "signal_session"
            else bootstrap([r["outcome_r"] for r in closed], cfg)
        ),
    )
    return out


def account_stats(marks, capital):
    if not marks:
        return {}
    values = np.array([capital, *[r["equity"] for r in marks]])
    duration = (date.fromisoformat(marks[-1]["day"]) - date.fromisoformat(marks[0]["day"])).days

    def cagr(ratio):
        return float(ratio ** (365.25 / duration) - 1) if duration and ratio > 0 else None

    return {
        "max_drawdown": float((1 - values / np.maximum.accumulate(values)).max()),
        "exposure_pct": float(np.mean([100 * r["invested"] / r["equity"] for r in marks])),
        "cagr": cagr(values[-1] / capital),
        "nifty_200_cagr": cagr(marks[-1]["benchmark"] / marks[0]["benchmark"]),
    }


def criteria(windows, cfg):
    rows = [r for group, _ in windows for r in group]
    stats = trade_stats(rows, cfg)
    n = len(windows)
    counts = [len(group) for group, _ in windows]
    good = [
        len(g) >= cfg["min_per_window"] and sum(r["outcome_r"] for r in g) > 0 for g, _ in windows
    ]
    fee_losses = any(r["net"] < 0 for r in rows)
    pf = stats["profit_factor"]
    unbounded = not fee_losses and any(r["net"] > 0 for r in rows)
    ci_low = stats["mean_r_ci"][0]
    checks = {}

    def check(name, value, op, target, passed):
        checks[name] = {"value": value, "operator": op, "threshold": target, "passed": bool(passed)}

    check("closed_oos", len(rows), ">=", cfg["min_closed"], len(rows) >= cfg["min_closed"])
    check(
        "closed_per_test_window",
        counts,
        "each >=",
        cfg["min_per_window"],
        bool(n and min(counts) >= cfg["min_per_window"]),
    )
    check(
        "mean_r",
        stats["expectancy_r"],
        ">",
        cfg["min_expectancy_r"],
        rows and stats["expectancy_r"] > cfg["min_expectancy_r"],
    )
    check(
        "bootstrap_lower_r",
        ci_low,
        ">",
        cfg["min_ci_lower_r"],
        ci_low is not None and ci_low > cfg["min_ci_lower_r"],
    )
    check(
        "profit_factor",
        "unbounded" if unbounded else pf,
        ">",
        cfg["min_profit_factor"],
        unbounded or (pf is not None and pf > cfg["min_profit_factor"]),
    )
    fraction = sum(good) / n if n else 0
    check(
        "positive_window_fraction",
        fraction,
        ">=",
        cfg["min_positive_fraction"],
        n and fraction >= cfg["min_positive_fraction"],
    )
    weak = sum(p and w > cfg["weak_regime_fraction"] for p, (_, w) in zip(good, windows))
    check(
        "positive_weak_windows",
        weak,
        ">=",
        cfg["min_weak_positive_windows"],
        weak >= cfg["min_weak_positive_windows"],
    )
    return {
        "qualified": all(c["passed"] for c in checks.values()),
        "criteria": checks,
        "aggregate": stats,
    }


def provenance(root, days, cfg):
    h = hashlib.sha256()
    unknown = defaultdict(list)
    for d in days:
        manifest = (root / "sessions" / str(d) / "manifest.json").read_bytes()
        ctx = read_context(root, d, verify_sources=True)
        market.verify(root, d)
        overlay = root / "sources" / "exchange-controls" / str(d) / "sources.json"
        if overlay.exists():
            h.update(overlay.read_bytes())
        h.update(manifest)
        h.update((root / "context" / str(d) / "context.sha256").read_bytes())
        for name, test in (
            ("surveillance", lambda c: c.get("surveillance") == "unknown"),
            ("price_band", lambda c: c.get("floor") is None or c.get("ceiling") is None),
            ("results_calendar", lambda c: c.get("earnings_coverage") == "unknown"),
        ):
            if any(test(c) for c in ctx.values()):
                unknown[name].append(d)
    for year in sorted({d.year for d in days} | {days[-1].year + 1}):
        for folder in (Path(__file__).with_name("calendars"), root / "calendars"):
            path = folder / f"{year}.json"
            if path.is_file():
                h.update(path.read_bytes())
    registry = root / "corporate_actions.json"
    if registry.is_file():
        h.update(registry.read_bytes())
    from .provenance import snapshot

    return {
        "rule_revision": cfg["revision"],
        "config_hash": cfg["config_hash"],
        "snapshot_hash": h.hexdigest(),
        **snapshot(root),
        "model_version": None,
        "prompt_version": None,
        "unavailable_filters": {k: history.ranges(v, days) for k, v in unknown.items()},
    }


def reserve_holdout(db, revision, experiment, days):
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT OR IGNORE INTO holdout_uses VALUES(?,?,?,?,?)",
            (revision, experiment, str(days[0]), str(days[-1]), "reserved"),
        )
        prior = db.execute("SELECT * FROM holdout_uses WHERE revision=?", (revision,)).fetchone()
        if prior["experiment"] != experiment:
            raise market.MarketError(
                f"Final holdout already reserved or used for this revision ({prior['experiment']})."
            )


def open_calls(db, run, day):
    return db.execute(
        """SELECT symbol, isin, min(day) AS day FROM calls WHERE run=? AND day<?
      AND status IN ('active','confirmed') GROUP BY symbol,isin""",
        (run, str(day)),
    ).fetchall()


def prepared_session(args):
    root, d, cfg = args
    regime, candidates, frames = load_session(root, d, cfg)
    members = market.read_members((root / "sessions" / str(d) / "members.csv").read_bytes())
    keys = dict(zip(members["isin"], members.symbol))
    return regime, candidates, {s: f.tail(2).copy() for s, f in frames.items()}, keys


def replay_window(db, root, days, books, *, unconstrained_runs=(), candidate_subset=None):
    from concurrent.futures import ProcessPoolExecutor
    from contextlib import nullcontext

    from .signals import attach_following

    pending = []
    for d in days:
        missing = [
            (key, config)
            for key, config in books
            if not db.execute("SELECT 1 FROM scans WHERE run=? AND day=?", (key, str(d))).fetchone()
        ]
        if missing:
            pending.append((d, missing))
    gaps = {}
    if not pending:
        return gaps
    with ProcessPoolExecutor(max_workers=4) if len(pending) >= 40 else nullcontext() as pool:
        for offset in range(0, len(pending), 12):
            batch = pending[offset : offset + 12]
            args = [(root, d, books[0][1]) for d, _ in batch]
            results = pool.map(prepared_session, args) if pool else map(prepared_session, args)
            for (d, missing), (regime, candidates, frames, keys) in zip(batch, results):
                missing = [item for item in missing if item[0] not in gaps]
                if not missing:
                    continue
                following = (c for key, _ in missing for c in open_calls(db, key, d))
                attach_following(root, d, books[0][1], following, frames, keys)
                for key, config in missing:
                    try:
                        options = {"unconstrained": True} if key in unconstrained_runs else {}
                        selected = (
                            candidate_subset(candidates, config) if candidate_subset else candidates
                        )
                        process(db, key, d, regime, selected, frames, config, **options)
                    except UnpricedPosition as exc:
                        gaps[key] = exc.detail
            if len(gaps) == len(books):
                break
    return gaps


def evaluate(root, partitions, all_days, cfg, parameters, locked, tests_only=False):
    proof = provenance(root, all_days, cfg)
    policy = None
    stats_cfg = cfg["validation"]
    if (
        parameters.get("mode") == "walk_forward"
        and cfg["config_hash"] == rules(revision="r2")["config_hash"]
    ):
        policy = baseline_qualification.validation_config()
        proof["qualification_config_sha256"] = policy["sha256"]
        stats_cfg = {**stats_cfg, **policy}
    identity = market.sha(
        json.dumps([proof, parameters, partitions], default=str, sort_keys=True).encode()
    )[:20]
    suffix = "-test-windows" if tests_only else ""
    dest = root / "backtests" / f"{cfg['revision']}-{identity}{suffix}.json"
    report = {
        "rules": cfg["revision"],
        "start": str(all_days[0]),
        "end": str(all_days[-1]),
        "folds": [],
        "provenance": proof,
        "parameters": parameters,
        "holdout_used": False,
    }
    if policy:
        report["qualification_config"] = policy
    pooled = {s: [] for s in STRATEGIES}
    variant = deepcopy(cfg)
    base_mode = cfg["filters"].get("earnings_blackout_mode", "calendar_days")
    other_mode = "holding_period" if base_mode == "calendar_days" else "calendar_days"
    variant["filters"]["earnings_blackout_mode"] = other_mode
    variant["revision"] = cfg["revision"] + "-blackout-" + other_mode
    report["blackout_comparison"] = {
        "baseline": base_mode,
        "alternative": other_mode,
        "test_windows": [],
    }
    with closing(journal(root / "journal.sqlite")) as db:
        if locked and not tests_only:
            used = db.execute(
                "SELECT experiment,status FROM holdout_uses WHERE revision=?", (cfg["revision"],)
            ).fetchone()
            if used and used["status"] == "used":
                prior = root / "backtests" / f"{cfg['revision']}-{used['experiment']}.json"
                saved = json.loads(prior.read_text())
                if (
                    saved["parameters"] == parameters
                    and saved["provenance"]["snapshot_hash"] == proof["snapshot_hash"]
                ):
                    return saved
                raise market.MarketError(
                    "This rule revision has already consumed its final holdout."
                )
        if dest.is_file():
            if locked and not tests_only:
                with db:
                    db.execute(
                        "UPDATE holdout_uses SET status='used' WHERE revision=? AND experiment=?",
                        (cfg["revision"], identity),
                    )
            return json.loads(dest.read_text())
        for name, days, train in partitions:
            if name == "holdout" and tests_only:
                continue
            run = f"bt-{cfg['revision']}-{identity}-{name}"
            paired = run + "-blackout"
            books = [(run, cfg)] if name == "holdout" else [(run, cfg), (paired, variant)]
            if locked and name == "holdout":
                reserve_holdout(db, cfg["revision"], identity, days)
            gaps = replay_window(db, root, days, books)
            if name != "holdout":
                pair = {"period": name, "portfolio_gaps": gaps}
                for key, mode in ((run, base_mode), (paired, other_mode)):
                    pair[mode] = run_report(db, key)["portfolio"]
                    pair[mode]["blackout_exclusions"] = db.execute(
                        "SELECT count(*) FROM calls WHERE run=? AND allocation='upcoming_results'",
                        (key,),
                    ).fetchone()[0]
                report["blackout_comparison"]["test_windows"].append(pair)
            stats = run_report(db, run)
            stats["corporate_actions"] = [
                {
                    "call_id": row["call_id"],
                    "day": row["day"],
                    "book": row["allocation"],
                    **json.loads(row["detail"]),
                }
                for row in db.execute(
                    """SELECT e.*,c.allocation FROM events e JOIN calls c ON c.id=e.call_id
                    WHERE c.run=? AND e.kind='corporate_action' ORDER BY e.id""",
                    (run,),
                )
            ]
            stats["valuation_gap"] = gaps.get(run)
            through = db.execute("SELECT max(day) FROM scans WHERE run=?", (run,)).fetchone()[0]
            stats["evaluated_through"] = through
            stats["complete"] = through == str(days[-1])
            stats["scheduled_sessions"] = len(days)
            executed = [d for d in days if through and str(d) <= through]
            stats["evaluated_sessions"] = len(executed)
            rows = [dict(r) for r in db.execute("SELECT * FROM calls WHERE run=?", (run,))]
            alloc = [r for r in rows if r["allocation"] == "portfolio"]
            affected = {a["call_id"] for a in stats["corporate_actions"]}
            stats["corporate_action_sensitivity"] = {
                "closed_book_including_actions": trade_stats(alloc, stats_cfg),
                "closed_book_excluding_affected_calls": trade_stats(
                    [r for r in alloc if r["id"] not in affected], stats_cfg
                ),
                "portfolio_calls_affected": sum(r["id"] in affected for r in alloc),
                "zero_fraction_cash_delta_inr": round(
                    sum(
                        a.get("sensitivity_zero_fraction_cash_delta", 0)
                        for a in stats["corporate_actions"]
                        if a["book"] == "portfolio"
                    ),
                    2,
                ),
                "method": "Same realized fills and sizing; excludes affected calls or sets their fractional proceeds to zero.",
            }
            cutoff = (
                baseline_qualification.maturity_cutoff(executed, cfg["exits"]["max_days"]) or ""
            )
            regimes = [
                json.loads(r[0])["enabled"]
                for r in db.execute("SELECT regime FROM scans WHERE run=?", (run,))
            ]
            weak = 1 - sum(regimes) / len(regimes) if regimes else None
            stats.update(
                period=name,
                start=str(days[0]),
                end=str(days[-1]),
                training=train,
                regime_fail_fraction=weak,
                maturity_cutoff=cutoff or None,
                mature_by_strategy={},
            )
            for book in ("portfolio", *STRATEGIES):
                subset = (
                    alloc if book == "portfolio" else [r for r in alloc if r["strategy"] == book]
                )
                result = trade_stats(subset, stats_cfg)
                marks = db.execute(
                    "SELECT * FROM account_marks WHERE run=? AND book=? ORDER BY day", (run, book)
                ).fetchall()
                result.update(account_stats(marks, cfg["risk"]["capital"]))
                if book == "portfolio":
                    stats["portfolio"] = result
                else:
                    stats["by_strategy"][book] = result
                    mature = [r for r in subset if r["status"] == "closed" and r["day"] <= cutoff]
                    stats["mature_by_strategy"][book] = trade_stats(mature, stats_cfg)
                    if name != "holdout":
                        pooled[book].append((mature, weak))
            stats["splits"] = {}
            for label, fn in (
                ("regime", lambda r: "pass" if json.loads(r["regime"])["enabled"] else "fail"),
                ("sector", lambda r: r["sector"]),
                ("year", lambda r: r["day"][:4]),
            ):
                groups = defaultdict(list)
                for r in rows:
                    groups[
                        (fn(r), "portfolio" if r["allocation"] == "portfolio" else "shadow")
                    ].append(r)
                stats["splits"][label] = {}
                for (value, book), group in groups.items():
                    stats["splits"][label].setdefault(value, {})[book] = trade_stats(
                        group, stats_cfg
                    )
            report["folds"].append(stats)
        tests = [f for f in report["folds"] if f["period"] != "holdout"]
        if policy:
            completed = {f["period"]: f["complete"] for f in tests}
            gates = {}
            for strategy, groups in pooled.items():
                measurements = [
                    {
                        "day": r["day"],
                        "net_r": r["outcome_r"],
                        "net_inr": r["net"],
                        "status": r["status"],
                        "mature": True,
                        "window": f["period"],
                    }
                    for f, (mature, _) in zip(tests, groups, strict=True)
                    for r in mature
                ]
                gate = baseline_qualification.qualification(measurements, completed, policy)
                counted = set(gate["counted_windows"])
                gate["aggregate"] = trade_stats(
                    [
                        r
                        for f, (mature, _) in zip(tests, groups, strict=True)
                        if f["period"] in counted
                        for r in mature
                    ],
                    stats_cfg,
                )
                gates[strategy] = gate
            report["qualification"] = gates
        else:
            report["qualification"] = {
                s: criteria(
                    [
                        (g if f["complete"] else [], weak or 0.0)
                        for f, (g, weak) in zip(tests, groups, strict=True)
                    ],
                    cfg["validation"],
                )
                for s, groups in pooled.items()
            }
        incomplete = [f["period"] for f in report["folds"] if not f["complete"]]
        report["coverage_status"] = "incomplete" if incomplete else "complete"
        for gate in report["qualification"].values():
            gate["criteria"]["incomplete_windows"] = {
                "value": incomplete,
                "operator": "count =",
                "threshold": 0,
                "passed": not incomplete,
            }
            gate["qualified"] = gate["qualified"] and not incomplete
        report["passes_measured_thresholds"] = {
            s: v["qualified"] for s, v in report["qualification"].items()
        }
        report["holdout_used"] = locked and not tests_only
        with market.staging(dest.parent) as tmp:
            file = tmp / "report.json"
            file.write_text(json.dumps(report, allow_nan=False, indent=2) + "\n")
            file.replace(dest)
            if locked and not tests_only:
                with db:
                    db.execute(
                        "UPDATE holdout_uses SET status='used' WHERE revision=? AND experiment=?",
                        (cfg["revision"], identity),
                    )
    return report
