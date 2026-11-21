"""Append-only events and simulated fills, committed once per completed session."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from pathlib import Path

from . import corporate, filings, notifier, paper, research_journal
from .market import MarketError, today

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, rules TEXT NOT NULL, capital REAL NOT NULL, peak REAL NOT NULL
);
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
        + corporate.SCHEMA
        + paper.SCHEMA
        + """
        CREATE TABLE IF NOT EXISTS fill_securities (
            fill_id INTEGER PRIMARY KEY REFERENCES fills(id), isin TEXT NOT NULL
        );
        CREATE VIEW IF NOT EXISTS cash_receipts AS SELECT call_id,day,amount FROM entitlements
            UNION ALL SELECT call_id,day,amount FROM corporate_cash;
    """
    )
    if "model_version" not in {r[1] for r in db.execute("PRAGMA table_info(calls)")}:
        with db:
            db.execute("BEGIN IMMEDIATE")
            if "model_version" not in {r[1] for r in db.execute("PRAGMA table_info(calls)")}:
                db.execute(
                    "ALTER TABLE calls ADD COLUMN model_version TEXT NOT NULL DEFAULT 'rs126-v1'"
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


def close_call(db, c, day, price, cfg, reason):
    fill(db, c, day, c["remaining"], price, "sell", cfg)
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


def advance(db, call, day, bar, cfg, context):
    c = dict(call)
    slip = cfg["cost"]["slippage_bps"] / 10000
    issue = context.get("unavailable")
    if context.get("quarantined"):
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
        if context["ceiling"] is None or bar.open >= context["ceiling"] - 0.005 or not bar.volume:
            return transition(db, c["id"], day, "missed", "Opening liquidity or upper price band")
        buy = float(bar.open) * (1 + slip)
        if buy <= c["stop"]:
            return transition(
                db, c["id"], day, "invalidated", "Opening fill crossed the planned stop"
            )
        if (c["target"] - buy) / (buy - c["stop"]) < cfg["exits"]["target_r"]:
            return transition(
                db, c["id"], day, "missed", "Opening gap or slippage reduced reward/risk"
            )
        fill(db, c, day, c["size"], buy, "buy", cfg)
        c.update(buy=buy, remaining=c["size"], trail=c["stop"])
        db.execute(
            "UPDATE calls SET buy=?,remaining=?,trail=? WHERE id=?",
            (buy, c["remaining"], c["trail"], c["id"]),
        )
        transition(db, c["id"], day, "active", "Next NSE session open")
    if c["pending_exit"]:
        if context["floor"] is None or bar.open <= context["floor"] + 0.005 or not bar.volume:
            db.execute(
                "UPDATE calls SET age=age+1, mae=min(mae,?), mfe=max(mfe,?) WHERE id=?",
                (
                    (bar.low - c["buy"]) / (c["buy"] - c["stop"]),
                    (bar.high - c["buy"]) / (c["buy"] - c["stop"]),
                    c["id"],
                ),
            )
            event(db, c["id"], day, "exit_deferred", "Opening liquidity or lower price band")
            return
        # Exit before observing this day's high or low.
        adverse = min(c["mae"], (bar.open - c["buy"]) / (c["buy"] - c["stop"]))
        favorable = max(c["mfe"], (bar.open - c["buy"]) / (c["buy"] - c["stop"]))
        db.execute(
            "UPDATE calls SET age=age+1,mae=?,mfe=? WHERE id=?", (adverse, favorable, c["id"])
        )
        return close_call(db, c, day, bar.open * (1 - slip), cfg, c["pending_exit"])
    risk = c["buy"] - c["stop"]
    mfe = max(c["mfe"], (bar.high - c["buy"]) / risk)
    mae = min(c["mae"], (bar.low - c["buy"]) / risk)
    age = c["age"] + 1
    db.execute("UPDATE calls SET age=?,mae=?,mfe=? WHERE id=?", (age, mae, mfe, c["id"]))
    if not c["partial"] and bar.high >= c["target"]:
        qty = c["size"] // 2
        if not qty:
            return close_call(
                db, c, day, max(bar.open, c["target"]) * (1 - slip), cfg, "Target, single share"
            )
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
                        entry,stop,target,size,allocation,status,model_version)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'detected',?)""",
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
        ),
    )
    i = cur.lastrowid
    event(db, i, day, "detected")
    transition(db, i, day, "watching", allocation)
    if qty == 0:
        transition(db, i, day, "invalidated", "Cannot fund one share at the planned risk")
    else:
        transition(db, i, day, "confirmed", "Completed daily candle")


