"""Exploratory slices and preregistered, paired walk-forward comparisons of equal-risk calls."""

import math
from collections import Counter

import numpy as np
import pandas as pd

from .baseline_qualification import clustered_mean_ci, qualification, validation_config

LABEL = "EXPLORATORY — hypothesis generation only, not evidence"
DIMENSIONS = (
    "stock",
    "sector",
    "size_tercile",
    "volatility_tercile",
    "liquidity_tercile",
    "gate",
    "vix_bucket",
    "breadth_bucket",
    "year",
    "signal_rank_quintile",
    "stop_distance_bucket",
)
DOMAINS = {
    "size_tercile": ("low", "middle", "high", "unknown"),
    "volatility_tercile": ("low", "middle", "high", "unknown"),
    "liquidity_tercile": ("low", "middle", "high", "unknown"),
    "vix_bucket": ("<15", "15–22", ">22", "unknown"),
    "breadth_bucket": ("<40%", "40–60%", ">60%", "unknown"),
    "stop_distance_bucket": ("<5%", "5–<7%", "≥7%", "unknown"),
    "gate": ("on", "off"),
    "signal_rank_quintile": ("Q1", "Q2", "Q3", "Q4", "Q5", "unknown"),
}
BLOCK_SESSIONS = 20
NULL_SEED = 20261122
NULL_DRAWS = 10000
FDR = 0.05


def labels(values):
    return values.astype("string").fillna("unknown")


def explore(frame, *, years=None, dimensions=None):
    """Summarize the original counted sample. One cell is one examined slice."""
    cfg = validation_config()
    dimensions = tuple(DIMENSIONS if dimensions is None else dimensions)
    if (
        not dimensions
        or len(set(dimensions)) != len(dimensions)
        or set(dimensions) - set(DIMENSIONS)
    ):
        raise ValueError("Choose distinct registered discovery dimensions")
    counted = frame.loc[frame["counted"] & frame.mature & frame.status.eq("closed")]
    if years is not None:
        counted = counted.loc[counted.year.isin(years)]
    if not np.isfinite(counted.net_r).all():
        raise ValueError("A counted trade has a missing after-cost outcome")
    cells = []
    for field in dimensions:
        groups = dict(tuple(counted.groupby(labels(counted[field]), sort=False)))
        domain = DOMAINS.get(field, ())
        if field == "year":
            domain = tuple(str(y) for y in sorted(frame.year.unique() if years is None else years))
        values = tuple(domain) + tuple(sorted(set(groups) - set(domain)))
        if not values:
            values = ("unknown",)
        for value in values:
            group = groups.get(value, counted.iloc[0:0])
            records = group[["day", "net_r"]].to_dict("records")
            ci = clustered_mean_ci(records, cfg)
            cells.append(
                {
                    "label": LABEL,
                    "dimension": field,
                    "value": str(value),
                    "trades": len(group),
                    "signal_dates": int(group.day.nunique()),
                    "mean_net_r": float(group.net_r.mean()) if records else None,
                    "ci90": ci,
                }
            )
    return {
        "label": LABEL,
        "trades": len(counted),
        "years": sorted(map(int, counted.year.unique())),
        "slices_examined": len(cells),
        "populated_slices": sum(r["trades"] > 0 for r in cells),
        "ci": "90% percentile bootstrap, entire signal dates resampled; trade-weighted mean",
        "validation_config_sha256": cfg["sha256"],
        "slices": cells,
    }


