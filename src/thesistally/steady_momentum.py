"""Monthly equal-weight momentum account. All plans and fills are paper-only."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import corporate, steady_actions
from .journal import cost

REGISTRY = Path(__file__).with_name("revisions") / "steady-monthly.json"
SETTINGS = json.loads(REGISTRY.read_bytes())
CAPITAL = 1_000_000
HOLD = SETTINGS["portfolio_slots"]
RETAIN = SETTINGS["retention_rank"]


class UnpricedHolding(Exception):
    def __init__(self, day, isin, reason):
        self.info = {"day": str(day), "isin": isin, "reason": reason}
        super().__init__(f"{day} {isin}: {reason}")


@dataclass
class Position:
    qty: int
    mark: float
    symbol: str
    basis: float
    # Holder's predecessor position remains frozen until delivered, tradable successor units exist.
    scheme: dict = field(default_factory=dict)


@dataclass
class Account:
    cash: float = CAPITAL
    positions: dict[str, Position] = field(default_factory=dict)
    orders: dict = field(default_factory=dict)
    receivables: list = field(default_factory=list)
    prior_gate: bool = False
    bonuses: list = field(default_factory=list)

    def locked(self, isin):
        return sum(b["whole"] for b in self.bonuses if b["isin"] == isin and not b["delivered"])

    @property
    def slots(self):
        names = set(self.positions)
        for b in self.bonuses:
            if not b["delivered"] and b["whole"] and b["parent"] not in names:
                names.add(b["isin"])
        return names

    @property
    def equity(self):
        return self.cash + self.invested + sum(r["estimate"] for r in self.receivables)

    @property
    def invested(self):
        restricted = sum(
            (0 if b["delivered"] else b["whole"]) * b["mark"]
            + (0 if b["paid"] else b["fraction"]) * b["mark"]
            for b in self.bonuses
        )
        return sum(p.qty * p.mark for p in self.positions.values()) + restricted

    @classmethod
    def restore(cls, raw):
        d = json.loads(raw)
        d["positions"] = {key: Position(**p) for key, p in d["positions"].items()}
        return cls(**d)


def open_journal(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=60, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE IF NOT EXISTS books(
        run TEXT PRIMARY KEY, definition TEXT NOT NULL, seed TEXT NOT NULL, initial_state TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(
        run TEXT NOT NULL, day TEXT NOT NULL, prior TEXT NOT NULL, gate INTEGER NOT NULL, closing_gate INTEGER NOT NULL,
        equity REAL NOT NULL, invested REAL NOT NULL, cash REAL NOT NULL, turnover REAL NOT NULL,
        fees REAL NOT NULL, slippage REAL NOT NULL, income REAL NOT NULL, state TEXT NOT NULL,
        PRIMARY KEY(run,day));
    CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY, run TEXT NOT NULL, day TEXT NOT NULL, isin TEXT, kind TEXT NOT NULL, detail TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS fills(
        id INTEGER PRIMARY KEY, run TEXT NOT NULL, day TEXT NOT NULL, signal TEXT NOT NULL, isin TEXT NOT NULL,
        side TEXT NOT NULL, qty INTEGER NOT NULL, raw_open REAL NOT NULL, price REAL NOT NULL, fee REAL NOT NULL,
        slippage REAL NOT NULL, reason TEXT NOT NULL, unknown_band INTEGER NOT NULL, realized_ex_income REAL);
    CREATE INDEX IF NOT EXISTS by_run_event ON events(run,day);
    CREATE INDEX IF NOT EXISTS by_run_fill ON fills(run,day);
    """)
    return db


def emit(db, run, day, isin, kind, data):
    db.execute(
        "INSERT INTO events(run,day,isin,kind,detail) VALUES(?,?,?,?,?)",
        (run, str(day), isin, kind, json.dumps(data, sort_keys=True, allow_nan=False)),
    )


