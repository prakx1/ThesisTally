"""Append-only events and simulated fills, committed once per completed session."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from pathlib import Path

from . import brake, conventions, corporate, dip_bonus, filings, notifier, paper, research_journal
from .market import MarketError, today
from .signals import STRATEGIES, signal_atr14, stop_bound_reason

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, rules TEXT NOT NULL, capital REAL NOT NULL, peak REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS unconstrained_runs (run TEXT PRIMARY KEY REFERENCES runs(id));
CREATE TABLE IF NOT EXISTS scans (
    run TEXT NOT NULL, day TEXT NOT NULL, regime TEXT NOT NULL, count INTEGER NOT NULL,
    PRIMARY KEY(run,day)
);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY, run TEXT NOT NULL REFERENCES runs(id), day TEXT NOT NULL,
    symbol TEXT NOT NULL, isin TEXT NOT NULL, sector TEXT NOT NULL, strategy TEXT NOT NULL,
    revision TEXT NOT NULL, regime TEXT NOT NULL, features TEXT NOT NULL, entry REAL NOT NULL,
    stop REAL NOT NULL, target REAL NOT NULL, size INTEGER NOT NULL, allocation TEXT NOT NULL,
    status TEXT NOT NULL, buy REAL, remaining INTEGER DEFAULT 0, trail REAL, pending_exit TEXT,
    age INTEGER DEFAULT 0, partial INTEGER DEFAULT 0, mae REAL DEFAULT 0, mfe REAL DEFAULT 0,
    net REAL, outcome_r REAL, action TEXT NOT NULL DEFAULT 'unreviewed',
    planned_target REAL, planned_size INTEGER,
    UNIQUE(run, day, isin, strategy)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, call_id INTEGER NOT NULL REFERENCES calls(id),
    day TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entitlements (
    call_id INTEGER NOT NULL REFERENCES calls(id), day TEXT NOT NULL, amount REAL NOT NULL,
    PRIMARY KEY(call_id,day)
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY, call_id INTEGER NOT NULL REFERENCES calls(id),
    day TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL, price REAL NOT NULL, fees REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rule_configs (revision TEXT PRIMARY KEY, config TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS account_marks (
    run TEXT NOT NULL, day TEXT NOT NULL, book TEXT NOT NULL, equity REAL NOT NULL,
    invested REAL NOT NULL, benchmark REAL, PRIMARY KEY(run,day,book)
);
CREATE INDEX IF NOT EXISTS calls_run_status ON calls(run,status);
CREATE INDEX IF NOT EXISTS fills_call ON fills(call_id);
CREATE INDEX IF NOT EXISTS events_call ON events(call_id);
CREATE INDEX IF NOT EXISTS fills_day ON fills(day);
CREATE TABLE IF NOT EXISTS holdout_uses (
    revision TEXT PRIMARY KEY, experiment TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
    status TEXT NOT NULL
);
"""
ACTIVE = ("confirmed", "active")


def journal(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20, isolation_level="DEFERRED")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        SCHEMA
        + filings.SCHEMA
        + research_journal.SCHEMA
        + notifier.SCHEMA
        + brake.SCHEMA
        + corporate.SCHEMA
        + dip_bonus.SCHEMA
        + paper.SCHEMA
        + """
        CREATE TABLE IF NOT EXISTS fill_securities (
            fill_id INTEGER PRIMARY KEY REFERENCES fills(id), isin TEXT NOT NULL
        );
        CREATE VIEW IF NOT EXISTS cash_receipts AS SELECT call_id,day,amount FROM entitlements
            UNION ALL SELECT call_id,day,amount FROM corporate_cash;
    """
    )
    additions = {
        "rights_factor": "REAL NOT NULL DEFAULT 1",
        "model_version": "TEXT NOT NULL DEFAULT 'rs126-v1'",
        "planned_target": "REAL",
        "planned_size": "INTEGER",
    }
    if additions.keys() - {r[1] for r in db.execute("PRAGMA table_info(calls)")}:
        with db:
            db.execute("BEGIN IMMEDIATE")
            existing = {r[1] for r in db.execute("PRAGMA table_info(calls)")}
            for name, definition in additions.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE calls ADD COLUMN {name} {definition}")
            if "planned_size" not in existing:
                db.execute("UPDATE calls SET planned_size=size")
            if "planned_target" not in existing:
                # Legacy shadow targets were rebased on fills. Their original
                # plans survive on the paired forward calls.
                db.execute(
                    """UPDATE calls AS c SET planned_target=CASE WHEN c.run=? THEN
                         (SELECT p.target FROM calls p WHERE p.run='observations'
                           AND p.day=c.day AND p.isin=c.isin AND p.strategy=c.strategy)
                       ELSE c.target END""",
                    (paper.ENTRY_SHADOW,),
                )
    return db