def validate_card(card):
    if set(card) - {"id", "strategy", "description", "filters", "selector"}:
        raise ValueError("Unexpected hypothesis card field")
    if any(
        not isinstance(card.get(k), str) or not card[k].strip()
        for k in ("id", "strategy", "description")
    ):
        raise ValueError("Each hypothesis needs an ID, strategy and description")
    filters, selector = card.get("filters", []), card.get("selector")
    if not isinstance(filters, list) or (not filters and not selector):
        raise ValueError("Each hypothesis requires at least one selection rule")
    allowed = set(DIMENSIONS) - {"year"}
    filtered_fields = set()
    for term in filters:
        if (
            not isinstance(term, dict)
            or set(term) != {"field", "values"}
            or term["field"] not in allowed
        ):
            raise ValueError("Unknown static filter")
        values = term["values"]
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(v, str) or not v or v == "unknown" for v in values)
            or len(set(values)) != len(values)
            or term["field"] in filtered_fields
        ):
            raise ValueError("Invalid or duplicate static hypothesis filter")
        domain = DOMAINS.get(term["field"])
        if domain is not None and set(values) - set(domain):
            raise ValueError(f"Unregistered bucket in {term['field']}")
        filtered_fields.add(term["field"])
    if selector is not None:
        keys = {"field", "top_fraction", "min_trades", "lookback_years"}
        if (
            not isinstance(selector, dict)
            or set(selector) != keys
            or selector["field"] not in allowed
        ):
            raise ValueError("Unknown historical selector")
        if (
            type(selector["min_trades"]) is not int
            or selector["min_trades"] < 1
            or type(selector["lookback_years"]) is not int
            or selector["lookback_years"] < 1
            or type(selector["top_fraction"]) not in (int, float)
            or not 0 < selector["top_fraction"] <= 1
        ):
            raise ValueError("Invalid historical sample floor, lookback or top fraction")


def select_window(history, window_rows, start, card):
    """Return an execution mask and a reproducible selection receipt for the opening of the window."""
    mask = pd.Series(True, index=window_rows.index)
    rules = card.get("filters", [])
    for term in rules:
        mask &= labels(window_rows[term["field"]]).isin(term["values"])
    receipt = {"as_of_exclusive": start, "signal_time_predicates": rules}
    selector = card.get("selector")
    if not selector:
        return mask, receipt
    left = (
        (pd.Timestamp(start) - pd.DateOffset(years=selector["lookback_years"])).date().isoformat()
    )
    eligible = history.loc[
        history.counted
        & history.mature
        & history.status.eq("closed")
        & history.day.ge(left)
        & history.day.lt(start)
        & history.closed_day.lt(start)
        & history.full_horizon_day.lt(start)
    ]
    # Membership in this historical ranking is conditional on the same static predicates.
    for term in rules:
        eligible = eligible.loc[labels(eligible[term["field"]]).isin(term["values"])]
    field = selector["field"]
    known = eligible.loc[labels(eligible[field]).ne("unknown")].copy()
    groups = known.groupby(labels(known[field]), sort=True).net_r.agg(["size", "mean"])
    groups = groups[groups["size"].ge(selector["min_trades"])]
    # groupby sorted the labels first. Stable sort preserves lexicographic ties.
    groups = groups.sort_values("mean", ascending=False, kind="stable")
    n = math.ceil(len(groups) * selector["top_fraction"])
    chosen = list(groups.index[:n])
    receipt.update(
        history_start=left,
        historical_trades=len(known),
        groups_meeting_floor=len(groups),
        eligible_history_last_settlement=max(known.closed_day, default=None),
        eligible_history_last_horizon=max(known.full_horizon_day, default=None),
        selected_labels=chosen,
        selection_field=field,
        selected_history=[
            {"value": str(k), "trades": int(r["size"]), "mean_net_r": float(r["mean"])}
            for k, r in groups.iloc[:n].iterrows()
        ],
    )
    return mask & labels(window_rows[field]).isin(chosen), receipt


def trade_summary(rows):
    return {
        "trades": len(rows),
        "signal_dates": int(rows.day.nunique()),
        "mean_net_r": float(rows.net_r.mean()) if len(rows) else None,
    }


