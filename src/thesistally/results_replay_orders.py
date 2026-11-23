"""Registered Strategy D orders. The shared journal owns sizing, fills, and holdings."""

import hashlib
import json
import math

from . import conventions
from .journal import close_call, event, record_excursion
from .signals import Candidate, rules

STRATEGY = "post_earnings_momentum"


def settings(horizon, book):
    if horizon not in (20, 40) or book not in ("equal_risk", "funded"):
        raise ValueError("Unknown Strategy D rule")
    cfg = rules(revision="r2")
    cfg.update(
        version=f"D{horizon}-{book}",
        base_sha256=cfg["config_hash"],
        execution_convention=conventions.VERSION,
        entry_band_convention=conventions.ENTRY_BAND_VERSION,
        book_strategies=[STRATEGY],
        results_strategy={"stop_atr": 3, "gate": book == "funded"},
    )
    cfg["filters"]["max_stop_atr"] = 3
    cfg["exits"]["max_days"] = horizon
    cfg["config_hash"] = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    cfg["revision"] = f"{cfg['version']}-{cfg['config_hash'][:12]}"
    return cfg


def candidates(rows, members, controls):
    orders = []
    for r in rows.itertuples(index=False):
        if (
            members.get(r.isin) != r.symbol
            or not r.usable_session < r.day
            or not all(math.isfinite(x) for x in (r.close, r.atr14, r.sue, r.reaction))
            or min(r.close, r.atr14, r.reaction) <= 0
        ):
            raise ValueError(f"Unusable D order: {r.day}/{r.isin}")
        control = controls[r.symbol]
        features = {
            "sue": r.sue,
            "reaction": r.reaction,
            "atr14": r.atr14,
            "filing_id": r.filing_id,
            "usable_session": r.usable_session,
            "surveillance": control["surveillance"],
            "quarantined": control.get("quarantined", False),
            "ranking_model_version": "sue-descending-isin-v1",
            "planned_stop": r.close - 3 * r.atr14,
            "has_profit_target": False,
        }
        # Shared journal requires a numeric target column. Zero means no target for D;
        # no D exit or freeze dispatch reads that field.
        orders.append(
            Candidate(
                r.symbol,
                r.isin,
                r.sector,
                STRATEGY,
                r.close,
                features["planned_stop"],
                0.0,
                features,
            )
        )
    return orders


def manage_open(db, call, day, bar, cfg, context):
    record_excursion(db, call, day, bar.low, bar.high)
    if bar.close < call["trail"]:
        reason = "Closing stop"
    elif call["age"] + 1 >= cfg["exits"]["max_days"]:
        floor = context.get("floor")
        if floor is not None and bar.close > floor + 0.005 and bar.volume:
            return close_call(
                db,
                call,
                day,
                bar.close * (1 - cfg["cost"]["slippage_bps"] / 10000),
                cfg,
                "Scheduled maximum holding close",
            )
        reason = "Maximum holding period"
    else:
        return
    db.execute("UPDATE calls SET pending_exit=? WHERE id=?", (reason, call["id"]))
    event(db, call["id"], day, "exit_due", reason)