def event(db, call_id, day, kind, detail=""):
    db.execute(
        "INSERT INTO events(call_id,day,kind,detail) VALUES(?,?,?,?)", (call_id, day, kind, detail)
    )


def transition(db, call_id, day, status, why):
    db.execute("UPDATE calls SET status=? WHERE id=?", (status, call_id))
    event(db, call_id, day, status, why)


def cost(side, value, cfg, charge_dp=True):
    fees = cfg["cost"]
    taxable = value * (fees["exchange"] + fees["sebi"] + fees["ipft"])
    amount = taxable * (1 + fees["gst"]) + value * fees["stt"]
    amount += value * fees["stamp_buy"] if side == "buy" else fees["dp_sell"] * charge_dp
    return round(amount, 2)


def fill(db, c, day, qty, price, side, cfg):
    if qty <= 0:
        raise ValueError("Fill quantity must be positive.")
    # The DP charge is assessed once per ISIN per sell day. Shadow calls
    # are independent counterfactuals and keep their own cost ledger.
    charged = False
    if side == "sell":
        if c["allocation"] == "portfolio":
            charged = db.execute(
                """SELECT 1 FROM fills f JOIN calls c ON c.id=f.call_id
                LEFT JOIN fill_securities a ON a.fill_id=f.id
                WHERE c.run=? AND coalesce(a.isin,c.isin)=? AND c.allocation='portfolio' AND f.day=? AND f.side='sell' LIMIT 1""",
                (c["run"], c["isin"], day),
            ).fetchone()
        else:
            charged = db.execute(
                "SELECT 1 FROM fills WHERE call_id=? AND day=? AND side='sell'", (c["id"], day)
            ).fetchone()
    fees = cost(side, price * qty, cfg, not charged)
    cursor = db.execute(
        "INSERT INTO fills(call_id,day,side,qty,price,fees) VALUES(?,?,?,?,?,?)",
        (c["id"], day, side, qty, price, fees),
    )
    db.execute("INSERT INTO fill_securities VALUES(?,?)", (cursor.lastrowid, c["isin"]))
    event(db, c["id"], day, "fill", f"{side} {qty} @ {price:.4f}, fees {fees:.2f}")
    return cursor.lastrowid


def close_call(db, c, day, price, cfg, reason):
    fill(db, c, day, c["remaining"], price, "sell", cfg)
    if conventions.tracks_holder_bonuses(cfg) and dip_bonus.defer_final_settlement(
        db, c, day, reason
    ):
        return
    finish(db, c, day, reason)


def saved_rules(db, revision):
    from .signals import rules

    row = db.execute("SELECT config FROM rule_configs WHERE revision=?", (revision,)).fetchone()
    if row:
        return json.loads(row[0])
    for path in (
        Path(__file__).with_name("rules.yaml"),
        *Path(__file__).with_name("revisions").glob("*.yaml"),
    ):
        cfg = rules(path)
        if cfg["revision"] == revision:
            return cfg
    raise MarketError(f"Missing archived rules for revision {revision}.")


def finish(db, c, day, reason):
    if db.execute(
        "SELECT 1 FROM corporate_receivables WHERE call_id=? AND paid IS NULL", (c["id"],)
    ).fetchone():
        db.execute("UPDATE calls SET remaining=0,pending_exit=NULL WHERE id=?", (c["id"],))
        transition(db, c["id"], day, "awaiting_cash", reason)
        return
    net = db.execute(
        """SELECT SUM(CASE side WHEN 'buy' THEN -qty*price ELSE qty*price END - fees)
                        FROM fills WHERE call_id=?""",
        (c["id"],),
    ).fetchone()[0]
    net += db.execute(
        "SELECT coalesce(sum(amount),0) FROM cash_receipts WHERE call_id=?", (c["id"],)
    ).fetchone()[0]
    risk = db.execute("SELECT (buy-stop)*size FROM calls WHERE id=?", (c["id"],)).fetchone()[0]
    net_r = net / risk
    db.execute(
        "UPDATE calls SET remaining=0,net=?,outcome_r=?,pending_exit=NULL WHERE id=?",
        (net, net_r, c["id"]),
    )
    transition(db, c["id"], day, "closed", reason)


class UnpricedPosition(MarketError):
    def __init__(self, call, day, reason):
        self.detail = {
            "call_id": call["id"],
            "symbol": call["symbol"],
            "isin": call["isin"],
            "day": str(day),
            "reason": reason,
        }
        super().__init__(f"Open portfolio call {call['id']} on {day}: {reason}")