def plan(db, run, day, account, ranked, gate):
    """Plan from this close. No fill may use its session's close to decide what to buy."""
    rows = list(ranked.itertuples(index=False))
    rank = {r.isin: int(r.rank) for r in rows}
    # A sale that could not execute stays pending. It still occupies a portfolio slot.
    mandatory = {s for s, o in account.orders.items() if o["reason"] == "retire"}
    retained = {
        s for s in account.slots if rank.get(s, RETAIN + 1) <= RETAIN and s not in mandatory
    }
    picks = sorted(retained, key=rank.get)
    for r in rows:
        if len(picks) == HOLD or not gate:
            break
        if r.isin not in account.slots:
            picks.append(r.isin)
    target = account.equity / HOLD
    orders = {}
    for s in sorted(account.slots):
        if s not in picks:
            orders[s] = (
                account.orders[s]
                if s in mandatory
                else {
                    "day": str(day),
                    "budget": 0.0,
                    "rank": rank.get(s),
                    "reason": "retire",
                    "buy": False,
                }
            )
    for s in picks:
        orders[s] = {
            "day": str(day),
            "budget": target,
            "rank": rank[s],
            "buy": bool(gate),
            "reason": "rebalance" if s in retained else "new",
        }
    account.orders = orders
    emit(
        db,
        run,
        day,
        None,
        "rebalance",
        {
            "gate": bool(gate),
            "nav": account.equity,
            "eligible": len(rows),
            "ranking": [r.isin for r in rows[:RETAIN]],
            "orders": orders,
        },
    )


def seed(db, run, prior, ranked, gate, definition):
    """Seed a fresh funded account using only a pre-window decision available at its first open."""
    basis = json.dumps(definition, sort_keys=True)
    db.execute("BEGIN IMMEDIATE")
    try:
        existing = db.execute("SELECT * FROM books WHERE run=?", (run,)).fetchone()
        if existing:
            if existing["definition"] != basis or existing["seed"] != str(prior):
                raise ValueError("Historical account input or rule identity changed")
        else:
            account = Account(prior_gate=bool(gate))
            if ranked is not None:
                plan(db, run, prior, account, ranked, gate)
            db.execute(
                "INSERT INTO books VALUES(?,?,?,?)",
                (run, basis, str(prior), json.dumps(asdict(account))),
            )
        db.commit()
    except BaseException:
        db.rollback()
        raise


def last_state(db, run):
    last = db.execute(
        "SELECT day,state FROM sessions WHERE run=? ORDER BY day DESC LIMIT 1", (run,)
    ).fetchone()
    if not last:
        last = db.execute("SELECT seed,initial_state FROM books WHERE run=?", (run,)).fetchone()
    if not last:
        raise ValueError(f"Unseeded account {run}")
    return last[0], Account.restore(last[1])


def _tradable(row):
    return row is not None and row["volume"] > 0 and row["open"] > 0


