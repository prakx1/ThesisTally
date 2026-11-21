"""Pre-tax settlement of compulsory exchange actions. Quantities use integer ratios."""

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

import pandas as pd

from . import history, market

VERSION = "gross-entitlements-v1"
REGISTRY = Path(__file__).with_name("revisions") / "compulsory-actions.json"
SCHEMA = """
CREATE TABLE IF NOT EXISTS corporate_receivables (
    call_id INTEGER NOT NULL REFERENCES calls(id), predecessor TEXT NOT NULL, issued TEXT NOT NULL,
    due TEXT NOT NULL, fraction REAL NOT NULL, fallback_price REAL NOT NULL, estimate REAL NOT NULL, paid TEXT,
    PRIMARY KEY(call_id, predecessor)
);
CREATE TABLE IF NOT EXISTS corporate_positions (
    call_id INTEGER PRIMARY KEY REFERENCES calls(id),
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corporate_cash (
    call_id INTEGER NOT NULL REFERENCES calls(id), day TEXT NOT NULL,
    isin TEXT NOT NULL, amount REAL NOT NULL, PRIMARY KEY(call_id,day,isin)
);
"""


@lru_cache(maxsize=8)
def _parse(raw):
    actions = {}
    for t in json.loads(raw):
        for field in ("suspended", "last_trade", "effective"):
            date.fromisoformat(t[field])
        if not t["source_url"].startswith("https://") or t["last_trade"] >= t["suspended"]:
            raise market.MarketError("Missing dated official compulsory-action evidence.")
        if not t.get("successor_isin") and t["type"] != "delisting":
            raise market.MarketError(
                "A compulsory share exchange requires a documented successor ISIN."
            )
        if t.get("successor_isin") == t["predecessor_isin"]:
            raise market.MarketError("A compulsory conversion must identify two different ISINs.")
        if t.get("successor_isin") and (
            min(t["new_shares"], t["old_shares"]) <= 0
            or not (
                t["suspended"] <= t["credited"] <= t["tradable"] and t["effective"] <= t["credited"]
            )
        ):
            raise market.MarketError("Invalid compulsory conversion terms.")
        key = t["predecessor_isin"]
        if key in actions:
            raise market.MarketError(f"Conflicting compulsory-action terms for {key}.")
        actions[key] = t
    return actions


def actions(root):
    local = root / "corporate_actions.json"
    base = _parse(REGISTRY.read_bytes())
    if not local.exists():
        return base
    extra = _parse(local.read_bytes())
    if any(k in base and base[k] != v for k, v in extra.items()):
        raise market.MarketError("Conflicting local and bundled corporate-action evidence.")
    return {**base, **extra}


def cash(qty, unit):
    return float(
        (Decimal(str(qty)) * Decimal(str(unit))).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    )


@lru_cache(maxsize=512)
def quote(root, day, isin):
    path = root / "sessions" / str(day) / "bhavcopy.csv"
    if not path.exists():
        return None
    market.verify(root, date.fromisoformat(str(day)))
    tape = market.csv_table(path.read_bytes())
    rows = tape[(tape.ISIN == isin) & (tape.SctySrs.str.strip() == "EQ")]
    if len(rows) != 1:
        return None
    row = rows.iloc[0]
    if float(row.TtlTradgVol) <= 0:
        return None
    return {
        "symbol": row.TckrSymb,
        "open": float(row.OpnPric),
        "close": float(row.ClsPric),
        "high": float(row.HghPric),
        "low": float(row.LwPric),
        "volume": int(row.TtlTradgVol),
    }