def opening_quantity(c, buy, cfg):
    risk = buy - c["stop"]
    if c["allocation"] == "unconstrained":
        budget = cfg["risk"]["capital"] * cfg["risk"]["fraction"]
        return math.floor(budget / risk)
    qty = c["size"]
    if c["allocation"] == "portfolio" and buy > c["entry"]:
        if conventions.fill_stop_atr(cfg) is not None:
            # Its risk per share stays fixed when the stop moves with the fill.
            # Reduce a chased purchase to the cash reserved at the signal close.
            return math.floor(qty * c["entry"] / buy)
        # Signal close reserved this order's risk and cash. For a long with a
        # positive stop, respecting this risk cap also caps the cash and buy fees.
        return math.floor(qty * (c["entry"] - c["stop"]) / risk)
    return qty


def record_excursion(db, c, day, low, high):
    risk = c.get("original_risk", c["buy"] - c["stop"])
    adverse = min(c["mae"], (low - c["buy"]) / risk)
    favorable = max(c["mfe"], (high - c["buy"]) / risk)
    db.execute("UPDATE calls SET age=age+1,mae=?,mfe=? WHERE id=?", (adverse, favorable, c["id"]))
    if c["mfe"] < 1 <= favorable:
        event(db, c["id"], day, "first_1r", "Market price reached one initial risk unit above fill")
    return adverse, favorable


