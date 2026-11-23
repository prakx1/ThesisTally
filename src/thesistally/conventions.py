"""Dated execution and market-evidence conventions authorised for Stage 3."""

import json
import math
import re
from functools import lru_cache

VERSION = "stage3-v1"
ENTRY_BAND_VERSION = "stage3-entry-v2"
FORWARD_EFFECTIVE = "2026-11-21"


def fill_stop_atr(cfg):
    for strategy in ("dip", "results_strategy"):
        if strategy in cfg:
            return cfg[strategy]["stop_atr"]
    return None


def tracks_holder_bonuses(cfg):
    return cfg.get("entry_band_convention") == ENTRY_BAND_VERSION and any(
        name in cfg for name in ("dip", "range_bouncers", "results_strategy")
    )


def opening_sale(open_price, volume, floor, slippage_bps, *, allow_unknown=True):
    if not volume or open_price <= 0 or (floor is None and not allow_unknown):
        return None, False
    if floor is not None and open_price <= floor + 0.005:
        return None, False
    return open_price * (1 - slippage_bps / 10000), floor is None


def unknown_opening_ceiling(open_price, ceiling):
    # A print beyond an obsolete range cannot establish a lock at the auction.
    return (
        ceiling is None
        or not math.isfinite(ceiling)
        or ceiling <= 0
        or open_price > ceiling + 0.005
    )


def purchase_block(open_price, volume, ceiling, *, allow_unknown=False):
    if (
        open_price is None
        or not math.isfinite(open_price)
        or open_price <= 0
        or volume is None
        or not math.isfinite(volume)
    ):
        return "Missing valid opening quote"
    if volume <= 0:
        return "No traded volume at open"
    if unknown_opening_ceiling(open_price, ceiling):
        return None if allow_unknown else "Unknown or inconsistent opening upper band"
    if open_price >= ceiling - 0.005:
        return "Verified upper band locked at open"
    return None


def opening_purchase(open_price, volume, ceiling, slippage_bps, *, allow_unknown=False):
    if purchase_block(open_price, volume, ceiling, allow_unknown=allow_unknown):
        return None
    return open_price * (1 + slippage_bps / 10000)


def cash_dividend_notices(info, *, allow_short_dividend=False):
    """Resolve a per-share dividend written with NSE's abbreviated AGM/Div label."""
    remaining = []
    resolved = {}
    for subject in info.get("other_actions", []):
        match = re.fullmatch(
            r"(?:annual general meeting|agm)\s*/\s*div\s*[-:]\s*rs\.?\s*"
            r"(\d+(?:\.\d+)?)\s*(?:/-)?\s*per\s+(?:equity\s+)?share",
            subject.strip(),
            re.IGNORECASE,
        )
        if allow_short_dividend and not match:
            match = re.fullmatch(
                r"int(?:erim)?\s+div\s*(?:[-:]\s*)?(?:re|rs)\.?\s*(\d+(?:\.\d+)?)\s*"
                r"(?:/-)?\s*per\s+sh(?:are)?\.?",
                subject.strip(),
                re.IGNORECASE,
            )
        if match:
            resolved[subject.strip().casefold()] = float(match[1])
        else:
            remaining.append(subject)
    if resolved:
        return {
            **info,
            "other_actions": remaining,
            "dividend": info.get("dividend", 0) + sum(resolved.values()),
        }
    return info


@lru_cache(maxsize=4)
def registry(path, version):
    return json.loads(path.read_bytes())["days"]


def dated(root, day):
    path = root / "sources" / "stage3-events.json"
    if not path.is_file():
        daily = root / "sources/forward-conventions" / f"{day}.json"
        if not daily.is_file():
            return {}
        packet = json.loads(daily.read_bytes())
        from . import market

        if (
            packet["version"] != VERSION
            or packet["day"] != str(day)
            or market.sha(json.dumps(packet["events"], sort_keys=True).encode())
            != packet["events_sha256"]
        ):
            raise market.MarketError(f"Invalid forward convention receipt for {day}")
        return packet["events"]
    return registry(path, path.stat().st_mtime_ns).get(str(day), {})


def apply(info, record):
    if record.get("unverified_jump") and not info.get("quarantined"):
        info.update(quarantined=True, quarantine_reason="unexplained_close_move")
    if (receipt := record.get("quarantine_release")) and info.get("quarantine_reason") in (
        None,
        "unexplained_close_move",
    ):
        info.update(quarantined=False, quarantine_release=receipt)
        info.pop("quarantine_reason", None)
    if rights := record.get("rights_issue"):
        subjects = {s.strip().casefold() for s in rights["subjects"]}
        info["other_actions"] = [
            s for s in info.get("other_actions", ()) if s.strip().casefold() not in subjects
        ]
        if not info["other_actions"] and info.get("quarantine_reason") == "unresolved_action":
            info.update(quarantined=False)
            info.pop("quarantine_reason", None)
        info["rights_issue"] = rights
    return info


def adjust_rights(db, call, day, issue):
    """Keep historical cash/buy risk immutable; rebase remaining order instructions."""
    from .journal import event

    prior_factor = call.get("rights_factor", 1.0)
    factor = issue["factor"] if issue else 1.0
    if issue:
        event(
            db,
            call["id"],
            day,
            "corporate_action",
            json.dumps(
                {
                    **issue,
                    "stage": "rights_lapsed"
                    if call["status"] == "active"
                    else "pending_entry_rebased",
                    "convention_version": VERSION,
                    "entitlement_cash": 0,
                    "subscribed": False,
                    "held_shares": call["remaining"],
                    "prior_stop": call["stop"] * prior_factor,
                    "prior_target": call["target"],
                    "prior_trail": call["trail"],
                },
                sort_keys=True,
            ),
        )
        call["target"] *= factor
        if call["trail"] is not None:
            call["trail"] *= factor
        if call["status"] == "confirmed":
            # Prior to a purchase, no entitlement exists and opening risk is in ex-rights units.
            original = {"entry": call["entry"], "stop": call["stop"], "features": call["features"]}
            event(
                db,
                call["id"],
                day,
                "planned_levels_before_rights",
                json.dumps(original, sort_keys=True),
            )
            call["entry"] *= factor
            call["stop"] *= factor
            features = json.loads(call["features"])
            for key in ("atr14", "atr"):
                if key in features and features[key] is not None:
                    features[key] *= factor
            call["features"] = json.dumps(features)
            db.execute(
                "UPDATE calls SET entry=?,stop=?,features=? WHERE id=?",
                (call["entry"], call["stop"], call["features"], call["id"]),
            )
        else:
            prior_factor *= factor
        db.execute(
            "UPDATE calls SET rights_factor=?,target=target*?,trail=? WHERE id=?",
            (prior_factor, factor, call["trail"], call["id"]),
        )
    call["rights_factor"] = prior_factor
    if call["status"] == "active" and prior_factor != 1:
        call["original_risk"] = call["buy"] - call["stop"]
        call["stop"] *= prior_factor
