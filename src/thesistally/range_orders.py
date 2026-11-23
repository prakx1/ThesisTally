"""Registered Range Bouncers close triggers for the shared paper execution engine."""

from .journal import close_call, event, record_excursion

TARGET = "Range target close"


def manage_open(db, call, day, bar, cfg, context):
    record_excursion(db, call, day, bar.low, bar.high)
    scheduled = call["age"] + 1 >= cfg["exits"]["max_days"]
    floor = context.get("floor")
    if bar.close < call["trail"]:
        reason = "Closing stop"
    elif scheduled:
        if floor is not None and bar.close > floor + 0.005 and bar.volume:
            slip = cfg["cost"]["slippage_bps"] / 10000
            return close_call(
                db, call, day, bar.close * (1 - slip), cfg, "Scheduled maximum holding close"
            )
        reason = "Maximum holding period"
    elif bar.close >= call["target"]:
        reason = TARGET
    else:
        return
    db.execute("UPDATE calls SET pending_exit=? WHERE id=?", (reason, call["id"]))
    event(db, call["id"], day, "exit_due", reason)