def advance(db, call, day, bar, cfg, context):
    c = dict(call)
    if cfg.get("entry_band_convention") == conventions.ENTRY_BAND_VERSION:
        context = conventions.cash_dividend_notices(context)
    slip = cfg["cost"]["slippage_bps"] / 10000
    modern = cfg.get("execution_convention") == conventions.VERSION
    if modern:
        conventions.adjust_rights(db, c, day, context.get("rights_issue"))
        if release := context.get("quarantine_release"):
            event(db, c["id"], day, "quarantine_release", json.dumps(release, sort_keys=True))
    issue = context.get("unavailable")
    closing_quarantine = (
        context.get("quarantined") and context.get("quarantine_reason") == "unexplained_close_move"
    )
    if context.get("quarantined") and not closing_quarantine:
        issue = "Quarantined daily price"
    if context.get("other_actions"):
        issue = "Corporate action requires reconciliation: " + ", ".join(context["other_actions"])
    if issue and (c["status"] == "confirmed" or c["allocation"] != "portfolio"):
        return transition(
            db, c["id"], day, "invalidated" if c["status"] == "confirmed" else "unmeasurable", issue
        )
    if issue:
        raise UnpricedPosition(c, day, issue)
    if c["status"] == "active" and context["dividend"]:
        amount = c["remaining"] * context["dividend"]
        db.execute("INSERT INTO entitlements VALUES(?,?,?)", (c["id"], day, amount))
        event(db, c["id"], day, "dividend", f"Accrued {amount:.2f} on ex-date")
    if c["status"] == "confirmed":
        shadow = c["run"] == paper.ENTRY_SHADOW
        rebase = "entries" in cfg and not shadow
        fill_atr = conventions.fill_stop_atr(cfg)
        if rebase and fill_atr is None and bar.open <= c["stop"]:
            return transition(
                db, c["id"], day, "invalidated", "Opening price at or below the planned stop"
            )
        revised = cfg.get("entry_band_convention") == conventions.ENTRY_BAND_VERSION
        raw_open, upper = float(bar.open), context.get("ceiling")
        buy = conventions.opening_purchase(
            raw_open, bar.volume, upper, cfg["cost"]["slippage_bps"], allow_unknown=revised
        )
        if buy is None:
            reason = (
                conventions.purchase_block(raw_open, bar.volume, upper, allow_unknown=True)
                if revised
                else "Opening liquidity or upper price band"
            )
            return transition(db, c["id"], day, "missed", reason)
        if fill_atr is None and buy <= c["stop"]:
            return transition(
                db, c["id"], day, "invalidated", "Opening fill crossed the planned stop"
            )
        if shadow or rebase:
            atr = signal_atr14(json.loads(c["features"]), cfg)
            if atr is None or not math.isfinite(atr) or atr <= 0:
                return transition(
                    db, c["id"], day, "unmeasurable", "Missing positive signal ATR(14)"
                )
            chase = 0.5 if shadow else cfg["entries"]["max_chase_atr"]
            ceiling = c["entry"] + chase * atr
            if bar.open > ceiling + (math.ulp(ceiling) if rebase else 0):
                return transition(
                    db, c["id"], day, "missed", f"Open above signal close + {chase:g} ATR(14)"
                )
            if fill_atr is not None:
                c["stop"] = buy - fill_atr * atr
            if rebase and stop_bound_reason(buy, c["stop"], atr, cfg):
                return transition(db, c["id"], day, "missed", "Fill stop distance out of bounds")
        elif (c["target"] - buy) / (buy - c["stop"]) < cfg["exits"]["target_r"]:
            return transition(
                db, c["id"], day, "missed", "Opening gap or slippage reduced reward/risk"
            )
        if rebase or c["allocation"] == "unconstrained":
            c["size"] = opening_quantity(c, buy, cfg)
            if not c["size"]:
                return transition(
                    db, c["id"], day, "invalidated", "Cannot fund one share at the opening risk"
                )
        if fill_atr is not None:
            db.execute("UPDATE calls SET stop=? WHERE id=?", (c["stop"], c["id"]))
            event(
                db, c["id"], day, "entry_stop", f"Stop at fill less 3 signal ATR: {c['stop']:.4f}"
            )
        elif (shadow or rebase) and "range_bouncers" not in cfg:
            target_r = 2 if shadow else cfg["exits"]["target_r"]
            c["target"] = buy + target_r * (buy - c["stop"])
            event(db, c["id"], day, "entry_target", f"{target_r:g}R from fill: {c['target']:.4f}")
        fill_id = fill(db, c, day, c["size"], buy, "buy", cfg)
        if revised and conventions.unknown_opening_ceiling(raw_open, upper):
            event(
                db,
                c["id"],
                day,
                "band_unknown",
                json.dumps(
                    {
                        "fill_id": fill_id,
                        "isin": c["isin"],
                        "side": "buy",
                        "open": raw_open,
                        "fill": buy,
                        "reported_ceiling": upper
                        if upper is not None and math.isfinite(upper)
                        else None,
                        "convention_version": conventions.ENTRY_BAND_VERSION,
                    },
                    sort_keys=True,
                ),
            )
        c.update(buy=buy, remaining=c["size"], trail=c["stop"])
        db.execute(
            "UPDATE calls SET buy=?,remaining=?,trail=?,target=?,size=? WHERE id=?",
            (buy, c["remaining"], c["trail"], c["target"], c["size"], c["id"]),
        )
        transition(db, c["id"], day, "active", "Next NSE session open")
    exit_price, unknown_floor = conventions.opening_sale(
        bar.open, bar.volume, context["floor"], cfg["cost"]["slippage_bps"], allow_unknown=modern
    )
    locked_exit = c["pending_exit"] and exit_price is None
    if closing_quarantine and (not c["pending_exit"] or locked_exit):
        # The exchange close revealed the jump after the opening auction.
        # Open-only exits can settle; a position still held needs a reliable mark.
        reason = "Quarantined closing valuation after the opening auction"
        if c["allocation"] == "portfolio":
            raise UnpricedPosition(c, day, reason)
        return transition(db, c["id"], day, "unmeasurable", reason)
    if c["pending_exit"]:
        if locked_exit:
            record_excursion(db, c, day, bar.low, bar.high)
            event(db, c["id"], day, "exit_deferred", "Opening liquidity or lower price band")
            return
        # Exit before observing this day's high or low.
        record_excursion(db, c, day, bar.open, bar.open)
        if unknown_floor:
            event(
                db,
                c["id"],
                day,
                "exit_unknown_price_band",
                json.dumps(
                    {
                        "convention_version": conventions.VERSION,
                        "open": float(bar.open),
                        "fill": exit_price,
                        "isin": c["isin"],
                    },
                    sort_keys=True,
                ),
            )
        return close_call(db, c, day, exit_price, cfg, c["pending_exit"])
    if "results_strategy" in cfg:
        from .results_replay_orders import manage_open

        return manage_open(db, c, day, bar, cfg, context)
    if "range_bouncers" in cfg:
        from .range_orders import manage_open

        return manage_open(db, c, day, bar, cfg, context)
    if "dip" in cfg:
        from .dip import manage_open

        return manage_open(db, c, day, bar, cfg, context)
    target_touch = not c["partial"] and bar.high >= c["target"]
    if target_touch and bar.low > c["trail"] and c["size"] == 1:
        # Full sale at an executable opening gap or first target touch. The later
        # high cannot count; a low on an intraday target bar may precede or follow it.
        exit_quote = max(bar.open, c["target"])
        low = bar.open if bar.open >= c["target"] else bar.low
        adverse, _ = record_excursion(db, c, day, low, exit_quote)
        upper = min(c["mae"], (bar.open - c["buy"]) / (c["buy"] - c["stop"]))
        event(
            db,
            c["id"],
            day,
            "target_exit_excursion",
            json.dumps({"mae_lower_r": adverse, "mae_upper_r": upper}),
        )
        return close_call(db, c, day, exit_quote * (1 - slip), cfg, "Target, single share")
    _, mfe = record_excursion(db, c, day, bar.low, bar.high)
    age = c["age"] + 1
    if target_touch:
        # OHLC does not establish the intraday order. A simultaneous stop
        # touch queues a full next-open sale under the same liquidity checks.
        if bar.low <= c["trail"]:
            reason = "Stop-first collision"
            db.execute("UPDATE calls SET pending_exit=? WHERE id=?", (reason, c["id"]))
            event(db, c["id"], day, "exit_due", reason)
            return
        qty = c["size"] // 2
        fill(db, c, day, qty, max(bar.open, c["target"]) * (1 - slip), "sell", cfg)
        c.update(partial=1, remaining=c["remaining"] - qty)
        db.execute("UPDATE calls SET partial=1, remaining=? WHERE id=?", (c["remaining"], c["id"]))
    next_trail = c["trail"]
    if c["partial"]:
        next_trail = float(max(next_trail, bar.fast, bar.trail))
    reason = None
    if (
        age >= cfg["exits"]["max_days"]
        and context["floor"] is not None
        and bar.close > context["floor"] + 0.005
    ):
        return close_call(
            db, c, day, bar.close * (1 - slip), cfg, "Scheduled maximum holding close"
        )
    if bar.close <= next_trail:
        reason = "Closing stop"
    elif age >= cfg["exits"]["max_days"]:
        reason = "Maximum holding period"
    elif age >= cfg["exits"]["no_progress_days"] and mfe < 1:
        reason = "Time stop without 1R"
    db.execute("UPDATE calls SET trail=?,pending_exit=? WHERE id=?", (next_trail, reason, c["id"]))
    if reason:
        event(db, c["id"], day, "exit_due", reason)


