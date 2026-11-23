"""Stage 2 inference on completed out-of-sample signal cohorts."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

CONFIG_PATH = Path(__file__).with_name("validation") / "stage-2-v1.json"


def validation_config():
    raw = CONFIG_PATH.read_bytes()
    return {**json.loads(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def maturity_cutoff(sessions, holding_sessions):
    """Latest signal with a full subsequent holding window in the observed prefix."""
    return str(sessions[-holding_sessions - 1]) if len(sessions) > holding_sessions else None


def clustered_mean_ci(rows, cfg):
    """Resample entire signal dates and weight their trades equally in each draw."""
    buckets = defaultdict(list)
    for r in rows:
        buckets[r["day"]].append(r["net_r"])
    if len(buckets) < 2:
        return [None, None]
    by_day = [buckets[d] for d in sorted(buckets)]
    totals = np.array([sum(x) for x in by_day], dtype=float)
    counts = np.array([len(x) for x in by_day])
    rng = np.random.default_rng(cfg["bootstrap_seed"])
    n, draws = len(by_day), cfg["bootstrap_draws"]
    batch = max(1, min(256, 1_000_000 // n))
    means = []
    for start in range(0, draws, batch):
        indices = rng.integers(n, size=(min(batch, draws - start), n))
        means.extend(totals[indices].sum(axis=1) / counts[indices].sum(axis=1))
    tail = (1 - cfg["confidence"]) / 2
    return np.quantile(means, [tail, 1 - tail]).tolist()


def cash_profit_factor(rows):
    gains = sum(max(r["net_inr"], 0.0) for r in rows)
    losses = -sum(min(r["net_inr"], 0.0) for r in rows)
    if losses:
        return gains / losses
    return "unbounded" if gains else None


def qualification(rows, complete_by_window, cfg):
    """Poorly sampled windows cannot contribute either trades or a positive vote."""
    mature = [r for r in rows if r["mature"] and r["status"] == "closed"]
    by_window = {key: [] for key in complete_by_window}
    for r in mature:
        by_window[r["window"]].append(r)
    floor = cfg["min_per_counted_window"]
    counted = {
        key: trades
        for key, trades in by_window.items()
        if complete_by_window[key] and len(trades) >= floor
    }
    pooled = [r for group in counted.values() for r in group]
    n, nw = len(pooled), len(by_window)
    counts = {key: len(trades) for key, trades in by_window.items()}
    mean = sum(r["net_r"] for r in pooled) / n if n else None
    ci = clustered_mean_ci(pooled, cfg)
    pf = cash_profit_factor(pooled)
    positive = [key for key, group in counted.items() if sum(r["net_r"] for r in group) > 0]
    num, den = cfg["min_positive_window_fraction"]
    complete = bool(nw) and all(complete_by_window.values())

    def criterion(value, rule, threshold, passed):
        return {"value": value, "operator": rule, "threshold": threshold, "passed": bool(passed)}

    criteria = {
        "completed_replays": criterion(sum(complete_by_window.values()), "==", nw, complete),
        "closed_oos": criterion(n, ">=", cfg["min_mature_closed"], n >= cfg["min_mature_closed"]),
        "counted_window_sample": criterion(
            {k: counts[k] for k in counted},
            "each >=",
            floor,
            bool(counted) and all(counts[k] >= floor for k in counted),
        ),
        "mean_r": criterion(
            mean,
            ">",
            cfg["min_mean_net_r_exclusive"],
            mean is not None and mean > cfg["min_mean_net_r_exclusive"],
        ),
        "bootstrap_lower_r": criterion(
            ci[0],
            ">",
            cfg["min_ci_lower_r_exclusive"],
            ci[0] is not None and ci[0] > cfg["min_ci_lower_r_exclusive"],
        ),
        "profit_factor": criterion(
            pf,
            ">",
            cfg["min_cash_profit_factor_exclusive"],
            pf == "unbounded" or (pf is not None and pf > cfg["min_cash_profit_factor_exclusive"]),
        ),
        "positive_window_fraction": criterion(
            len(positive) / nw if nw else None,
            ">=",
            num / den,
            nw and den * len(positive) >= num * nw,
        ),
    }
    qualified = all(c["passed"] for c in criteria.values())
    if qualified:
        verdict = "edge"
    elif complete and n >= cfg["min_mature_closed"] and ci[1] is not None and ci[1] <= 0:
        verdict = "no edge"
    else:
        verdict = "not enough data"
    return {
        "qualified": qualified,
        "verdict": verdict,
        "criteria": criteria,
        "mature_closed_all": len(mature),
        "mature_closed_by_window": counts,
        "mature_closed_in_counted_windows": n,
        "counted_windows": list(counted),
        "positive_counted_windows": positive,
        "total_planned_windows": nw,
        "incomplete_windows": [w for w in by_window if not complete_by_window[w]],
        "signal_date_clusters": len({r["day"] for r in pooled}),
        "mean_net_r_ci90": ci,
    }