def action_frame(root, day, term, frames, by_isin):
    successor = term.get("successor_isin")
    current = by_isin.get(successor)
    frame = frames[current].copy(deep=False) if current else pd.DataFrame(index=[day])
    context = dict(frame.attrs.get("exchange", {}))
    if successor and not current:
        bar = quote(root, day, successor)
        if bar:
            frame = pd.DataFrame(
                [{**bar, "fast": float("nan"), "trail": float("nan")}], index=[day]
            )
            # Bounds for off-index instruments are available in the exchange-wide daily report.
            from .exchange import decode_controls, read_context
            from .historical_sessions import context_for, reference_rows

            read_context(root, day, verify_sources=True)
            member = pd.DataFrame([{"symbol": bar["symbol"], "isin": successor, "series": "EQ"}])
            events, meetings, coverage, _ = reference_rows(root)
            context = context_for(day, member, meetings, events, coverage, set())[bar["symbol"]]
            for folder in (
                root / "context" / str(day),
                root / "sources" / "exchange-controls" / str(day),
            ):
                master = folder / "security.csv.gz"
                if master.is_file():
                    context.update(
                        decode_controls(member, None, master.read_bytes())[bar["symbol"]]
                    )
                    break
        context.setdefault("dividend", 0)
    last = quote(root, term["last_trade"], term["predecessor_isin"])
    if not last:
        raise market.MarketError(
            f"Missing official final predecessor quote on {term['last_trade']}."
        )
    context.update(compulsory_action=term, last_traded_price=last["close"])
    frame.attrs["exchange"] = context
    return frame


def state(db, call_id):
    row = db.execute("SELECT state FROM corporate_positions WHERE call_id=?", (call_id,)).fetchone()
    return json.loads(row[0]) if row else None


def save(db, call_id, p):
    db.execute(
        "INSERT INTO corporate_positions VALUES(?,?) ON CONFLICT(call_id) DO UPDATE SET state=excluded.state",
        (call_id, json.dumps(p)),
    )


def effective_call(c, p):
    if not p:
        return dict(c)
    factor = p["level_factor"]
    return {
        **dict(c),
        "isin": p["isin"],
        "symbol": p["symbol"],
        "buy": c["buy"] * factor,
        "stop": c["stop"] * factor,
        "entry": c["entry"] * factor,
        "target": c["target"] * factor,
        "size": p["converted_size"],
    }


def log(db, c, day, stage, t, **details):
    from .journal import event

    info = {
        "stage": stage,
        "convention_version": VERSION,
        "predecessor_isin": t["predecessor_isin"],
        "successor_isin": t.get("successor_isin"),
        "effective_date": t["effective"],
        "suspension_date": t["suspended"],
        "ratio": [t.get("new_shares", 0), t.get("old_shares", 1)],
        "source_url": t["source_url"],
        "fractional_cash": None,
        "tax_note": "Pre-tax. Holder-specific TDS and other investor-specific taxes excluded.",
        **details,
    }
    event(db, c["id"], day, "corporate_action", json.dumps(info, sort_keys=True))