def record(db, run, day, regime, candidate, cfg, qty, allocation):
    c = candidate
    cur = db.execute(
        """INSERT INTO calls(run,day,symbol,isin,sector,strategy,revision,regime,features,
                        entry,stop,target,size,allocation,status,model_version,planned_target,planned_size)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'detected',?,?,?)""",
        (
            run,
            day,
            c.symbol,
            c.isin,
            c.sector,
            c.strategy,
            cfg["revision"],
            json.dumps(regime),
            json.dumps(c.features),
            c.entry,
            c.stop,
            c.target,
            qty,
            allocation,
            c.features.get("ranking_model_version", "rs126-v1"),
            c.target,
            qty,
        ),
    )
    i = cur.lastrowid
    event(db, i, day, "detected")
    transition(db, i, day, "watching", allocation)
    if qty == 0 and allocation != "unconstrained":
        transition(db, i, day, "invalidated", "Cannot fund one share at the planned risk")
    else:
        transition(db, i, day, "confirmed", "Completed daily candle")


def exclusion(c, cfg, regime):
    filters = cfg["filters"]
    if c.features.get("quarantined"):
        return "data_quarantined"
    if "max_stop_atr" in filters and conventions.fill_stop_atr(cfg) is None:
        issue = stop_bound_reason(c.entry, c.stop, signal_atr14(c.features, cfg), cfg)
        if issue:
            return issue
    if c.features.get("surveillance") == "unknown":
        return "surveillance_unknown"
    if c.features.get("surveillance"):
        return "surveillance"
    if "results_strategy" in cfg:
        # D specifies the result itself as its catalyst. Only the funded book uses the gate.
        return "regime" if cfg["results_strategy"]["gate"] and not regime["enabled"] else None
    if c.features.get("earnings_coverage") == "unknown":
        return "results_unknown"
    horizon = filters["earnings_blackout_days"]
    if filters.get("earnings_blackout_mode") == "holding_period":
        horizon = c.features.get("holding_blackout_days")
        if horizon is None or (
            horizon > c.features.get("meeting_coverage_days", 30)
            and c.features.get("earnings_in_days") is None
        ):
            return "results_window_unknown"
    earnings = c.features.get("earnings_in_days")
    if earnings is not None and earnings <= horizon:
        return "upcoming_results"
    if not regime["enabled"]:
        return "regime"
    return None


def advance_from_frame(db, c, day, frames, cfg):
    if c["symbol"] not in frames:
        raise MarketError(f"Missing open-call candle for {c['symbol']} on {day}.")
    f = frames.get(c["isin"], frames[c["symbol"]])
    if str(f.index[-1]) != day:
        raise MarketError(f"Stale open-call candle for {c['symbol']}.")
    fill_cfg = cfg
    if c["revision"] != cfg["revision"]:
        fill_cfg = saved_rules(db, c["revision"])
    if (
        cfg.get("forward_execution_convention") == conventions.VERSION
        and day >= conventions.FORWARD_EFFECTIVE
    ):
        fill_cfg = {**fill_cfg, "execution_convention": conventions.VERSION}
        if cfg.get("entry_band_convention") == conventions.ENTRY_BAND_VERSION:
            fill_cfg["entry_band_convention"] = conventions.ENTRY_BAND_VERSION
    context = f.attrs["exchange"]
    if "compulsory_action" in context:
        previous = corporate.state(db, c["id"])
        if corporate.settle(db, c, day, f.iloc[-1], fill_cfg, context, previous):
            return
        c = corporate.effective_call(
            db.execute("SELECT * FROM calls WHERE id=?", (c["id"],)).fetchone(),
            corporate.state(db, c["id"]),
        )
    if conventions.tracks_holder_bonuses(fill_cfg):
        c, waiting = dip_bonus.prepare(db, c, day, f.iloc[-1], context)
        if waiting:
            return
    advance(db, c, day, f.iloc[-1], fill_cfg, context)


