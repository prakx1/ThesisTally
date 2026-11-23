"""Pre-registered daily dip setups within the ranked steady-momentum universe."""

import math

from .signals import Candidate

STRATEGY = "dip_in_steady_risers"


def candidates(ranking, context, cfg):
    """Apply the daily close setup to the already-ranked, dated Nifty 200 list."""
    specification = cfg["dip"]
    result = []
    for row in ranking.itertuples(index=False):
        if (
            row.rank > specification["eligible_count"]
            or not row.close > row.ema100
            or not row.rsi2 < specification["rsi_below"]
            or not math.isfinite(row.atr14)
            or row.atr14 <= 0
            or not math.isfinite(row.prior_high10)
        ):
            continue
        planned_stop = row.close - specification["stop_atr"] * row.atr14
        info = context[row.symbol]
        features = {
            "steady_rank": int(row.rank),
            "steady_score": float(row.score),
            "rsi2": float(row.rsi2),
            "ema100": float(row.ema100),
            "atr14": float(row.atr14),
            "median_turnover": float(row.turnover),
            "prior_high10": float(row.prior_high10),
            "planned_stop": float(planned_stop),
            "ranking_model_version": "steady-126-v1",
            "quarantined": info.get("quarantined", False),
            "surveillance": info["surveillance"],
            "earnings_in_days": info["earnings_in_days"],
            "earnings_coverage": info.get("earnings_coverage", "known"),
            "holding_blackout_days": info.get("holding_blackout_days"),
            "meeting_coverage_days": info.get("meeting_coverage_days", 30),
        }
        result.append(
            Candidate(
                row.symbol,
                row.isin,
                row.sector,
                STRATEGY,
                float(row.close),
                float(planned_stop),
                float(row.prior_high10),
                features,
            )
        )
    return result


def manage_open(db, call, day, bar, cfg, context):
    """The completed bar can create a next-open exit. Age counts the entry session."""
    from .journal import close_call, event, record_excursion

    record_excursion(db, call, day, bar.low, bar.high)
    last_session = call["age"] + 1 >= cfg["exits"]["max_days"]
    known_floor = context.get("floor")
    if bar.close <= call["trail"]:
        reason = "Closing stop"
    elif (
        last_session and known_floor is not None and bar.close > known_floor + 0.005 and bar.volume
    ):
        return close_call(
            db,
            call,
            day,
            bar.close * (1 - cfg["cost"]["slippage_bps"] / 10000),
            cfg,
            "Scheduled maximum holding close",
        )
    elif bar.close > call["target"]:
        reason = "Close above pre-signal 10-session high"
    elif last_session:
        reason = "Maximum holding period"
    else:
        return
    db.execute("UPDATE calls SET pending_exit=? WHERE id=?", (reason, call["id"]))
    event(db, call["id"], day, "exit_due", reason)
