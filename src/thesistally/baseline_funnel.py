"""Explain momentum attrition from immutable test-window journals."""

import json
import math
import sqlite3
from collections import Counter
from contextlib import closing

from .signals import signal_atr14


def stop_failures(entry, stop, atr, filters):
    """Report overlapping bound failures separately from the primary journal exclusion."""
    if atr is None or not math.isfinite(atr) or atr <= 0:
        return ["missing_atr"]
    distance = entry - stop
    tolerance = math.ulp(entry)
    failures = []
    if distance < entry * filters["min_stop_fraction"] - tolerance:
        failures.append("below_min_pct")
    if distance > entry * filters["max_stop_fraction"] + tolerance:
        failures.append("above_max_pct")
    if distance > atr * filters["max_stop_atr"] + tolerance:
        failures.append("above_max_atr")
    return failures


def momentum_funnel(windows, revision, cfg):
    """Sum signal/entry counts across fresh OOS accounts; preserve incomplete-book coverage."""
    detector = Counter()
    allocation = Counter()
    bound_combinations = Counter()
    dispositions = {k: Counter() for k in ("funded", "equal_risk")}
    opening_reasons = {k: Counter() for k in dispositions}
    candidates = []
    coverage = {}
    portfolio_run = f"{revision}-portfolio"
    equal_run = f"{revision}-equal-risk"

    for window, path in windows:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            scans = list(
                db.execute(
                    "SELECT day, regime FROM scans WHERE run=? ORDER BY day", (portfolio_run,)
                )
            )
            for scan in scans:
                detector.update(json.loads(scan["regime"])["detector_funnel"]["high_momentum"])
            coverage[window] = {
                "portfolio_sessions": len(scans),
                "equal_risk_sessions": db.execute(
                    "SELECT count(*) FROM scans WHERE run=?", (equal_run,)
                ).fetchone()[0],
            }
            plans = db.execute(
                """
                SELECT c.*, (SELECT e.detail FROM events e WHERE e.call_id=c.id
                  AND e.kind=c.status ORDER BY e.id DESC LIMIT 1) AS end_reason
                FROM calls c WHERE c.strategy='high_momentum' AND c.run IN (?,?)
                ORDER BY c.day, c.isin, c.run
            """,
                (portfolio_run, equal_run),
            )
            for c in plans:
                portfolio = c["run"] == portfolio_run
                if portfolio:
                    allocation[c["allocation"]] += 1
                    atr = signal_atr14(json.loads(c["features"]), cfg)
                    failures = stop_failures(c["entry"], c["stop"], atr, cfg["filters"])
                    if c["allocation"] == "stop_out_of_bounds":
                        if not failures:
                            raise ValueError(
                                f"Momentum stop exclusion disagrees for {window} #{c['id']}"
                            )
                        bound_combinations[" + ".join(failures)] += 1
                    candidates.append(
                        {
                            "window": window,
                            "day": c["day"],
                            "symbol": c["symbol"],
                            "isin": c["isin"],
                            "entry": c["entry"],
                            "base_low_stop": c["stop"],
                            "atr14": atr,
                            "stop_fraction": (c["entry"] - c["stop"]) / c["entry"],
                            "stop_atr": (c["entry"] - c["stop"]) / atr if atr and atr > 0 else None,
                            "stop_violations": failures,
                            "allocation": c["allocation"],
                            "status": c["status"],
                            "terminal_reason": c["end_reason"],
                        }
                    )
                    if c["allocation"] != "portfolio":
                        continue
                book = "funded" if portfolio else "equal_risk"
                counts = dispositions[book]
                counts["plans"] += 1
                counts[c["status"]] += 1
                counts["bought"] += int(c["buy"] is not None)
                if c["status"] in {"missed", "invalidated", "unmeasurable"}:
                    opening_reasons[book][f"{c['status']}: {c['end_reason']}"] += 1

    if detector["liquid"] - detector["invalid_geometry"] != sum(allocation.values()):
        raise ValueError(f"Unreconciled {revision} momentum detector and journal")
    if allocation["portfolio"] != dispositions["funded"]["plans"]:
        raise ValueError(f"Unreconciled {revision} momentum allocations")
    return {
        "scope": "out-of-sample test sessions reached by each book",
        "coverage": coverage,
        "detector": dict(detector),
        "allocation": dict(allocation),
        "signal_stop_exclusion_combinations": dict(bound_combinations),
        "dispositions": {b: dict(c) for b, c in dispositions.items()},
        "entry_or_censor_reasons": {b: dict(c) for b, c in opening_reasons.items()},
        "plans": candidates,
    }