def process(db, run, day, regime, candidates, frames, cfg, *, unconstrained=False):
    """One atomic daily research step for forward observation and historical replay."""
    if run == paper.ENTRY_SHADOW:
        raise MarketError("This shadow is advanced automatically by the observations scan.")
    if unconstrained and run == "observations":
        raise MarketError("Unconstrained replay requires a separate run.")
    day = str(day)
    with db:
        db.execute("BEGIN IMMEDIATE")
        r = cfg["risk"]
        created = db.execute(
            "INSERT OR IGNORE INTO runs VALUES(?,?,?,?)",
            (run, cfg["revision"], r["capital"], r["capital"]),
        ).rowcount
        if created and unconstrained:
            db.execute("INSERT INTO unconstrained_runs VALUES(?)", (run,))
        saved_mode = db.execute("SELECT 1 FROM unconstrained_runs WHERE run=?", (run,)).fetchone()
        if bool(saved_mode) != unconstrained:
            raise MarketError("Portfolio and unconstrained replays need separate run identifiers.")
        state = db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone()
        if state["rules"] != cfg["revision"] and run != "observations":
            raise MarketError("Rules changed. Select a fresh run for this revision.")
        existing = db.execute(
            "SELECT count FROM scans WHERE run=? AND day=?", (run, day)
        ).fetchone()
        if existing:
            return existing[0]
        entry_census = any(
            k in cfg for k in ("dip", "range_bouncers", "results_strategy")
        ) and bool(cfg.get("entry_band_convention"))
        if entry_census:
            from . import dip_coverage

            dip_coverage.capture(db, run, day, candidates, cfg, regime)
        db.execute(
            "INSERT OR IGNORE INTO rule_configs VALUES(?,?)", (cfg["revision"], json.dumps(cfg))
        )
        last = db.execute("SELECT max(day) FROM scans WHERE run=?", (run,)).fetchone()[0]
        calendar = frames["NIFTY 50"].index
        if last and (len(calendar) < 2 or str(calendar[-2]) != last):
            raise MarketError(
                f"Replay every consecutive NSE session after {last} in chronological order."
            )
        corporate.pay_receivables(db, run, day)
        research_journal.enforce(db, run, day)
        books = [run]
        if run == "observations":
            if last:
                paper.pair_signals(db, last)
            corporate.pay_receivables(db, paper.ENTRY_SHADOW, day)
            books.append(paper.ENTRY_SHADOW)
        open_calls = [
            c
            for book in books
            for c in db.execute(
                "SELECT * FROM calls WHERE run=? AND status IN (?,?) ORDER BY id", (book, *ACTIVE)
            )
        ]
        for c in open_calls:
            if c["run"] == paper.ENTRY_SHADOW or unconstrained:
                # An unpriceable independent leg must not interrupt the other calls.
                # A savepoint rolls back any partial corporate settlement for this leg.
                db.execute("SAVEPOINT shadow_step")
                try:
                    advance_from_frame(db, c, day, frames, cfg)
                except UnpricedPosition as exc:
                    db.execute("ROLLBACK TO shadow_step")
                    transition(db, c["id"], day, "unmeasurable", exc.detail["reason"])
                finally:
                    db.execute("RELEASE shadow_step")
            else:
                advance_from_frame(db, c, day, frames, cfg)
        if unconstrained:
            # Each valid signal has its own fixed rupee risk and independent fills and charges.
            # There is no pooled equity or mark from which to fund the next trade.
            for c in db.execute("SELECT * FROM calls WHERE run=? AND status='active'", (run,)):
                position, close, value = paper.valuation(db, c, frames)
                paper.mark_position(db, position, day, close, value)
            count = 0
            budget = r["capital"] * r["fraction"]
            for c in candidates:
                if exclusion(c, cfg, regime) is None:
                    planned_qty = math.floor(budget / (c.entry - c.stop))
                    record(db, run, day, regime, c, cfg, planned_qty, "unconstrained")
                    count += 1
            db.execute("INSERT INTO scans VALUES(?,?,?,?)", (run, day, json.dumps(regime), count))
            return count
        live = db.execute(
            "SELECT * FROM calls WHERE run=? AND status='active' AND allocation='portfolio'", (run,)
        ).fetchall()
        balance = (
            r["capital"]
            + db.execute(
                """SELECT coalesce(sum(
            CASE side WHEN 'sell' THEN qty*price ELSE -qty*price END - fees), 0)
            FROM fills f JOIN calls c ON c.id=f.call_id WHERE c.run=? AND c.allocation='portfolio'""",
                (run,),
            ).fetchone()[0]
        )
        investments = Counter()
        positions = []
        for c in live:
            position, close, value = paper.valuation(db, c, frames)
            investments[c["strategy"]] += value
            if position["remaining"]:
                positions.append(position)
            if run == "observations":
                paper.mark_position(db, position, day, close, value)
        if run == "observations":
            for c in db.execute(
                """SELECT * FROM calls WHERE status='active'
                AND (run=? OR (run='observations' AND allocation!='portfolio'))""",
                (paper.ENTRY_SHADOW,),
            ):
                pos, close, value = paper.valuation(db, c, frames)
                paper.mark_position(db, pos, day, close, value)
        live = positions
        balance += db.execute(
            """SELECT coalesce(sum(amount),0) FROM cash_receipts e JOIN calls c ON c.id=e.call_id
                             WHERE c.run=? AND allocation='portfolio'""",
            (run,),
        ).fetchone()[0]
        receivable = db.execute(
            "SELECT coalesce(sum(q.estimate),0) FROM corporate_receivables q JOIN calls c ON c.id=q.call_id WHERE c.run=? AND c.allocation='portfolio' AND q.paid IS NULL",
            (run,),
        ).fetchone()[0]
        equity = balance + sum(investments.values()) + receivable
        if "NIFTY 200" in frames:
            mark_accounts(
                db,
                run,
                day,
                frames,
                investments,
                equity,
                r["capital"],
                strategies=cfg.get(
                    "book_strategies", ("dip_in_steady_risers",) if "dip" in cfg else STRATEGIES
                ),
            )
        peak = max(equity, state["peak"])
        db.execute("UPDATE runs SET peak=? WHERE id=?", (peak, run))
        paused, unit_risk = brake.funding(
            db, run, day, last, regime["enabled"], equity, state["peak"], cfg
        )
        reserved = sum(max(x["buy"] - x["trail"], 0) * x["remaining"] for x in live)
        sectors = Counter(x["sector"] for x in live)
        held = {x["isin"] for x in live}
        from .ranking import order

        for c in order(candidates, regime, cfg):
            per_share = c.entry - c.stop
            qty = max(0, math.floor(min(equity * unit_risk / per_share, equity / c.entry)))
            reason = "portfolio"
            blocked = exclusion(c, cfg, regime)
            check_capacity = entry_census and blocked in dip_coverage.UNKNOWN_FILTERS
            if blocked and not check_capacity:
                reason = blocked
            elif paused:
                reason = "drawdown"
            elif (
                c.isin in held
                or len(held) >= r["max_positions"]
                or sectors[c.sector] >= r["max_per_sector"]
            ):
                reason = "position_limit"
            else:
                # Reserve entry costs and risk for all candidates confirmed on this close.
                unit_cost = c.entry + cost("buy", c.entry * max(qty, 1), cfg) / max(qty, 1)
                allowed = max(
                    0,
                    math.floor(
                        min(
                            qty,
                            balance / unit_cost,
                            (equity * r["max_open_risk"] - reserved) / per_share,
                        )
                    ),
                )
                if not allowed:
                    reason = "capital_or_risk"
                elif not blocked:
                    qty = allowed
                    held.add(c.isin)
                    sectors[c.sector] += 1
                    balance -= qty * c.entry + cost("buy", qty * c.entry, cfg)
                    reserved += qty * per_share
            if check_capacity:
                # Capacity is observed at this candidate's existing rank. Unknown
                # eligibility never reserves the cash, risk, or sector slot.
                if reason != "portfolio":
                    db.execute(
                        "INSERT INTO dip_funding_veto VALUES(?,?,?,?)",
                        (run, day, c.isin, reason),
                    )
                reason = blocked
            record(db, run, day, regime, c, cfg, qty, reason)
        db.execute(
            "INSERT INTO scans VALUES(?,?,?,?)", (run, day, json.dumps(regime), len(candidates))
        )
        if run == "observations":
            paper.pair_signals(db, day)
            notifier.scan_messages(db, run, day, regime)
            research_journal.queue(db, run, day)
    return len(candidates)


