"""Diagnostics for the paired r2 portfolio and independent equal-risk pilot."""

import json
from collections import Counter, defaultdict

from .evaluation import trade_stats
from .journal import STRATEGIES


def disposition(rows, events):
    statuses = Counter(c["status"] for c in rows)
    reasons = defaultdict(Counter)
    for call in rows:
        status = call["status"]
        if status in {"missed", "invalidated", "unmeasurable"}:
            reasons[status][events[call["id"], status]] += 1
    return {
        "plans": len(rows),
        "bought": sum(c["buy"] is not None for c in rows),
        "statuses": dict(statuses),
        "reasons": {key: dict(counts) for key, counts in reasons.items()},
    }


ALLOCATION_GATES = {"portfolio", "drawdown", "position_limit", "capital_or_risk"}


def mechanics_report(db, portfolio, edge, cfg):
    rows = list(db.execute("SELECT * FROM calls WHERE run IN (?,?) ORDER BY id", (portfolio, edge)))
    events = {
        (r["call_id"], r["kind"]): r["detail"]
        for r in db.execute(
            """SELECT e.call_id,e.kind,e.detail FROM events e JOIN calls c ON c.id=e.call_id
               WHERE c.run IN (?,?) AND e.kind IN ('missed','invalidated','unmeasurable')""",
            (portfolio, edge),
        )
    }
    stages = {s: Counter() for s in STRATEGIES}
    for (raw,) in db.execute("SELECT regime FROM scans WHERE run=?", (portfolio,)):
        for strategy, counts in json.loads(raw)["detector_funnel"].items():
            stages[strategy].update(counts)
    result = {}
    discrepancies = []
    for strategy in STRATEGIES:
        calls = [r for r in rows if r["strategy"] == strategy and r["run"] == portfolio]
        funded = [r for r in calls if r["allocation"] == "portfolio"]
        shadows = [r for r in calls if r["allocation"] != "portfolio"]
        independent = [r for r in rows if r["strategy"] == strategy and r["run"] == edge]
        valid = [r for r in calls if r["allocation"] in ALLOCATION_GATES]

        def signal_keys(calls):
            return {(r["day"], r["isin"], r["strategy"]) for r in calls}

        if signal_keys(valid) != signal_keys(independent):
            discrepancies.append(f"{strategy}: admissible plans differ between books")
        counts = stages[strategy]
        if counts["liquid"] != len(calls) + counts["invalid_geometry"]:
            discrepancies.append(f"{strategy}: liquidity count does not reconcile to plans")
        tickets = disposition(funded, events)
        attempted = len(funded) - tickets["statuses"].get("confirmed", 0)
        result[strategy] = {
            **dict(counts),
            "journaled": len(calls),
            "allocated": tickets,
            "miss_rate_among_allocated_with_open": tickets["statuses"].get("missed", 0) / attempted
            if attempted
            else None,
            "shadow_by_reason": dict(Counter(r["allocation"] for r in shadows)),
            "shadow": disposition(shadows, events),
            "unconstrained": {
                **disposition(independent, events),
                **trade_stats(independent, cfg["validation"]),
            },
        }
    return {
        "risk_per_independent_trade_inr": cfg["risk"]["capital"] * cfg["risk"]["fraction"],
        "edge_run": edge,
        "strategies": result,
        "account_events": [
            dict(r)
            for r in db.execute(
                "SELECT * FROM account_events WHERE run=? ORDER BY day", (portfolio,)
            )
        ],
        "discrepancies": discrepancies,
    }
