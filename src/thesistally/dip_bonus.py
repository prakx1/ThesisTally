"""Settlement of bonus shares while the holder can still sell the original shares."""

import json
import math
from functools import lru_cache

from . import corporate
from .market import MarketError

SCHEMA = """
CREATE TABLE IF NOT EXISTS dip_bonus_tranches (
    call_id INTEGER NOT NULL REFERENCES calls(id), ex_date TEXT NOT NULL, state TEXT NOT NULL,
    PRIMARY KEY(call_id, ex_date)
);
"""


@lru_cache(maxsize=8)
def parse(raw):
    records = {}
    for term in json.loads(raw):
        isin, day = term["predecessor_isin"], term["effective"]
        leg = term["bonus"]
        if (
            term["successor_isin"] != isin
            or term["type"] != "bonus"
            or (isin, day) in records
            or not day <= leg["credited"] <= leg["tradable"]
            or min(leg["new_shares"], leg["old_shares"]) <= 0
            or not leg["subjects"]
        ):
            raise MarketError("Invalid same-ISIN bonus entitlement")
        records[isin, day] = term
    return records


def register(root):
    path = root / "dip-bonus-actions.json"
    return parse(path.read_bytes()) if path.is_file() else {}


def attach(context, term, previous):
    from .steady_actions import with_holder_dividend

    context.update(with_holder_dividend(context, term))
    subjects = {s.strip().casefold() for s in term["bonus"]["subjects"]}
    context["other_actions"] = [
        s for s in context.get("other_actions", ()) if s.strip().casefold() not in subjects
    ]
    if not context["other_actions"] and context.get("quarantine_reason") == "unresolved_action":
        context["quarantined"] = False
        context.pop("quarantine_reason")
    context["bonus_issue"] = term
    context["bonus_prior_close"] = previous["close"] if previous else None


def tranches(db, cid):
    return [
        json.loads(r[0])
        for r in db.execute(
            "SELECT state FROM dip_bonus_tranches WHERE call_id=? ORDER BY ex_date", (cid,)
        )
    ]


def persist(db, c, t):
    db.execute(
        "INSERT INTO dip_bonus_tranches VALUES(?,?,?) "
        "ON CONFLICT(call_id,ex_date) DO UPDATE SET state=excluded.state",
        (c["id"], t["ex_date"], json.dumps(t)),
    )


def emit(db, c, day, t, stage, **fields):
    from .journal import event

    leg = t["leg"]
    event(
        db,
        c["id"],
        day,
        "corporate_action",
        json.dumps(
            {
                "stage": stage,
                "isin": c["isin"],
                "ex_date": t["ex_date"],
                "ratio": [leg["new_shares"], leg["old_shares"]],
                "allotted": leg.get("allotted"),
                "credited": leg["credited"],
                "credit_evidence_basis": leg.get("credit_evidence_basis"),
                "tradable": leg["tradable"],
                "cash": 0,
                "source_url": leg["source_url"],
                "listing_source": leg["listing_source"],
                "convention_version": corporate.VERSION,
                **fields,
            },
            sort_keys=True,
        ),
    )


def issue(db, c, day, term, prior_close):
    from .journal import UnpricedPosition

    leg = term["bonus"]
    den, num = leg["old_shares"], leg["new_shares"]
    scale = den / (den + num)
    tranche = {"ex_date": day, "leg": leg, "factor": scale}
    if c["status"] == "confirmed":
        # An opening order has no entitlement. Keep its cash/risk reservation in pre-ex rupees.
        features = json.loads(c["features"])
        emit(
            db,
            c,
            day,
            tranche,
            "bonus_pending_rebased",
            original_features=features,
            original_entry=c["entry"],
            original_stop=c["stop"],
            original_size=c["size"],
            original_target=c["target"],
            factor=scale,
        )
        for field in ("atr", "atr14", "prior_high10"):
            if features.get(field) is not None:
                features[field] *= scale
        c.update(
            entry=c["entry"] * scale,
            stop=c["stop"] * scale,
            target=c["target"] * scale,
            size=math.floor(c["size"] * (den + num) / den),
            features=json.dumps(features),
        )
        db.execute(
            "UPDATE calls SET entry=?,stop=?,target=?,size=?,features=? WHERE id=?",
            (c["entry"], c["stop"], c["target"], c["size"], c["features"], c["id"]),
        )
        return
    if not c["remaining"]:
        return
    if prior_close is None or not math.isfinite(prior_close) or prior_close <= 0:
        raise UnpricedPosition(c, day, "Missing verified pre-bonus close for restricted shares")
    whole, rem = divmod(c["remaining"] * num, den)
    tranche.update(
        whole=whole,
        fraction=rem / den,
        mark=prior_close * scale,
        credited=False,
        delivered=False,
        paid=not rem,
    )
    persist(db, c, tranche)
    emit(
        db,
        c,
        day,
        tranche,
        "bonus_entitlement",
        original_qty=c["remaining"],
        whole=whole,
        fraction=rem / den,
        factor=scale,
        restricted_mark=tranche["mark"],
    )


