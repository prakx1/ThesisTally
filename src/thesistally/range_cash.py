"""Apply the signed, ex-date gross dividend amounts used by Range Bouncers."""

import json
import math

from . import market


def load_events(path, digest):
    raw = path.read_bytes()
    if market.sha(raw) != digest:
        raise market.MarketError("Range dividend evidence differs from its signal manifest.")
    events = {}
    for event in json.loads(raw)["events"]:
        key = event["day"], event["isin"]
        cash = event["cash_per_share"]
        if (
            key in events
            or not event["subjects"]
            or (cash is not None and (not math.isfinite(cash) or cash < 0))
        ):
            raise market.MarketError(f"Invalid ex-date gross dividend record {key}.")
        events[key] = event
    return events


def normalize(text):
    return " ".join(text.casefold().split())


def reconcile(info, day, isin, cash_events):
    """Replace, once, the cash-only NSE notices for this traded issue and date."""
    row = cash_events.get((str(day), isin))
    if not row or info.get("holder_dividend_accounted"):
        return info
    subjects = set(map(normalize, row["subjects"]))
    other = [s for s in info.get("other_actions", ()) if normalize(s) not in subjects]
    revised = {**info, "other_actions": other}
    cash = row["cash_per_share"]
    if cash is None:
        reason = f"Unverified ex-date cash per share for {isin} on {day}"
        if reason not in other:
            other.append(reason)
        # The shared journal quarantines unresolved events before credit or sale.
        revised["dividend"] = 0
    else:
        revised["dividend"] = cash
        if not other and info.get("quarantine_reason") == "unresolved_action":
            revised["quarantined"] = False
            revised.pop("quarantine_reason")
    return revised