def settle(db, c, day, bar, cfg, context, prior):
    """Return True when this session's normal trading lifecycle must be skipped."""
    from .journal import UnpricedPosition, finish, transition

    t = context["compulsory_action"]
    if c["status"] == "confirmed":
        transition(
            db, c["id"], day, "invalidated", "Suspended before the original orders could fill"
        )
        return True
    p = prior
    if not p or p["action"] != t["predecessor_isin"]:
        p = {
            "action": t["predecessor_isin"],
            "isin": t["predecessor_isin"],
            "symbol": c["symbol"],
            "level_factor": prior["level_factor"] if prior else 1,
            "converted_size": prior["converted_size"] if prior else c["size"],
            "original_qty": c["remaining"],
            "fraction": 0,
            "phase": "suspended",
            "frozen_value": c["remaining"] * context["last_traded_price"],
        }
        log(
            db,
            c,
            day,
            "suspended",
            t,
            last_traded_price=context["last_traded_price"],
            suspension_source=t.get("suspension_source", t["source_url"]),
        )
    if p["phase"] == "trading":
        if not getattr(bar, "volume", 0):
            raise UnpricedPosition(c, day, "No verified successor price after credit")
        return False
    successor = t.get("successor_isin")
    if day >= t["effective"] and not successor:
        price = t.get("offer_price")
        proxy = price is None
        price = context["last_traded_price"] if proxy else price
        proceeds = cash(c["remaining"], price)
        db.execute(
            "INSERT INTO corporate_cash VALUES(?,?,?,?)", (c["id"], day, p["isin"], proceeds)
        )
        log(
            db,
            c,
            day,
            "delisted",
            t,
            exit_price=price,
            cash=proceeds,
            flagged=True,
            price_basis="last_trade_fallback" if proxy else "published_offer",
            alternative_last_trade_cash=cash(c["remaining"], context["last_traded_price"]),
        )
        finish(db, c, day, "Compulsory delisting settlement")
        return True
    if day >= t["effective"] and p["phase"] == "suspended":
        whole, remainder = divmod(p["original_qty"] * t["new_shares"], t["old_shares"])
        scale = t["old_shares"] / t["new_shares"]
        p.update(
            phase="entitled",
            isin=successor,
            level_factor=p["level_factor"] * scale,
            fraction=remainder / t["old_shares"],
            converted_size=whole,
        )
        db.execute("UPDATE calls SET remaining=?,trail=trail*? WHERE id=?", (whole, scale, c["id"]))
        converted = effective_call(c, p)
        log(
            db,
            c,
            day,
            "converted",
            t,
            original_qty=p["original_qty"],
            successor_qty=whole,
            fraction=p["fraction"],
            translated_levels={k: converted[k] for k in ("entry", "buy", "stop", "target")},
        )
    has_quote = getattr(bar, "volume", 0) > 0
    if p["phase"] == "entitled" and has_quote and "first_close" not in p:
        bhavcopy = history.source_urls(date.fromisoformat(day))["bhavcopy.zip"]
        p.update(
            first_close=float(bar.close),
            first_close_date=day,
            first_close_source=f"{market.ARCHIVE}/{bhavcopy}",
            symbol=str(getattr(bar, "symbol", successor)),
        )
    needs_quote = p["fraction"] and (
        t.get("fraction_price") is None or t.get("cash_date", t.get("credited")) > day
    )
    awaiting_listing = needs_quote and "first_close" not in p and day < t["tradable"]
    if successor and day >= t["credited"] and p["phase"] == "entitled" and not awaiting_listing:
        if p["fraction"] and "first_close" not in p and t.get("fraction_price") is None:
            raise UnpricedPosition(c, day, "No successor quote for the fractional entitlement")
        delayed = bool(p["fraction"] and t.get("cash_date", t["credited"]) > day)
        published = None if delayed else t.get("fraction_price")
        if delayed and "first_close" not in p:
            raise UnpricedPosition(
                c, day, "No dated successor quote for the outstanding cash claim"
            )
        price = p.get("first_close") if published is None else published
        amount = cash(p["fraction"], price) if p["fraction"] else 0
        if delayed:
            db.execute(
                "INSERT INTO corporate_receivables VALUES(?,?,?,?,?,?,?,NULL)",
                (
                    c["id"],
                    t["predecessor_isin"],
                    day,
                    t["cash_date"],
                    p["fraction"],
                    p["first_close"],
                    amount,
                ),
            )
        elif amount:
            db.execute(
                "INSERT INTO corporate_cash VALUES(?,?,?,?)", (c["id"], day, successor, amount)
            )
        p.update(phase="credited", frozen_value=p["frozen_value"] - amount)
        log(
            db,
            c,
            day,
            "credited",
            t,
            credited_date=t["credited"],
            tradable_date=t["tradable"],
            fraction=p["fraction"],
            fractional_cash=0 if delayed else amount,
            outstanding_fractional_estimate=amount if delayed else 0,
            cash_date=t.get("cash_date", t["credited"]),
            price_per_share=price,
            price_basis="first_successor_close_fallback"
            if published is None and p["fraction"]
            else "published"
            if published is not None
            else "whole_shares_only",
            fallback_date=p.get("first_close_date"),
            fractional_cash_source=t.get("fraction_source", t["source_url"])
            if published is not None
            else p.get("first_close_source"),
            flagged=published is None and bool(p["fraction"]),
            credit_source=t["credit_source"],
            sensitivity_zero_fraction_cash_delta=0 if delayed else -amount,
            sensitivity_first_close_cash=cash(p["fraction"], p["first_close"])
            if "first_close" in p
            else None,
        )
        if p["converted_size"] == 0:
            finish(db, c, day, "Fractional-only compulsory settlement")
            save(db, c["id"], p)
            return True
    if p["phase"] == "credited" and day >= t["tradable"] and has_quote:
        p["phase"] = "trading"
        log(db, c, day, "tradable", t, first_tradable_session=day, credit_source=t["credit_source"])
        save(db, c["id"], p)
        return False
    # While frozen only observe successor closes for the existing close-based stop.
    refreshed = db.execute(
        "SELECT trail,age,pending_exit FROM calls WHERE id=?", (c["id"],)
    ).fetchone()
    due = refreshed["pending_exit"]
    if not due and day >= t["effective"] and has_quote and float(bar.close) <= refreshed["trail"]:
        due = "Closing stop breached during compulsory suspension"
    if not due and refreshed["age"] + 1 >= cfg["exits"]["max_days"]:
        due = "Maximum holding period during compulsory suspension"
    if not due and refreshed["age"] + 1 >= cfg["exits"]["no_progress_days"] and c["mfe"] < 1:
        due = "Time stop during compulsory suspension"
    db.execute("UPDATE calls SET age=age+1,pending_exit=? WHERE id=?", (due, c["id"]))
    save(db, c["id"], p)
    return True