def marked(c, rows):
    call = dict(c)
    factor = math.prod(t["factor"] for t in rows)
    if factor != 1:
        for key in ("buy", "stop", "target", "trail"):
            call[key] *= factor
        if "original_risk" in call:
            call["original_risk"] *= factor
    return call


def value(db, call, marked_value):
    rows = tranches(db, call["id"])
    if not rows:
        return call, marked_value
    effective = marked(call, rows)
    for t in rows:
        locked = 0 if t["delivered"] else t["whole"]
        unpaid = 0 if t["paid"] else t["fraction"]
        effective["remaining"] += locked
        marked_value += (locked + unpaid) * t["mark"]
    return effective, marked_value


def prepare(db, c, day, bar, ctx):
    """Settle only entitlements existing at this session. Keep raw buy/risk in the journal."""
    from .journal import UnpricedPosition, finish

    c = dict(c)
    if term := ctx.get("bonus_issue"):
        issue(db, c, day, term, ctx.get("bonus_prior_close"))
    rows = tranches(db, c["id"])
    if not rows:
        return c, False
    for t in rows:
        leg = t["leg"]
        if day >= leg["credited"] and not t["credited"]:
            t["credited"] = True
            emit(db, c, day, t, "bonus_credited", whole=t["whole"])
        traded = getattr(bar, "volume", 0) > 0 and getattr(bar, "open", 0) > 0
        if day >= leg["tradable"] and traded and not t["delivered"]:
            c["remaining"] += t["whole"]
            db.execute("UPDATE calls SET remaining=? WHERE id=?", (c["remaining"], c["id"]))
            t["delivered"] = True
            emit(db, c, day, t, "bonus_tradable", whole=t["whole"])
        if not t["paid"] and day >= leg["tradable"]:
            if traded and "reference" not in t and not ctx.get("quarantined"):
                t.update(reference=float(bar.close), reference_day=day)
            if day >= leg.get("cash_date", leg["tradable"]):
                price = leg.get("fraction_price", t.get("reference"))
                if price is None or not math.isfinite(price) or price <= 0:
                    raise UnpricedPosition(c, day, "Missing verified bonus fractional cash price")
                amount = corporate.cash(t["fraction"], price)
                db.execute(
                    "INSERT INTO corporate_cash VALUES(?,?,?,?)", (c["id"], day, c["isin"], amount)
                )
                t["paid"] = True
                emit(
                    db,
                    c,
                    day,
                    t,
                    "bonus_fraction_paid",
                    cash=amount,
                    fraction=t["fraction"],
                    fraction_unit_price=price,
                    first_close_fallback="fraction_price" not in leg,
                    reference_day=t.get("reference_day"),
                    source_url=leg.get("fraction_source", leg["source_url"]),
                )
        persist(db, c, t)
    if not c["remaining"] and c["pending_exit"]:
        if all(t["paid"] and t["delivered"] for t in rows):
            finish(db, c, day, c["pending_exit"])
        return c, True
    return marked(c, rows), False


def defer_final_settlement(db, c, day, reason):
    from .journal import event

    if any(not (t["paid"] and t["delivered"]) for t in tranches(db, c["id"])):
        db.execute("UPDATE calls SET remaining=0,pending_exit=? WHERE id=?", (reason, c["id"]))
        event(db, c["id"], day, "restricted_bonus_exit", reason)
        return True
    return False