def mark_accounts(db, run, day, frames, invested, equity, capital, *, strategies=STRATEGIES):
    """Daily marked wealth. Strategy books attribute the shared account's trades."""
    cashflow = Counter()
    for row in db.execute(
        """
        SELECT c.strategy,sum((CASE WHEN side='buy' THEN -1 ELSE 1 END)*qty*price-fees) AS amount
        FROM fills f JOIN calls c ON c.id=f.call_id WHERE f.day=? AND c.run=?
        AND c.allocation='portfolio' GROUP BY c.strategy""",
        (day, run),
    ):
        cashflow[row["strategy"]] += row["amount"]
    for row in db.execute(
        """
        SELECT c.strategy,sum(amount) AS amount FROM cash_receipts e JOIN calls c ON c.id=e.call_id
        WHERE e.day=? AND c.run=? AND c.allocation='portfolio' GROUP BY c.strategy""",
        (day, run),
    ):
        cashflow[row["strategy"]] += row["amount"]
    for row in db.execute(
        """SELECT c.strategy,sum((q.issued=?) * q.estimate - (coalesce(q.paid,'')=?) * q.estimate) AS delta
        FROM corporate_receivables q JOIN calls c ON c.id=q.call_id WHERE c.run=? AND c.allocation='portfolio' AND (q.issued=? OR q.paid=?) GROUP BY c.strategy""",
        (day, day, run, day, day),
    ):
        cashflow[row["strategy"]] += row["delta"]
    benchmark = float(frames["NIFTY 200"].iloc[-1].close)
    marks = [(run, day, "portfolio", equity, sum(invested.values()), benchmark)]
    for strategy in strategies:
        prior = db.execute(
            "SELECT equity-invested FROM account_marks WHERE run=? AND book=? "
            "ORDER BY day DESC LIMIT 1",
            (run, strategy),
        ).fetchone()
        cash = (prior[0] if prior else capital) + cashflow[strategy]
        marks.append((run, day, strategy, cash + invested[strategy], invested[strategy], benchmark))
    db.executemany("INSERT INTO account_marks VALUES(?,?,?,?,?,?)", marks)