def pay_receivables(db, run, day):
    from .journal import finish

    rows = db.execute(
        """SELECT q.* FROM corporate_receivables q JOIN calls c ON q.call_id=c.id
            WHERE c.run=? AND q.paid IS NULL AND q.due<=?""",
        (run, day),
    ).fetchall()
    if not rows:
        return
    root = Path(db.execute("PRAGMA database_list").fetchone()[2]).parent
    register = actions(root)
    for q in rows:
        t = register[q["predecessor"]]
        c = db.execute("SELECT * FROM calls WHERE id=?", (q["call_id"],)).fetchone()
        published = t.get("fraction_price")
        price = q["fallback_price"] if published is None else published
        amount = cash(q["fraction"], price)
        credited_event = db.execute(
            """SELECT detail FROM events WHERE call_id=? AND kind='corporate_action'
               AND json_extract(detail, '$.stage')='credited'
               AND json_extract(detail, '$.predecessor_isin')=? ORDER BY id DESC LIMIT 1""",
            (c["id"], q["predecessor"]),
        ).fetchone()
        credit = json.loads(credited_event[0]) if credited_event else {}
        db.execute(
            "INSERT INTO corporate_cash VALUES(?,?,?,?)",
            (c["id"], day, t["successor_isin"], amount),
        )
        db.execute(
            "UPDATE corporate_receivables SET paid=? WHERE call_id=? AND predecessor=?",
            (day, c["id"], q["predecessor"]),
        )
        log(
            db,
            c,
            day,
            "fraction_paid",
            t,
            cash_date=q["due"],
            fractional_cash=amount,
            fraction=q["fraction"],
            price_per_share=price,
            flagged=published is None,
            price_basis="first_successor_close_fallback" if published is None else "published",
            fractional_cash_source=t.get("fraction_source", t["source_url"])
            if published is not None
            else credit.get("fractional_cash_source"),
            fallback_date=credit.get("fallback_date") if published is None else None,
            sensitivity_zero_fraction_cash_delta=-amount,
        )
        if c["status"] == "awaiting_cash":
            finish(db, c, day, "Compulsory cash entitlement settled")
