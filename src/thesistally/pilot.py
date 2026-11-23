"""Offline continuous-account pilot using the same replay engine as the walk-forward study."""

import argparse
import fcntl
import hashlib
import json
import math
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date
from itertools import groupby, pairwise
from pathlib import Path
from time import monotonic

from . import conventions, corporate, evaluation, market, provenance
from .journal import STRATEGIES, cost, journal
from .signals import rules


def write_json(path, value):
    with market.staging(path.parent) as tmp:
        file = tmp / "output"
        file.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
        file.replace(path)


def preflight_inputs(root, receipt, *, expected_start=date(2024, 1, 1)):
    from .pilot_data import snapshot_hash

    raw = receipt.read_bytes()
    audit = json.loads(raw)
    if audit.get("complete") is not True:
        raise market.MarketError("Data preflight has unresolved blockers.")
    days = [date.fromisoformat(day) for day in audit["sessions"]]
    if (
        not days
        or days != sorted(set(days))
        or str(days[0]) != audit["start"]
        or str(days[-1]) != audit["end"]
        or days[0] != expected_start
    ):
        raise market.MarketError("Invalid calendar in data preflight.")
    if snapshot_hash(root, days) != audit["snapshot_sha256"]:
        raise market.MarketError("Replay inputs differ from the verified preflight archive.")
    extra = hashlib.sha256()
    files = list((root / "calendars").glob("*.json"))
    files.extend((root / "sources" / "reference").glob("*.source.json"))
    for manifest in (root / "sources" / "compulsory-actions").glob("*/sources.json"):
        for name, evidence in json.loads(manifest.read_bytes()).items():
            if (
                Path(name).name != name
                or market.sha((manifest.parent / name).read_bytes()) != evidence["sha256"]
            ):
                raise market.MarketError(
                    f"Changed compulsory-action evidence in {manifest.parent.name}"
                )
        files.append(manifest)
    if (root / "corporate_actions.json").exists():
        files.append(root / "corporate_actions.json")
    for file in sorted(files):
        extra.update(str(file.relative_to(root)).encode() + b"\0" + file.read_bytes())
    return days, {
        "preflight_sha256": market.sha(raw),
        "snapshot_sha256": audit["snapshot_sha256"],
        "supplementary_inputs_sha256": extra.hexdigest(),
    }


def prefix(db, run, days):
    scanned = [r[0] for r in db.execute("SELECT day FROM scans WHERE run=? ORDER BY day", (run,))]
    if scanned != [str(d) for d in days[: len(scanned)]]:
        raise market.MarketError("Pilot journal has a nonconsecutive scan history.")
    return len(scanned)


def quarantine_decisions(db, calls, rows):
    """Find orders carried into an affected open, using only the prior journal state."""
    decisions = []
    for q in rows:
        if q["reason"] not in {"unexplained_close_move", "unexplained_move_over_20pct"}:
            continue
        day = q["day"]
        converted = {
            r[0]
            for r in db.execute(
                "SELECT call_id FROM events WHERE kind='corporate_action' AND day<? "
                "AND json_extract(detail,'$.successor_isin')=?",
                (day, q["isin"]),
            )
        }
        for cid, c in calls.items():
            if c["day"] >= day or (c["isin"] != q["isin"] and cid not in converted):
                continue
            events = list(
                db.execute(
                    "SELECT day,kind,detail FROM events WHERE call_id=? AND day<=? ORDER BY id",
                    (cid, day),
                )
            )
            before = [e["kind"] for e in events if e["day"] < day]
            if {"missed", "closed", "invalidated", "unmeasurable", "awaiting_cash"}.intersection(
                before
            ):
                continue
            side = (
                "sell"
                if "exit_due" in before
                else ("buy" if "confirmed" in before and "active" not in before else None)
            )
            if side:
                decisions.append(
                    {
                        "id": cid,
                        "symbol": q["symbol"],
                        "isin": q["isin"],
                        "day": day,
                        "book": "portfolio" if c["allocation"] == "portfolio" else "shadow",
                        "side": side,
                        "quarantine_reason": q["reason"],
                        "events": [dict(e) for e in events if e["day"] == day],
                    }
                )
    return decisions