def block_contributions(rows, selected, sessions):
    """Influence of each fixed block on the difference in two overlapping trade means."""
    offsets = {day: i // BLOCK_SESSIONS for i, day in enumerate(sessions)}
    if any(day not in offsets for day in rows.day):
        raise ValueError("Trade outside the scheduled exchange sessions")
    block = rows.day.map(offsets)
    keep = selected.reindex(rows.index)
    included = rows[keep]
    # Center both terms separately. The same observed call and block reside on both sides.
    influence = -(rows.net_r - rows.net_r.mean()) / len(rows)
    influence.loc[keep] += (included.net_r - included.net_r.mean()) / len(included)
    blocks = influence.groupby(block).sum()
    k = len(blocks)
    if k < 2 or block.loc[keep].nunique() < 2:
        return None
    correction = math.sqrt(k / (k - 1))
    return {sessions[i * BLOCK_SESSIONS]: float(v * correction) for i, v in blocks.items()}


def coverage_for_selection(plans, receipt, meta):
    """Bound subgroup data loss when a blocked order has an unobservable filter value."""
    if meta.get("planned_entries") is None:
        return {
            "available": False,
            "reason": "Parent book predates the planned-entry coverage census",
        }
    if plans is None:
        raise ValueError(
            "A strategy with an archived planned-entry gate requires its complete candidate census"
        )
    blocked = plans.blocked_data
    if blocked.isna().any() or not pd.api.types.is_bool_dtype(blocked):
        raise ValueError("Missing or non-boolean planned-order data disposition")
    if len(plans) != meta["planned_entries"] or int(blocked.sum()) != meta["blocked_data"]:
        raise ValueError("Candidate census and archived entry coverage differ")
    definite = pd.Series(True, index=plans.index)
    possible = definite.copy()
    rules = list(receipt["signal_time_predicates"])
    if "selection_field" in receipt:
        rules.append({"field": receipt["selection_field"], "values": receipt["selected_labels"]})
    for rule in rules:
        values = labels(plans[rule["field"]])
        passes = values.isin(rule["values"])
        definite &= passes
        possible &= passes | (values.eq("unknown") & bool(rule["values"]))
    uncertain = possible & ~definite
    known = int(definite.sum())
    known_blocked = int((blocked & definite).sum())
    possible_blocked = int((blocked & uncertain).sum())
    # The worst case includes every unresolved blocked candidate and excludes every
    # unresolved nonblocked one. It cannot give a pass by hiding an unknown order.
    numerator = known_blocked + possible_blocked
    denominator = known + possible_blocked
    return {
        "available": True,
        "parent_planned": len(plans),
        "parent_data_blocked": int(blocked.sum()),
        "selected_planned": known,
        "selected_data_blocked": known_blocked,
        "uncertain_membership": int(uncertain.sum()),
        "possibly_selected_data_blocked": possible_blocked,
        "worst_case_blocked": numerator,
        "worst_case_planned": denominator,
        "worst_case_fraction": numerator / denominator if denominator else None,
        "coverage_eligible": 10 * numerator <= denominator,
    }


def validation_detail(frame, windows, card, cfg, first_year, plans):
    validation_windows = {
        w: meta
        for w, meta in sorted(windows.items(), key=lambda item: item[1]["start"])
        if int(meta["start"][:4]) >= first_year
    }
    if not validation_windows:
        raise ValueError("The registered validation schedule is empty")
    completion = {}
    selected_records, outcomes, comparable_deltas, blocks = [], [], [], []
    passed_windows = 0
    for w, meta in validation_windows.items():
        days = meta["sessions"]
        if (
            list(days) != sorted(set(days))
            or not days
            or (days[0], days[-1]) != (meta["start"], meta["end"])
        ):
            raise ValueError(f"Invalid validation session schedule for {w}")
        within = frame.loc[frame.window.eq(w) & frame.mature & frame.status.eq("closed")]
        if not within.day.between(days[0], days[-1]).all():
            raise ValueError("Trade/window mismatch")
        if not np.isfinite(within[["net_r", "net_inr"]].to_numpy()).all():
            raise ValueError("Unmeasurable trade inside closed, mature sample")
        mask, receipt = select_window(frame, within, days[0], card)
        filtered = within[mask]
        complete = bool(meta["complete"])
        window_plans = None if plans is None else plans.loc[plans.window.eq(w)]
        subgroup_coverage = coverage_for_selection(window_plans, receipt, meta)
        covered = bool(
            meta["coverage_eligible"] and subgroup_coverage.get("coverage_eligible", True)
        )
        if covered:
            completion[w] = complete
            selected_records.extend(
                filtered[["day", "window", "mature", "status", "net_r", "net_inr"]].to_dict(
                    "records"
                )
            )
        delta = float(filtered.net_r.mean() - within.net_r.mean()) if len(filtered) else None
        enough = bool(complete and covered and len(filtered) >= cfg["min_per_counted_window"])
        won = bool(enough and delta > 0)
        if enough:
            comparable_deltas.append(delta)
            blocks.append(block_contributions(within, mask, days))
        passed_windows += int(won)
        outcomes.append(
            {
                "window": w,
                "start": days[0],
                "end": days[-1],
                "complete": complete,
                "coverage_eligible": covered,
                "parent_coverage_eligible": bool(meta["coverage_eligible"]),
                "filtered_entry_coverage": subgroup_coverage,
                "meets_comparison_sample": enough,
                "filtered": trade_summary(filtered),
                "unfiltered": trade_summary(within),
                "difference_r": delta,
                "won": won,
                "selection": receipt,
            }
        )
    absolute = qualification(selected_records, completion, cfg)
    completed = sum(v["complete"] for v in validation_windows.values())
    absolute["criteria"]["completed_replays"].update(
        value=completed,
        threshold=len(validation_windows),
        passed=completed == len(validation_windows),
    )
    if completed != len(validation_windows):
        absolute.update(qualified=False, verdict="not enough data")
    absolute["coverage_excluded_windows"] = [
        row["window"] for row in outcomes if not row["coverage_eligible"]
    ]
    absolute["incomplete_windows"] = [w for w, v in validation_windows.items() if not v["complete"]]
    required = math.ceil(2 * len(outcomes) / 3)
    difference = float(np.mean(comparable_deltas)) if comparable_deltas else None
    influences = Counter()
    estimable = len(blocks) >= 2 and all(b is not None for b in blocks)
    if estimable:
        for block in blocks:
            influences.update({k: v / len(blocks) for k, v in block.items()})
        estimable = any(abs(v) > 1e-12 for v in influences.values())
    return {
        "id": card["id"],
        "strategy": card["strategy"],
        "description": card["description"],
        "windows": outcomes,
        "qualification": absolute,
        "scheduled_windows": len(outcomes),
        "windows_won": passed_windows,
        "windows_required": required,
        "breadth_pass": passed_windows >= required,
        "comparable_windows": len(blocks),
        "paired_difference_r": difference,
        "estimable": bool(estimable),
    }, influences


def benjamini_hochberg(p_values):
    """Adjusted p-values in original order, retaining untestable cards as p=1."""
    values = np.asarray(p_values, dtype=float)
    if (
        values.ndim != 1
        or not len(values)
        or not np.isfinite(values).all()
        or (values < 0).any()
        or (values > 1).any()
    ):
        raise ValueError("Each registered card requires a probability in [0, 1]")
    order = np.argsort(values, kind="stable")
    ranks = np.arange(1, len(values) + 1)
    result = np.empty(len(values))
    result[order] = np.minimum(
        1, np.minimum.accumulate((values[order] * len(values) / ranks)[::-1])[::-1]
    )
    return result


def validate_family(
    frames, windows_by_strategy, cards, *, plans_by_strategy=None, first_year=2021, alpha=FDR
):
    """Evaluate all preregistered beneficiaries together. No per-card, uncorrected pass."""
    if not 1 <= len(cards) <= 10 or not 0 < alpha < 1:
        raise ValueError("Validation requires 1–10 registered cards and a valid family FDR")
    if len({c["id"] for c in cards}) != len(cards):
        raise ValueError("Duplicate hypothesis ID")
    cfg = validation_config()
    results, coefficients = [], []
    grids = {}
    for card in cards:
        validate_card(card)
        sid = card["strategy"]
        frame = frames[sid].reset_index(drop=True)
        for meta in windows_by_strategy[sid].values():
            start = meta["start"]
            if int(start[:4]) >= first_year:
                sessions = tuple(meta["sessions"])
                if start in grids and grids[start] != sessions:
                    raise ValueError("Paired strategies must use the same dated exchange calendar")
                grids[start] = sessions
        plans = (plans_by_strategy or {}).get(sid)
        result, coef = validation_detail(
            frame, windows_by_strategy[sid], card, cfg, first_year, plans
        )
        results.append(result)
        coefficients.append(coef)
    schedules = [tuple(w["start"] for w in r["windows"]) for r in results]
    if len(set(schedules)) != 1:
        raise ValueError("All cards must face the same scheduled validation windows")
    keys = sorted({k for c in coefficients for k in c})
    vectors = np.array([[c.get(k, 0) for k in keys] for c in coefficients], dtype=float)
    rng = np.random.default_rng(NULL_SEED)
    signs = rng.integers(0, 2, size=(NULL_DRAWS, len(keys)), dtype=np.int8) * 2 - 1
    null = signs @ vectors.T
    marginal, simulated_p = [], np.ones(null.shape)
    for j, row in enumerate(results):
        if row["estimable"]:
            effects = null[:, j]
            d = row["paired_difference_r"]
            p = (1 + int(np.count_nonzero(effects >= d))) / (NULL_DRAWS + 1)
            bounds = np.quantile(effects, [0.05, 0.95])
            row["paired_difference_ci90"] = [float(d - bounds[1]), float(d - bounds[0])]
            # Rank each complete-null draw among the joint simulations. Count ties conservatively.
            simulated_p[:, j] = (
                NULL_DRAWS - np.searchsorted(np.sort(effects), effects, side="left")
            ) / NULL_DRAWS
        else:
            p = 1.0
            row["paired_difference_ci90"] = [None, None]
        row["p_one_sided"] = p
        marginal.append(p)
    for row, q in zip(results, benjamini_hochberg(marginal), strict=True):
        row["p_bh"] = float(q)
        row["bh_pass"] = bool(row["estimable"] and q <= alpha)
        row["passed"] = bool(
            row["qualification"]["qualified"] and row["breadth_pass"] and row["bh_pass"]
        )
    cutoffs = alpha * np.arange(1, len(cards) + 1) / len(cards)
    ranks = np.arange(1, len(cards) + 1)
    bh_counts = np.max(np.where(np.sort(simulated_p, axis=1) <= cutoffs, ranks, 0), axis=1)
    return {
        "first_validation_year": first_year,
        "family_size": len(cards),
        "alpha": alpha,
        "block_sessions": BLOCK_SESSIONS,
        "null_draws": NULL_DRAWS,
        "null_seed": NULL_SEED,
        "qualified_thresholds": cfg,
        "hypotheses": results,
        "passed_ids": [r["id"] for r in results if r["passed"]],
        "global_null": {
            "expected_unadjusted": float(np.mean((simulated_p <= alpha).sum(axis=1))),
            "expected_bh_discoveries": float(np.mean(bh_counts)),
            "probability_any_bh_discovery": float(np.mean(bh_counts > 0)),
            "bh_count_95th_percentile": int(np.quantile(bh_counts, 0.95, method="higher")),
            "expected_full_passes_upper_bound": float(np.mean(bh_counts)),
        },
    }