def metrics(rows):
    rs = [row["outcome_r"] for row in rows if row["status"] == "closed"]
    pnl = [row["net"] for row in rows if row["status"] == "closed"]
    gains = sum(x for x in pnl if x > 0)
    loss = -sum(x for x in pnl if x < 0)
    return {
        "calls": len(rows),
        "closed": len(rs),
        "expectancy_r": sum(rs) / len(rs) if rs else None,
        "profit_factor": gains / loss if loss else None,
        "net_inr": sum(x["net"] or 0 for x in rows),
        "active": sum(x["status"] in ACTIVE for x in rows),
    }


def run_report(db, run):
    rows = db.execute("SELECT * FROM calls WHERE run=? ORDER BY id", (run,)).fetchall()
    portfolio = [r for r in rows if r["allocation"] == "portfolio"]
    return {
        "run": run,
        "portfolio": metrics(portfolio),
        "by_strategy": {
            s: metrics([r for r in portfolio if r["strategy"] == s])
            for s in ("trend_pullback", "high_momentum", "vcp_breakout")
        },
        "shadow": metrics([r for r in rows if r["allocation"] != "portfolio"]),
    }


def record_action(db, call_id, decision):
    if decision not in {"taken", "skip"}:
        raise MarketError("Choose taken or skip.")
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT action FROM calls WHERE id=?", (call_id,)).fetchone()
        if not row:
            raise MarketError(f"Unknown call {call_id}.")
        if row[0] != decision:
            db.execute("UPDATE calls SET action=? WHERE id=?", (decision, call_id))
            event(db, call_id, str(today()), "action", decision)


def read_call(db, call_id):
    row = db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
    if not row:
        raise MarketError(f"Unknown call {call_id}.")
    detail = dict(row)
    for key in ("regime", "features"):
        detail[key] = json.loads(detail[key])
    detail["ai_verdicts"] = [
        dict(v)
        for v in db.execute("SELECT * FROM ai_verdicts WHERE call_id=? ORDER BY id", (call_id,))
    ]
    detail["research_reports"] = [
        dict(v)
        for v in db.execute(
            "SELECT * FROM research_reports WHERE call_id=? ORDER BY report_id", (call_id,)
        )
    ]
    detail["corporate_position"] = corporate.state(db, call_id)
    detail["corporate_cash"] = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM corporate_cash WHERE call_id=? ORDER BY day", (call_id,)
        )
    ]
    detail["entitlements"] = [
        dict(x)
        for x in db.execute("SELECT * FROM entitlements WHERE call_id=? ORDER BY day", (call_id,))
    ]
    for key in ("events", "fills"):
        detail[key] = [
            dict(x)
            for x in db.execute(f"SELECT * FROM {key} WHERE call_id=? ORDER BY id", (call_id,))
        ]
    return detail
