"""Reconcile official ex-date notices printed under a later ticker to the traded ISIN."""

from __future__ import annotations

import json
import re
from functools import lru_cache

import pandas as pd

from . import market
from .historical_sessions import FIELDS, adjustment, context_for

CAPITAL = re.compile(r"split|bonus|rights|consolidation|demerger", re.IGNORECASE)


def restore_action_aliases(root, tape, view, ledger, actions):
    from .pilot_data import write_json

    archive = {}
    for path in sorted((root / "sources" / "reference").glob("actions-????-??-??_????-??-??.json")):
        meta = json.loads(path.with_suffix(".json.source.json").read_bytes())
        if market.sha(path.read_bytes()) != meta["sha256"]:
            raise market.MarketError(f"Corporate action source changed: {path.name}")
        archive[path.name] = meta
    records = [
        (d, printed, row["isin"], row)
        for (d, printed), events in actions.items()
        for row in events
        if row.get("isin")
    ]
    published = pd.DataFrame(records, columns=["day", "printed", "isin", "notice"])
    matches = published.merge(
        tape[["day", "isin", "symbol"]], on=["day", "isin"], validate="many_to_one"
    )
    matches = matches[matches.printed.ne(matches.symbol)]
    lookup = tape.set_index(["day", "isin"]).index
    repairs, contexts, cleared = [], {}, set()
    for (day, isin, sym), group in matches.groupby(["day", "isin", "symbol"], sort=True):
        ix = tape.index[lookup.get_loc((day, isin))]
        old_rows = actions.get((day, sym), [])
        known = {r["subject"].strip() for r in old_rows}
        new = {}
        for row in group.notice:
            subject = row["subject"].strip()
            if subject not in known:
                new[subject] = row
        if not new:
            continue
        actions[day, sym] = [*old_rows, *new.values()]
        member = pd.DataFrame([{"symbol": sym, "isin": isin}])
        ctx = context_for(day, member, {}, actions, {"meetings": []}, {})[sym]
        contexts.setdefault(str(day), {})[sym] = {
            "isin": isin,
            "dividend": ctx["dividend"],
            "other_actions": ctx["other_actions"],
        }
        for subject, row in new.items():
            if not CAPITAL.search(subject):
                continue
            proof = {
                "day": str(day),
                "isin": isin,
                "symbol": sym,
                "source_symbol": row["symbol"],
                "subject": subject,
            }
            current = tape.loc[ix]
            same_units = tape.chain.eq(current.chain) & tape.day.ge(day)
            if (
                "demerger" in subject.lower()
                or (terms := adjustment(subject, row.get("faceVal"), current.prior_close)) is None
            ):
                # A separately priced entitlement has no justified OHLC conversion. Start a
                # fresh indicator history on its ex-date and keep the holder valuation blocker.
                tape.loc[same_units, "chain"] = f"{isin}@{day}"
                existing = next(
                    (r for r in ledger if r["day"] == str(day) and r.get("isin") == isin), None
                )
                if existing is None:
                    existing = {"day": str(day), "symbol": sym, "isin": isin}
                    ledger.append(existing)
                existing.update(reason="unresolved_action")
                existing.setdefault("subject", subject)
                proof["resolution"] = "reset_at_unpriced_entitlement"
            elif pd.isna(current.prior_close):
                # The new issue has no prior raw close. The dated predecessor/new ISIN
                # reconciliation sets both price and share units from the predecessor.
                proof["resolution"] = "dated_isin_transition"
            else:
                price, shares = terms
                if price <= 0 or shares <= 0:
                    raise market.MarketError(f"Invalid capital terms on {day} for {isin}")
                tape.loc[same_units, "price_factor"] *= price
                tape.loc[same_units, "share_factor"] *= shares
                for field in FIELDS:
                    scale = "share_factor" if field == "volume" else "price_factor"
                    tape.loc[same_units, "adjusted_" + field] = (
                        tape.loc[same_units, field] / tape.loc[same_units, scale]
                    )
                if not market.is_strict_move(current.close, current.prior_close * price):
                    if current.unexplained_jump:
                        cleared.add((str(day), isin))
                        ledger[:] = [
                            r
                            for r in ledger
                            if not (
                                r["day"] == str(day)
                                and r.get("isin") == isin
                                and r["reason"] == "unexplained_move_over_20pct"
                            )
                        ]
                    tape.loc[ix, "unexplained_jump"] = False
                proof.update(
                    resolution="ex_date_rebase", price_multiplier=price, shares_multiplier=shares
                )
            repairs.append(proof)
    path = view / "sources" / "adjusted" / "action-aliases.json"
    write_json(path, {"archives": archive, "dates": contexts, "capital_actions": repairs})
    return {
        "complete": True,
        "records": len(matches),
        "traded_security_dates": sum(map(len, contexts.values())),
        "capital_actions": repairs,
        "sha256": market.sha(path.read_bytes()),
    }, cleared


@lru_cache(maxsize=2)
def _read(path, modified, size):
    return json.loads(path.read_bytes())["dates"]


def dated_aliases(root, day):
    path = root / "sources" / "adjusted" / "action-aliases.json"
    if not path.is_file():
        return {}
    stat = path.stat()
    return _read(path, stat.st_mtime_ns, stat.st_size).get(str(day), {})


def apply_to_following(root, day, member, info):
    from .exchange import settle_notices

    row = dated_aliases(root, day).get(member["symbol"], {})
    if row.get("isin") == member["isin"]:
        info.update(dividend=row["dividend"], other_actions=row["other_actions"])
        info.update(settle_notices(info))
    return info