def exclusion(c, cfg, regime):
    filters = cfg["filters"]
    if c.features.get("quarantined"):
        return "data_quarantined"
    distance = c.entry - c.stop
    if "max_stop_atr" in filters and (
        not filters["min_stop_fraction"] <= distance / c.entry <= filters["max_stop_fraction"]
        or distance > filters["max_stop_atr"] * c.features["atr"]
    ):
        return "stop_out_of_bounds"
    if c.features.get("surveillance") == "unknown":
        return "surveillance_unknown"
    if c.features.get("surveillance"):
        return "surveillance"
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


def process(db, run, day, regime, candidates, frames, cfg):
    """One atomic daily research step for forward observation and historical replay."""
    day = str(day)
    with db:
        db.execute("BEGIN IMMEDIATE")
        r = cfg["risk"]
        db.execute(
            "INSERT OR IGNORE INTO runs VALUES(?,?,?,?)",
            (run, cfg["revision"], r["capital"], r["capital"]),
        )
        state = db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone()
        if state["rules"] != cfg["revision"] and run != "observations":
            raise MarketError("Rules changed. Select a fresh run for this revision.")
        existing = db.execute(
            "SELECT count FROM scans WHERE run=? AND day=?", (run, day)
        ).fetchone()
        if existing:
            return existing[0]
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
        open_calls = db.execute(
            "SELECT * FROM calls WHERE run=? AND status IN (?,?) ORDER BY id", (run, *ACTIVE)
        ).fetchall()
        for c in open_calls:
            if c["symbol"] not in frames:
                raise MarketError(f"Missing open-call candle for {c['symbol']} on {day}.")
            f = frames.get(c["isin"], frames[c["symbol"]])
            if str(f.index[-1]) != day:
                raise MarketError(f"Stale open-call candle for {c['symbol']}.")
            fill_cfg = cfg
            if c["revision"] != cfg["revision"]:
                fill_cfg = saved_rules(db, c["revision"])
            context = f.attrs["exchange"]
            if "compulsory_action" in context:
                previous = corporate.state(db, c["id"])
                if corporate.settle(db, c, day, f.iloc[-1], fill_cfg, context, previous):
                    continue
                c = corporate.effective_call(
                    db.execute("SELECT * FROM calls WHERE id=?", (c["id"],)).fetchone(),
                    corporate.state(db, c["id"]),
                )
            advance(db, c, day, f.iloc[-1], fill_cfg, context)
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
            frame = frames.get(c["isin"], frames[c["symbol"]])
            p = (
                corporate.state(db, c["id"])
                if "compulsory_action" in frame.attrs["exchange"]
                else None
            )
            frozen = p and p["phase"] != "trading"
            close = None if frozen else float(frame.iloc[-1].close)
            value = p["frozen_value"] if frozen else c["remaining"] * close
            investments[c["strategy"]] += value
            position = corporate.effective_call(c, p)
            positions.append(position)
            if run == "observations":
                paper.mark_position(db, position, day, close, value)
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
            mark_accounts(db, run, day, frames, investments, equity, r["capital"])
        peak = max(equity, state["peak"])
        db.execute("UPDATE runs SET peak=? WHERE id=?", (peak, run))
        dd = 1 - equity / peak
        unit_risk = r["fraction"] * (0.5 if dd >= r["half_risk_drawdown"] else 1)
        reserved = sum(max(x["buy"] - x["trail"], 0) * x["remaining"] for x in live)
        sectors = Counter(x["sector"] for x in live)
        held = {x["isin"] for x in live}
        from .ranking import order

        for c in order(candidates, regime, cfg):
            per_share = c.entry - c.stop
            qty = max(0, math.floor(min(equity * unit_risk / per_share, equity / c.entry)))
            reason = "portfolio"
            blocked = exclusion(c, cfg, regime)
            if blocked:
                reason = blocked
            elif dd >= r["pause_drawdown"]:
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
                if allowed:
                    qty = allowed
                    held.add(c.isin)
                    sectors[c.sector] += 1
                    balance -= qty * c.entry + cost("buy", qty * c.entry, cfg)
                    reserved += qty * per_share
                else:
                    reason = "capital_or_risk"
            record(db, run, day, regime, c, cfg, qty, reason)
        db.execute(
            "INSERT INTO scans VALUES(?,?,?,?)", (run, day, json.dumps(regime), len(candidates))
        )
        if run == "observations":
            notifier.scan_messages(db, run, day, regime)
            research_journal.queue(db, run, day)
    return len(candidates)


STRATEGIES = ("trend_pullback", "high_momentum", "vcp_breakout")


def mark_accounts(db, run, day, frames, invested, equity, capital):
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
    for strategy in STRATEGIES:
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