def audit_ledger(db, run, days, cfg, root, quarantine=()):
    """Reconstruct net results, check costs and compare buy/sell prices to official tape."""
    calls = {r["id"]: r for r in db.execute("SELECT * FROM calls WHERE run=?", (run,))}
    receipts = dict(db.execute("SELECT call_id,SUM(amount) FROM cash_receipts GROUP BY call_id"))
    corporate_events = defaultdict(list)
    events = defaultdict(list)
    fill_successors = defaultdict(list)
    for event in db.execute(
        "SELECT e.* FROM events e JOIN calls c ON e.call_id=c.id WHERE c.run=? ORDER BY e.id",
        (run,),
    ):
        cid = event["call_id"]
        if events[cid] and events[cid][-1] == "fill":
            fill_successors[cid].append((event["day"], event["kind"], event["detail"]))
        events[cid].append(event["kind"])
        if event["kind"] == "corporate_action":
            corporate_events[cid].append({"day": event["day"], **json.loads(event["detail"])})
    fills = defaultdict(list)
    totals = defaultdict(Counter)
    discrepancies = []
    counts = Counter()
    next_day = {str(a): str(b) for a, b in pairwise(days)}
    portfolio_flows = Counter()
    open_flows = Counter()
    settled_flows = Counter()
    charged_dp = set()
    slip = cfg["cost"]["slippage_bps"] / 10000

    def check(ok, reason, identifier):
        counts[reason] += 1
        if not ok:
            discrepancies.append({"check": reason, "id": identifier})

    sql = """SELECT f.*,s.isin FROM fills f LEFT JOIN fill_securities s ON s.fill_id=f.id
             JOIN calls c ON f.call_id=c.id WHERE c.run=? ORDER BY f.day,f.id"""
    for day, day_fills in groupby(db.execute(sql, (run,)), key=lambda f: f["day"]):
        tape = market.csv_table((root / "sessions" / day / "bhavcopy.csv").read_bytes())
        tape = tape[tape.SctySrs.str.strip().isin(["EQ", "BE", "BZ"])].set_index("ISIN")
        for f in day_fills:
            cid, side, price, qty = f["call_id"], f["side"], f["price"], f["qty"]
            c = calls[cid]
            fills[cid].append(f)
            book = "portfolio" if c["allocation"] == "portfolio" else "shadow"
            key = (book, day, f["isin"] if book == "portfolio" else cid)
            expected_fee = cost(side, qty * price, cfg, key not in charged_dp)
            if book == "portfolio":
                amount = (1 if side == "sell" else -1) * qty * price - f["fees"]
                portfolio_flows[day] += amount
                settled_flows[day] += amount
                if side == "buy":
                    open_flows[day] += amount
                    if cfg.get("entries"):
                        risk_per_share = (
                            conventions.fill_stop_atr(cfg) * json.loads(c["features"])["atr14"]
                            if conventions.fill_stop_atr(cfg) is not None
                            else c["entry"] - c["stop"]
                        )
                        reserved = c["planned_size"] * risk_per_share
                        for e in corporate_events.get(cid, ()):
                            if e["stage"] == "bonus_pending_rebased":
                                per_share = (
                                    conventions.fill_stop_atr(cfg) * e["original_features"]["atr14"]
                                    if conventions.fill_stop_atr(cfg) is not None
                                    else e["original_entry"] - e["original_stop"]
                                )
                                reserved = e["original_size"] * per_share
                        check(
                            qty * (price - c["stop"]) <= reserved + 1e-8,
                            "funded_buy_within_planned_risk",
                            f["id"],
                        )
                    else:
                        check(price <= c["entry"] + 1e-8, "funded_buy_within_planned_risk", f["id"])
            if side == "sell":
                charged_dp.add(key)
            check(f["fees"] == expected_fee, "fee_schedule_and_DP", f["id"])
            available = (
                f["isin"] in tape.index and f["isin"] not in tape.index[tape.index.duplicated()]
            )
            check(available, "unique_exchange_ISIN", f["id"])
            if available:
                bar = tape.loc[f["isin"]]
                raw = price / (1 + slip if side == "buy" else 1 - slip)
                if side == "buy":
                    distance = price - c["stop"]
                    pending_rights = math.prod(
                        e["factor"]
                        for e in corporate_events.get(cid, ())
                        if e["stage"] == "pending_entry_rebased"
                    )
                    holder_rights = math.prod(
                        e["factor"]
                        for e in corporate_events.get(cid, ())
                        if e["stage"] == "rights_lapsed"
                    )
                    if cfg.get("entries"):
                        features = json.loads(c["features"])
                        atr = features.get(
                            "atr14", features.get("atr") if cfg["trend"]["atr"] == 14 else None
                        )
                        tol = math.ulp(price)
                        check(
                            atr
                            and float(bar.OpnPric)
                            <= c["entry"] + cfg["entries"]["max_chase_atr"] * atr
                            and float(bar.OpnPric) > c["stop"]
                            and cfg["filters"]["min_stop_fraction"] * price - tol
                            <= distance
                            <= min(
                                cfg["filters"]["max_stop_fraction"] * price,
                                cfg["filters"]["max_stop_atr"] * atr,
                            )
                            + tol,
                            "fill_chase_and_stop_bounds",
                            f["id"],
                        )
                        if "results_strategy" in cfg:
                            check(
                                math.isclose(distance, conventions.fill_stop_atr(cfg) * atr),
                                "results_fill_anchored_stop",
                                f["id"],
                            )
                        elif "dip" in cfg:
                            pending_bonus = math.prod(
                                e["factor"]
                                for e in corporate_events.get(cid, ())
                                if e["stage"] == "bonus_pending_rebased"
                            )
                            check(
                                math.isclose(distance, conventions.fill_stop_atr(cfg) * atr)
                                and math.isclose(
                                    c["planned_target"] * pending_bonus, features["prior_high10"]
                                )
                                and math.isclose(
                                    c["target"],
                                    features["prior_high10"] * pending_rights * holder_rights,
                                ),
                                "dip_frozen_high_and_opening_stop",
                                f["id"],
                            )
                        elif "range_bouncers" in cfg:
                            pending_bonus = math.prod(
                                e["factor"]
                                for e in corporate_events.get(cid, ())
                                if e["stage"] == "bonus_pending_rebased"
                            )
                            scale = pending_bonus * pending_rights
                            fraction = cfg["range_bouncers"]["target_fraction"]
                            planned = features["low60"] + fraction * (
                                features["high60"] - features["low60"]
                            )
                            check(
                                math.isclose(c["planned_target"], planned)
                                and math.isclose(c["target"], planned * scale * holder_rights)
                                and math.isclose(c["stop"], features["low60"] * scale - atr),
                                "range_frozen_levels",
                                f["id"],
                            )
                        else:
                            check(
                                math.isclose(
                                    c["target"],
                                    (price + cfg["exits"]["target_r"] * distance) * holder_rights,
                                )
                                and math.isclose(
                                    c["planned_target"] * pending_rights,
                                    c["entry"]
                                    + cfg["exits"]["target_r"] * (c["entry"] - c["stop"]),
                                ),
                                "planned_and_fill_targets",
                                f["id"],
                            )
                    else:
                        check(
                            math.isclose(
                                c["target"], c["planned_target"] * pending_rights * holder_rights
                            )
                            and distance > 0
                            and (c["planned_target"] * pending_rights - price) / distance
                            >= cfg["exits"]["target_r"],
                            "fixed_target_fill_reward_to_risk",
                            f["id"],
                        )
                    if c["allocation"] == "unconstrained":
                        budget = cfg["risk"]["capital"] * cfg["risk"]["fraction"]
                        check(
                            distance > 0 and qty == math.floor(budget / distance),
                            "equal_risk_quantity",
                            f["id"],
                        )
                    check(day == next_day.get(c["day"]), "buy_next_session", f["id"])
                    check(
                        math.isclose(raw, float(bar.OpnPric), abs_tol=1e-6), "buy_at_open", f["id"]
                    )
                else:
                    check(
                        float(bar.LwPric) - 1e-6 <= raw <= float(bar.HghPric) + 1e-6,
                        "sell_within_observed_range",
                        f["id"],
                    )
                    n = len(fills[cid]) - 1
                    following = fill_successors[cid]
                    following_day, state, reason = (
                        following[n] if n < len(following) else ("", "", "")
                    )
                    final_sale = following_day == day and state in {
                        "closed",
                        "awaiting_cash",
                        "restricted_bonus_exit",
                    }
                    if not final_sale or reason == "Target, single share":
                        scale = math.prod(
                            e["ratio"][1] / e["ratio"][0]
                            for e in corporate_events.get(cid, ())
                            if e["stage"] == "converted" and e["day"] <= day
                        )
                        expected = max(float(bar.OpnPric), c["target"] * scale)
                    elif reason == "Scheduled maximum holding close" and not any(
                        d < day and k == "restricted_bonus_exit" for d, k, _ in following
                    ):
                        expected = float(bar.ClsPric)
                    else:
                        expected = float(bar.OpnPric)
                        if book == "portfolio":
                            open_flows[day] += amount
                    check(
                        math.isclose(raw, expected, abs_tol=1e-6),
                        "sell_price_and_slippage",
                        f["id"],
                    )
                totals[book]["modeled_slippage_inr"] += abs(price - raw) * qty
            totals[book][side + "_fills"] += 1
            totals[book]["fees_inr"] += f["fees"]
    for cid, c in calls.items():
        history = events[cid]
        check(
            history[:2] == ["detected", "watching"] and c["status"] in history,
            "lifecycle_journal",
            cid,
        )
        check(len(fills[cid]) == history.count("fill"), "fill_event_pair", cid)
        if c["status"] == "closed":
            net = sum(
                (1 if f["side"] == "sell" else -1) * f["qty"] * f["price"] - f["fees"]
                for f in fills[cid]
            ) + receipts.get(cid, 0)
            check(math.isclose(net, c["net"], abs_tol=1e-6), "closed_net_cash", cid)
            risk = (c["buy"] - c["stop"]) * c["size"]
            check(
                risk > 0 and math.isclose(net / risk, c["outcome_r"], abs_tol=1e-8),
                "closed_net_risk",
                cid,
            )
        if c["buy"] is not None and not corporate_events.get(cid):
            remaining = sum((1 if f["side"] == "buy" else -1) * f["qty"] for f in fills[cid])
            check(remaining == c["remaining"], "position_quantity", cid)
    collisions = []
    for due in db.execute(
        "SELECT e.* FROM events e JOIN calls c ON c.id=e.call_id WHERE c.run=? "
        "AND e.kind='exit_due' AND e.detail='Stop-first collision' ORDER BY e.id",
        (run,),
    ):
        cid, day = due["call_id"], due["day"]
        c = calls[cid]
        deferred_days = [
            r[0]
            for r in db.execute(
                "SELECT day FROM events WHERE call_id=? AND id>? AND kind='exit_deferred' ORDER BY id",
                (cid, due["id"]),
            )
        ]
        sales = [f for f in fills[cid] if f["side"] == "sell" and f["day"] >= day]
        before = [f for f in fills[cid] if f["day"] <= day and f["side"] == "buy"]
        check(not any(f["day"] == day for f in sales), "collision_no_same_day_sale", cid)
        expected_day = next_day.get(day)
        for deferred in deferred_days:
            check(deferred == expected_day, "collision_deferred_session", cid)
            expected_day = next_day.get(deferred)
        if sales:
            check(len(sales) == 1 and sales[0]["day"] == expected_day, "collision_next_open", cid)
            if not corporate_events.get(cid):
                check(sum(f["qty"] for f in before) == sales[0]["qty"], "collision_full_exit", cid)
        else:
            last_scan = db.execute("SELECT MAX(day) FROM scans WHERE run=?", (run,)).fetchone()[0]
            check(
                expected_day is None or expected_day > last_scan, "collision_pending_at_cutoff", cid
            )
        collisions.append(
            {
                "id": cid,
                "book": "portfolio" if c["allocation"] == "portfolio" else "shadow",
                "symbol": c["symbol"],
                "strategy": c["strategy"],
                "collision": day,
                "exit_day": sales[0]["day"] if sales else None,
                "deferred_days": deferred_days,
                "exit_fill": sales[0]["price"] if sales else None,
                "net_r": c["outcome_r"],
            }
        )
    records = []
    from . import dip_bonus

    bonus_terms = dip_bonus.register(root)
    terms = corporate.actions(root)
    for cid, stages in corporate_events.items():
        c = calls[cid]
        for event in stages:
            if event["stage"].startswith("bonus_"):
                leg = bonus_terms[(event["isin"], event["ex_date"])]["bonus"]
                check(
                    event["ratio"] == [leg["new_shares"], leg["old_shares"]]
                    and event["convention_version"] == corporate.VERSION
                    and event["source_url"] in {leg["source_url"], leg.get("fraction_source")}
                    and event["listing_source"] == leg["listing_source"]
                    and event["credited"] == leg["credited"]
                    and event["tradable"] == leg["tradable"],
                    "bonus_official_terms",
                    cid,
                )
                if event["stage"] == "bonus_entitlement":
                    whole, rem = divmod(
                        event["original_qty"] * leg["new_shares"], leg["old_shares"]
                    )
                    check(
                        whole == event["whole"]
                        and math.isclose(rem / leg["old_shares"], event["fraction"])
                        and event["day"] == event["ex_date"],
                        "bonus_entitlement_quantity",
                        cid,
                    )
                    early_sold = sum(
                        f["qty"]
                        for f in fills[cid]
                        if f["side"] == "sell" and event["ex_date"] <= f["day"] < leg["tradable"]
                    )
                    check(
                        early_sold <= event["original_qty"], "bonus_units_locked_until_listing", cid
                    )
                if event["stage"] == "bonus_fraction_paid":
                    price = leg.get("fraction_price")
                    if price is None:
                        quote = corporate.quote(root, event["reference_day"], event["isin"])
                        price = quote and quote["close"]
                    check(
                        price
                        and event["cash"] == corporate.cash(event["fraction"], price)
                        and event["first_close_fallback"] == ("fraction_price" not in leg),
                        "bonus_fraction_price_and_cash",
                        cid,
                    )
                continue
            if event["stage"] in {"rights_lapsed", "pending_entry_rebased"}:
                from .conventions import VERSION, dated

                terms_today = dated(root, event["day"]).get(event["isin"], {}).get("rights_issue")
                check(
                    event["convention_version"] == VERSION
                    and terms_today
                    and all(event.get(k) == v for k, v in terms_today.items())
                    and not event["subscribed"]
                    and event["entitlement_cash"] == 0
                    and (
                        event["held_shares"] == 0
                        if event["stage"] == "pending_entry_rebased"
                        else event["held_shares"] > 0
                    ),
                    "rights_terms_and_no_entitlement_cash",
                    cid,
                )
                continue
            term = terms[event["predecessor_isin"]]
            check(
                event["convention_version"] == corporate.VERSION
                and event["source_url"] == term["source_url"]
                and event["effective_date"] == term["effective"]
                and event["suspension_date"] == term["suspended"]
                and event["ratio"] == [term.get("new_shares", 0), term.get("old_shares", 1)],
                "corporate_terms",
                cid,
            )
            if event["stage"] == "converted":
                num, den = event["ratio"]
                qty, rem = divmod(event["original_qty"] * num, den)
                check(
                    qty == event["successor_qty"] and math.isclose(rem / den, event["fraction"]),
                    "conversion_quantity",
                    cid,
                )
                # Stored levels retain the initial plan. Each subsequent conversion multiplies the factor.
                scale = math.prod(
                    e["ratio"][1] / e["ratio"][0]
                    for e in stages
                    if e["stage"] == "converted" and e["day"] <= event["day"]
                )
                check(
                    all(
                        math.isclose(c[k] * scale, v) for k, v in event["translated_levels"].items()
                    ),
                    "converted_levels",
                    cid,
                )
                check(
                    all(not (term["suspended"] <= f["day"] < term["tradable"]) for f in fills[cid]),
                    "freeze_has_no_fills",
                    cid,
                )
            if event["stage"] == "tradable":
                first_buy = next(f["day"] for f in fills[cid] if f["side"] == "buy")
                prior_age = sum(first_buy <= str(d) < event["day"] for d in days)
                if prior_age >= cfg["exits"]["max_days"]:
                    on_resume = [
                        f for f in fills[cid] if f["day"] == event["day"] and f["side"] == "sell"
                    ]
                    deferred = db.execute(
                        "SELECT 1 FROM events WHERE call_id=? AND day=? AND kind='exit_deferred'",
                        (cid, event["day"]),
                    ).fetchone()
                    check(bool(on_resume or deferred), "frozen_time_exit_attempt", cid)
            if event["stage"] in {"credited", "fraction_paid"} and event.get("fraction"):
                deferred = (
                    event["stage"] == "credited"
                    and event.get("outstanding_fractional_estimate", 0) > 0
                )
                expected = (
                    0 if deferred else corporate.cash(event["fraction"], event["price_per_share"])
                )
                check(event["fractional_cash"] == expected, "fractional_cash", cid)
                if event["price_basis"] == "first_successor_close_fallback":
                    first_traded = next(
                        (
                            str(d)
                            for d in days
                            if term["effective"] <= str(d) <= event["fallback_date"]
                            and corporate.quote(root, d, term["successor_isin"])
                        ),
                        None,
                    )
                    quote = corporate.quote(root, event["fallback_date"], term["successor_isin"])
                    check(
                        bool(event.get("flagged"))
                        and first_traded == event["fallback_date"]
                        and quote
                        and math.isclose(quote["close"], event["price_per_share"]),
                        "fallback_close_and_flag",
                        cid,
                    )
        records.append(
            {
                "id": cid,
                "symbol": c["symbol"],
                "allocation": c["allocation"],
                "status": c["status"],
                "events": stages,
                "fills": [dict(f) for f in fills[cid]],
            }
        )
    for table in ("entitlements", "corporate_cash"):
        for (
            book,
            amount,
        ) in db.execute(
            f"""SELECT CASE WHEN c.allocation='portfolio' THEN 'portfolio' ELSE 'shadow' END,
                SUM(e.amount) FROM {table} e JOIN calls c ON c.id=e.call_id WHERE c.run=? GROUP BY 1""",
            (run,),
        ):
            totals[book][table + "_inr"] = amount
    # Add dividends, scheme cash and receivable valuation to the independently
    # accumulated funded cashflows, then reconcile each close against the journal.
    for from_table, day_col, value_col, sign in (
        ("cash_receipts", "day", "amount", 1),
        ("corporate_receivables", "issued", "estimate", 1),
        ("corporate_receivables", "paid", "estimate", -1),
    ):
        for day, amount in db.execute(
            f"""SELECT e.{day_col},sum(e.{value_col}) FROM {from_table} e JOIN calls c ON c.id=e.call_id
                WHERE c.run=? AND c.allocation='portfolio' AND e.{day_col} IS NOT NULL GROUP BY e.{day_col}""",
            (run,),
        ):
            portfolio_flows[day] += sign * amount
    running = cfg["risk"]["capital"]
    settled = running
    min_open_cash = min_close_cash = settled
    for mark in db.execute(
        "SELECT * FROM account_marks WHERE run=? AND book='portfolio' ORDER BY day", (run,)
    ):
        # Exclude intraday/close sales, unpaid fractions and ex-day dividend
        # credits from money available to fund that morning's opening orders.
        day = mark["day"]
        opening = settled + open_flows[day]
        min_open_cash = min(min_open_cash, opening)
        check(opening >= -1e-6, "nonnegative_opening_cash", day)
        credits = db.execute(
            "SELECT coalesce(sum(e.amount),0) FROM cash_receipts e JOIN calls c ON c.id=e.call_id "
            "WHERE c.run=? AND c.allocation='portfolio' AND e.day=?",
            (run, day),
        ).fetchone()[0]
        settled += settled_flows[day] + credits
        min_close_cash = min(min_close_cash, settled)
        check(settled >= -1e-6, "nonnegative_closing_cash", day)
        running += portfolio_flows[mark["day"]]
        check(
            math.isclose(mark["equity"], running + mark["invested"], rel_tol=0, abs_tol=1e-5),
            "funded_equity_and_cash",
            mark["day"],
        )
    scans = dict(db.execute("SELECT day,count FROM scans WHERE run=?", (run,)))
    detected = Counter(c["day"] for c in calls.values())
    check(
        all(scans.get(day) == n for day, n in detected.items())
        and all(n == detected[day] for day, n in scans.items()),
        "scan_counts",
        run,
    )
    check(not list(db.execute("PRAGMA foreign_key_check")), "foreign_keys", run)
    official_records = []
    for isin in sorted(
        {
            event["predecessor_isin"]
            for rec in records
            for event in rec["events"]
            if "predecessor_isin" in event
        }
    ):
        folder = root / "sources" / "compulsory-actions" / isin
        source_file = folder / "sources.json"
        manifest = json.loads(source_file.read_bytes()) if source_file.is_file() else {}
        terms_urls = {
            terms[isin][k]
            for k in ("source_url", "suspension_source", "credit_source")
            if k in terms[isin]
        }
        check(
            terms_urls <= {d["url"] for d in manifest.values()}, "corporate_source_coverage", isin
        )
        for name, doc in manifest.items():
            file = folder / name
            check(
                file.is_file() and market.sha(file.read_bytes()) == doc["sha256"],
                "corporate_source_checksum",
                f"{isin}/{name}",
            )
        official_records.append({"isin": isin, "source_documents": manifest})
    return {
        "orders_on_close_quarantine": quarantine_decisions(db, calls, quarantine),
        "minimum_opening_cash_inr": min_open_cash,
        "minimum_closing_cash_inr": min_close_cash,
        "stop_first_collisions": collisions,
        "corporate_sources": official_records,
        "checks": counts,
        "discrepancies": len(discrepancies),
        "examples": discrepancies[:30],
        "costs_and_cash": totals,
        "corporate_positions": records,
    }