def _record_fill(db, run, day, order, isin, side, qty, raw, price, fees, band_missing, realized):
    slip = abs(raw - price) * qty
    db.execute(
        """INSERT INTO fills(run,day,signal,isin,side,qty,raw_open,price,fee,slippage,reason,unknown_band,realized_ex_income)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run,
            day,
            order["day"],
            isin,
            side,
            qty,
            raw,
            price,
            fees,
            slip,
            order["reason"],
            bool(band_missing),
            realized,
        ),
    )
    if band_missing:
        emit(
            db,
            run,
            day,
            isin,
            "band_unknown" if side == "buy" else "exit_band_unknown",
            {"side": side, "qty": qty, "raw_open": raw, "slipped_open": price},
        )
    return slip


def trade(db, run, day, account, quotes, cfg, terms=None):
    from .conventions import (
        ENTRY_BAND_VERSION,
        opening_purchase,
        opening_sale,
        unknown_opening_ceiling,
    )

    bps = cfg["cost"]["slippage_bps"]
    allow_unknown = cfg.get("entry_band_convention") == ENTRY_BAND_VERSION
    fees = slippage = turnover = 0.0
    retry = {}
    purchases = []
    for isin, order in account.orders.items():
        pos = account.positions.get(isin)
        held = pos.qty if pos else 0
        locked = account.locked(isin)
        if locked and order["reason"] == "retire":
            retry[isin] = order
        q = quotes.get(isin)
        term = (terms or {}).get(isin)
        suspension = (pos and pos.scheme) or (
            term and term.get("type") != "bonus" and day >= term["suspended"]
        )
        if suspension or not _tradable(q):
            missing_reason = "missing_open"
            if suspension:
                missing_reason = "verified_suspension"
            elif q is not None and q.get("volume") == 0:
                missing_reason = "zero_traded_volume"
            # Without an opening quote, only the last available mark can
            # distinguish a prospective top-up from an overweight reduction.
            intent = order["buy"] and (
                order["reason"] == "new"
                or (pos is not None and (held + locked) * pos.mark < order["budget"])
            )
            if held and (order["reason"] == "retire" or pos.qty * pos.mark > order["budget"]):
                retry[isin] = {**order, "buy": False}
            emit(
                db,
                run,
                day,
                isin,
                "deferred" if held else "missed",
                {"reason": missing_reason, "planned_purchase": bool(intent), "order": order},
            )
            continue
        # Both legs use the applicable side's slipped opening price.
        sell_target = max(0, int(order["budget"] / (q["open"] * (1 - bps / 10000))) - locked)
        buy_target = max(0, int(order["budget"] / (q["open"] * (1 + bps / 10000))) - locked)
        if "sell_qty" in order:
            sell_target = max(0, held - order["sell_qty"])
        if sell_target < held:
            price, missing = opening_sale(
                q["open"], q["volume"], q.get("floor"), bps, allow_unknown=True
            )
            if price is None:
                retry[isin] = {**order, "buy": False, "sell_qty": held - sell_target}
                emit(
                    db,
                    run,
                    day,
                    isin,
                    "deferred",
                    {"reason": "lower_circuit_or_liquidity", "order": order},
                )
                continue
            n = held - sell_target
            value = n * price
            fee = cost("sell", value, cfg)
            released_basis = pos.basis * n / held
            slippage += _record_fill(
                db,
                run,
                day,
                order,
                isin,
                "sell",
                n,
                q["open"],
                price,
                fee,
                missing,
                value - fee - released_basis,
            )
            account.cash += value - fee
            turnover += n * price
            fees += fee
            pos.qty -= n
            pos.basis -= released_basis
            if pos.qty == 0:
                del account.positions[isin]
        elif buy_target > held and order["buy"]:
            purchases.append((order["rank"], isin, buy_target - held, order, q))
        elif order["reason"] == "new" and order["buy"] and buy_target == 0:
            emit(
                db,
                run,
                day,
                isin,
                "missed",
                {"reason": "cash_or_whole_share", "planned_purchase": True, "order": order},
            )
    for _, isin, n, order, q in sorted(purchases):
        price = opening_purchase(
            q["open"], q["volume"], q.get("ceiling"), bps, allow_unknown=allow_unknown
        )
        unknown = unknown_opening_ceiling(q["open"], q.get("ceiling"))
        reason = None
        if price is None:
            reason = "unknown_upper_band" if unknown else "verified_upper_lock"
        elif isin not in account.slots and len(account.slots) >= HOLD:
            reason = "held_slot_pending_sale"
        if reason:
            emit(
                db,
                run,
                day,
                isin,
                "missed",
                {"reason": reason, "planned_purchase": True, "order": order},
            )
            continue
        n = min(n, int(account.cash / price))
        # Cost includes transaction taxes; sizing is capped by cash inclusive of those fees.
        n = max(0, n)
        if n:
            fee = cost("buy", price * n, cfg)
            if n * price + fee > account.cash:
                n = max(0, min(n - 1, int(account.cash / (price + fee / n))))
            while n and price * n + cost("buy", price * n, cfg) > account.cash:
                n -= 1
        if not n:
            emit(
                db,
                run,
                day,
                isin,
                "missed",
                {"reason": "cash_or_whole_share", "planned_purchase": True, "order": order},
            )
            continue
        fee = cost("buy", n * price, cfg)
        slippage += _record_fill(
            db, run, day, order, isin, "buy", n, q["open"], price, fee, unknown, None
        )
        account.cash -= n * price + fee
        pos = account.positions.setdefault(isin, Position(0, q["open"], q["symbol"], 0.0))
        pos.qty += n
        pos.basis += n * price + fee
        turnover += n * price
        fees += fee
    account.orders = retry
    return fees, slippage, turnover


def prepare_holdings(db, run, day, account, quotes, terms):
    """Settle ex-date holder events before sizing orders. Cash received at the close is unavailable at the open."""
    income = 0.0
    previous_holdings = list(account.positions.items())
    for isin, pos in previous_holdings:
        t = terms.get(isin)
        if t and t.get("bonus") and day == t["effective"]:
            steady_actions.issue_bonus(db, run, day, account, pos, t, emit)
        if t and t.get("type") != "bonus" and day >= t["suspended"]:
            continue
        q = quotes.get(isin, {})
        if t and t.get("bonus") and day == t["effective"]:
            q = steady_actions.with_holder_dividend(q, t)
        if release := q.get("quarantine_release"):
            emit(db, run, day, isin, "quarantine_release", release)
        rights = q.get("rights_issue")
        if rights:
            emit(
                db,
                run,
                day,
                isin,
                "corporate_action",
                {"kind": "rights_no_subscription", "qty": pos.qty, "cash": 0, **rights},
            )
        handled = (
            set(t["bonus"].get("subjects", ()))
            if t and t.get("bonus") and day == t["effective"]
            else set()
        )
        unknown = [a for a in q.get("other_actions", ()) if a not in handled]
        if unknown:
            raise UnpricedHolding(day, isin, "Unresolved corporate action: " + ", ".join(unknown))
        amount = corporate.cash(pos.qty, q.get("dividend", 0))
        if amount:
            income += amount
            emit(
                db,
                run,
                day,
                isin,
                "dividend",
                {"qty": pos.qty, "per_share": q["dividend"], "cash": amount},
            )
    # Ex-date income on an existing successor uses holdings before today's credit.
    for isin, pos in previous_holdings:
        t = terms.get(isin)
        if t and t.get("type") != "bonus" and day >= t["suspended"]:
            income += convert(db, run, day, account, isin, pos, t, quotes)
    income += steady_actions.settle_bonuses(db, run, day, account, terms, quotes, emit, Position)
    return income


def convert(db, run, day, account, isin, pos, term, quotes):
    """Apply registered gross entitlements. The pending rebalance follows the successor."""
    t = term
    scheme = pos.scheme

    def action(stage, **detail):
        emit(
            db,
            run,
            day,
            isin,
            "corporate_action",
            {
                "stage": stage,
                "convention_version": corporate.VERSION,
                "ratio": [t.get("new_shares", 0), t.get("old_shares", 1)],
                "effective": t["effective"],
                "suspended": t["suspended"],
                "credited": t.get("credited"),
                "tradable": t.get("tradable"),
                "source_url": t["source_url"],
                "credit_source": t.get("credit_source", t["source_url"]),
                "successor_isin": t.get("successor_isin"),
                **detail,
            },
        )

    if not scheme:
        scheme["phase"] = "suspended"
        action("suspended", original_qty=pos.qty, frozen_value=pos.qty * pos.mark)
    if day < t["effective"]:
        return 0.0
    new = t.get("successor_isin")
    if not new:
        price = t.get("offer_price")
        price = pos.mark if price is None else price
        amount = corporate.cash(pos.qty, price)
        action("delisted", cash=amount, last_trade_fallback=t.get("offer_price") is None)
        del account.positions[isin]
        account.orders.pop(isin, None)
        return amount
    if scheme["phase"] == "suspended":
        whole, num = divmod(pos.qty * t["new_shares"], t["old_shares"])
        scheme.update(phase="entitled", whole=whole, fraction=num / t["old_shares"])
        action("converted", successor_qty=whole, fraction=scheme["fraction"])
    q = quotes.get(new)
    if _tradable(q) and not q.get("quarantined") and "fallback" not in scheme:
        scheme.update(fallback=q["close"], fallback_date=day)
    amount = 0.0
    if day >= t["credited"] and scheme["phase"] == "entitled":
        fraction = scheme["fraction"]
        if (
            fraction
            and "fallback" not in scheme
            and (t.get("fraction_price") is None or t.get("cash_date", day) > day)
        ):
            if day >= t["tradable"]:
                raise UnpricedHolding(
                    day, isin, "Missing first verified successor close for fractional entitlement"
                )
            return 0.0
        due = t.get("cash_date", t["credited"])
        published = t.get("fraction_price")
        delayed = due > day and fraction
        unit = scheme.get("fallback", 0) if delayed or published is None else published
        estimate = corporate.cash(fraction, unit)
        if delayed:
            account.receivables.append(
                {
                    "isin": isin,
                    "due": due,
                    "fraction": fraction,
                    "estimate": estimate,
                    "source_url": t.get("fraction_source", t["source_url"]),
                }
            )
        else:
            amount = estimate
        # Keep the predecessor entitlement value frozen, less its separately accounted fraction.
        pos.mark -= estimate / pos.qty
        scheme.update(phase="credited")
        action(
            "credited",
            fraction=fraction,
            cash=amount,
            receivable=estimate if delayed else 0.0,
            fallback=bool(fraction and published is None),
            fallback_date=scheme.get("fallback_date"),
            cash_date=due,
        )
    if scheme["phase"] == "credited" and day >= t["tradable"] and _tradable(q):
        whole = scheme["whole"]
        action("tradable", successor_qty=whole)
        del account.positions[isin]
        order = account.orders.pop(isin, None)
        if whole:
            current = account.positions.setdefault(new, Position(0, q["open"], q["symbol"], 0))
            current.qty += whole
            current.basis += pos.basis * whole / (whole + scheme["fraction"])
            if order and new not in account.orders:
                account.orders[new] = order
    return amount


def advance(db, run, day, prior, ranked, gate, quotes, cfg, terms):
    """Advance one session atomically; rank at the close only when a rebalance is scheduled."""
    day, prior = str(day), str(prior)
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT 1 FROM sessions WHERE run=? AND day=?", (run, day)).fetchone():
            db.rollback()
            return
        previous, account = last_state(db, run)
        if previous != prior or day <= prior:
            raise ValueError(f"Nonconsecutive monthly account session {run} {prior} {day}")
        income = prepare_holdings(db, run, day, account, quotes, terms)
        fees, slippage, turnover = trade(db, run, day, account, quotes, cfg, terms)
        for isin, pos in account.positions.items():
            if pos.scheme:
                continue
            q = quotes.get(isin)
            if not _tradable(q):
                raise UnpricedHolding(
                    day, isin, "Missing traded closing valuation for a held security"
                )
            if q.get("quarantined"):
                raise UnpricedHolding(day, isin, "Unverified closing quarantine")
            pos.mark = q["close"]
            pos.symbol = q["symbol"]
        for recv in list(account.receivables):
            if recv["due"] <= day:
                published = terms[recv["isin"]].get("fraction_price")
                paid = (
                    recv["estimate"]
                    if published is None
                    else corporate.cash(recv["fraction"], published)
                )
                income += paid
                account.receivables.remove(recv)
                emit(
                    db,
                    run,
                    day,
                    recv["isin"],
                    "corporate_action",
                    {"stage": "fraction_cash_paid", "cash": paid, **recv},
                )
        account.cash += income
        if not math.isfinite(account.equity) or account.equity <= 0 or account.cash < -0.00001:
            raise ValueError("Invalid monthly account mark")
        if ranked is not None:
            plan(db, run, day, account, ranked, gate)
        prior_gate = account.prior_gate
        account.prior_gate = bool(gate)
        db.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run,
                day,
                prior,
                prior_gate,
                bool(gate),
                account.equity,
                account.invested,
                account.cash,
                turnover,
                fees,
                slippage,
                income,
                json.dumps(asdict(account), allow_nan=False),
            ),
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