def pilot(root, receipt, revision=None):
    root, receipt = root.resolve(), receipt.resolve()
    if root.name != "market" or root.parent.name != "pilot" or receipt.parent != root.parent:
        raise market.MarketError(
            "Use the prepared pilot/market root and its sibling preflight receipt."
        )
    days, proof = preflight_inputs(root, receipt)
    cfg = rules(revision=revision)
    paired = bool(cfg.get("entries"))
    if cfg["risk"]["capital"] != 1_000_000:
        raise market.MarketError("Stage 1 requires the current ₹10 lakh account configuration.")
    work = root.parent
    archive = work / "provenance" / "source"
    if archive.resolve() != archive:
        raise market.MarketError("Pilot source archive must be an independent local directory.")
    code = provenance.snapshot(work)
    identity = market.sha(
        json.dumps(
            [
                "continuous-pilot-v1",
                proof["snapshot_sha256"],
                proof["supplementary_inputs_sha256"],
                str(days[0]),
                str(days[-1]),
                code["code_hash"],
                cfg["config_hash"],
            ],
            sort_keys=True,
        ).encode()
    )
    run = "pilot-" + identity[:20]
    edge = run + "-equal-risk"
    books = [(run, cfg), (edge, cfg)] if paired else [(run, cfg)]
    folder = work / "runs" / run
    if folder.resolve() != folder:
        raise market.MarketError("Pilot output must be a local independent directory.")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "journal.sqlite"
    metadata = folder / "identity.json"
    with (folder / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.is_symlink() or (
            path.exists() and (not metadata.exists() or path.stat().st_nlink != 1)
        ):
            raise market.MarketError("Unidentified or linked pilot journal.")
        if metadata.exists() and json.loads(metadata.read_text())["identity"] != identity:
            raise market.MarketError("Pilot provenance does not match the existing journal.")
        if not metadata.exists():
            write_json(
                metadata,
                {
                    "identity": identity,
                    **proof,
                    **code,
                    "revision": cfg["revision"],
                    "config_hash": cfg["config_hash"],
                },
            )
        # Cash settlements resolve optional local terms relative to the sqlite file.
        terms = root / "corporate_actions.json"
        if terms.is_file():
            dest = folder / terms.name
            if not dest.exists():
                dest.write_bytes(terms.read_bytes())
            if dest.read_bytes() != terms.read_bytes():
                raise market.MarketError("Isolated settlement terms changed.")
        with closing(journal(path)) as db:
            if {r[0] for r in db.execute("SELECT id FROM runs")} - {key for key, _ in books}:
                raise market.MarketError("Pilot journal contains a different experiment.")
            timing_file = folder / "timings.json"
            timings = json.loads(timing_file.read_text()) if timing_file.exists() else []
            gaps = {}
            for year, group in groupby(days, key=lambda d: d.year):
                year_days = list(group)
                before = min(prefix(db, key, days) for key, _ in books)
                pending = [d for d in year_days if d >= days[before]] if before < len(days) else []
                if not pending:
                    continue
                started = monotonic()
                size = path.stat().st_size
                try:
                    gaps = evaluation.replay_window(
                        db, root, pending, books, unconstrained_runs={edge} if paired else ()
                    )
                finally:
                    elapsed = monotonic() - started
                    after = min(prefix(db, key, days) for key, _ in books)
                    timings.append(
                        {
                            "year": year,
                            "sessions": after - before,
                            "seconds": elapsed,
                            "journal_growth_bytes": path.stat().st_size - size,
                        }
                    )
                    write_json(timing_file, timings)
                print(f"{year}: {after}/{len(days)} sessions committed", flush=True)
                if gaps:
                    break
            done = prefix(db, run, days)
            quarantine = (
                json.loads(receipt.read_text()).get("quarantine", []) if receipt.is_file() else []
            )
            check = audit_ledger(db, run, days, cfg, root, quarantine)
            calls = list(db.execute("SELECT * FROM calls WHERE run=?", (run,)))
            totals = {}
            for book, rows in (
                ("portfolio", [c for c in calls if c["allocation"] == "portfolio"]),
                ("shadow", [c for c in calls if c["allocation"] != "portfolio"]),
            ):
                totals[book] = {}
                for label in ("all", *STRATEGIES):
                    cohort = rows if label == "all" else [c for c in rows if c["strategy"] == label]
                    totals[book][label] = {
                        **evaluation.trade_stats(cohort, cfg["validation"]),
                        "statuses": Counter(c["status"] for c in cohort),
                        "entered": sum(c["buy"] is not None for c in cohort),
                    }
            marks = list(
                db.execute(
                    "SELECT * FROM account_marks WHERE run=? AND book='portfolio' ORDER BY day",
                    (run,),
                )
            )
            cash = cfg["risk"]["capital"]
            snap = {
                **evaluation.account_stats(marks, cash),
                "ending_equity": marks[-1]["equity"] if marks else None,
                "start_capital": cash,
                "ending_invested": marks[-1]["invested"] if marks else None,
            }
            _, final = preflight_inputs(root, receipt)
            if final != proof:
                raise market.MarketError("Pilot data or receipt changed during the run.")
            report = {
                "label": "not for decisions",
                "run": run,
                "provenance": json.loads(metadata.read_text()),
                "complete": done == len(days) and not gaps and not check["discrepancies"],
                "scanned": done,
                "expected": len(days),
                "start": str(days[0]),
                "end": str(days[-1]),
                "through": str(days[done - 1]) if done else None,
                "valuation_gaps": gaps,
                "books": totals,
                "allocations": Counter(c["allocation"] for c in calls),
                "equity": snap,
                "audit": check,
                "timings": timings,
                "runtime_sessions_measured": sum(t["sessions"] for t in timings),
                "journal_bytes": path.stat().st_size,
            }
            if paired:
                from .mechanics import mechanics_report

                edge_audit = audit_ledger(db, edge, days, cfg, root, quarantine)
                report["mechanics"] = mechanics_report(db, run, edge, cfg)
                report["unconstrained_audit"] = edge_audit
                report["complete"] = (
                    report["complete"]
                    and prefix(db, edge, days) == len(days)
                    and not edge_audit["discrepancies"]
                    and not report["mechanics"]["discrepancies"]
                )
            write_json(folder / "report.json", report)
            return folder / "report.json", report["complete"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/pilot/market"))
    parser.add_argument("--preflight", type=Path, default=Path("data/pilot/preflight.json"))
    parser.add_argument("--revision", choices=("r1", "r2"), default="r2")
    args = parser.parse_args()
    report_path, complete = pilot(args.root, args.preflight, args.revision)
    print(report_path, flush=True)
    raise SystemExit(0 if complete else 1)
