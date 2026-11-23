"""Daily valuations and account history for the forward paper portfolio."""

import json
import math

from . import corporate

# Persist the version in each comparison call's run key. The original and shadow
# share the immutable signal key (day, ISIN, strategy), even after a symbol change.
ENTRY_SHADOW = "paper-entry-gap-half-atr-2r-v1"


def pair_signals(db, day):
    """Pair forward plans before their opening auction has been observed."""
    from .journal import event, saved_rules, transition

    rows = db.execute(
        """SELECT c.* FROM calls c WHERE c.run='observations' AND c.day=?
        AND (c.status='confirmed' OR (c.status='invalidated' AND c.size=0))
        AND NOT EXISTS (SELECT 1 FROM calls a WHERE a.run=? AND a.day=c.day
            AND a.isin=c.isin AND a.strategy=c.strategy) ORDER BY c.id""",
        (day, ENTRY_SHADOW),
    ).fetchall()
    if rows:
        db.execute(
            """INSERT OR IGNORE INTO runs SELECT ?,rules,0,0 FROM runs WHERE id='observations'""",
            (ENTRY_SHADOW,),
        )
    atr_periods = {}
    for c in rows:
        cursor = db.execute(
            """INSERT INTO calls(run,day,symbol,isin,sector,strategy,revision,regime,features,
                 entry,stop,target,size,allocation,status,model_version,planned_target,planned_size)
            SELECT ?,day,symbol,isin,sector,strategy,revision,regime,features,
                 entry,stop,target,size,'entry_shadow','detected',model_version,
                 coalesce(planned_target,target),coalesce(planned_size,size) FROM calls WHERE id=?""",
            (ENTRY_SHADOW, c["id"]),
        )
        cid = cursor.lastrowid
        event(db, cid, day, "detected", f"Forward entry comparison for call #{c['id']}")
        features = json.loads(c["features"])
        atr = features.get("atr14")
        if "atr14" not in features and features.get("atr"):
            revision = c["revision"]
            if revision not in atr_periods:
                atr_periods[revision] = saved_rules(db, revision)["trend"]["atr"]
            if atr_periods[revision] == 14:
                atr = features["atr"]
        if not atr or not math.isfinite(atr) or atr <= 0:
            transition(db, cid, day, "unmeasurable", "Missing positive signal ATR(14)")
        elif not c["size"]:
            transition(db, cid, day, "invalidated", "Cannot fund one share at the planned risk")
        else:
            transition(db, cid, day, "confirmed", "Awaiting raw open; target set at fill")
    return len(rows)


def comparisons(db):
    return [
        dict(r)
        for r in db.execute(
            """SELECT c.id,c.day,c.symbol,c.strategy,c.allocation,c.status,c.buy,c.outcome_r,
              a.id AS shadow_id,a.status AS shadow_status,a.buy AS shadow_buy,
              a.outcome_r AS shadow_r
           FROM calls a JOIN calls c ON c.run='observations' AND c.day=a.day
               AND c.isin=a.isin AND c.strategy=a.strategy
           WHERE a.run=? ORDER BY c.day DESC,c.id""",
            (ENTRY_SHADOW,),
        )
    ]


SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_position_marks (
    call_id INTEGER NOT NULL REFERENCES calls(id), day TEXT NOT NULL, symbol TEXT NOT NULL,
    qty INTEGER NOT NULL, close REAL, value REAL NOT NULL, pnl REAL NOT NULL,
    PRIMARY KEY(call_id,day)
);
"""


def valuation(db, call, frames):
    frame = frames.get(call["isin"], frames[call["symbol"]])
    state = (
        corporate.state(db, call["id"]) if "compulsory_action" in frame.attrs["exchange"] else None
    )
    frozen = state and state["phase"] != "trading"
    close = None if frozen else float(frame.iloc[-1].close)
    value = state["frozen_value"] if frozen else call["remaining"] * close
    position = corporate.effective_call(call, state)
    if frame.attrs["exchange"].get("dip_bonus_accounting"):
        from . import dip_bonus

        position, value = dip_bonus.value(db, position, value)
    return position, close, value


def mark_position(db, call, day, close, value):
    """Use the same position value as the account, including compulsory freezes."""
    cid = call["id"]
    db.execute(
        """INSERT INTO paper_position_marks(call_id,day,symbol,qty,close,value,pnl)
        SELECT ?,?,?,?,?,?, ? +
            (SELECT coalesce(sum((CASE side WHEN 'buy' THEN -1 ELSE 1 END)*qty*price-fees),0)
             FROM fills WHERE call_id=?) +
            (SELECT coalesce(sum(amount),0) FROM cash_receipts WHERE call_id=?) +
            (SELECT coalesce(sum(estimate),0) FROM corporate_receivables
             WHERE call_id=? AND paid IS NULL)""",
        (cid, day, call["symbol"], call["remaining"], close, value, value, cid, cid, cid),
    )


def account_history(db, capital):
    # Cash includes dividends and settled corporate actions. Outstanding entitlements
    # belong in the journal's marked equity, not in spendable cash.
    rows = db.execute(
        """WITH flows AS (
            SELECT f.day, sum((CASE f.side WHEN 'buy' THEN -1 ELSE 1 END)*f.qty*f.price-f.fees)
                       AS amount, count(*) AS fills
            FROM calls c JOIN fills f ON f.call_id=c.id
            WHERE c.run='observations' AND c.allocation='portfolio' GROUP BY f.day
            UNION ALL
            SELECT r.day,sum(r.amount),0 FROM calls c JOIN cash_receipts r ON r.call_id=c.id
            WHERE c.run='observations' AND c.allocation='portfolio' GROUP BY r.day
        ), daily AS (SELECT day,sum(amount) AS amount,sum(fills) AS fills FROM flows GROUP BY day)
        SELECT s.day,m.equity,m.invested,d.amount,d.fills FROM scans s
        LEFT JOIN account_marks m ON m.run=s.run AND m.day=s.day AND m.book='portfolio'
        LEFT JOIN daily d ON d.day=s.day WHERE s.run='observations' ORDER BY s.day"""
    )
    cash = previous = capital
    fills = 0
    history = []
    for row in rows:
        cash += row["amount"] or 0
        fills += row["fills"] or 0
        equity, invested = row["equity"], row["invested"]
        if equity is None and fills == 0:
            # Older journals recorded the first scan before daily equity marks existed.
            equity, invested = cash, 0
        history.append(
            {
                "day": row["day"],
                "cash": cash,
                "equity": equity,
                "invested": invested,
                "daily_pnl": equity - previous if None not in (equity, previous) else None,
                "total_pnl": equity - capital if equity is not None else None,
            }
        )
        previous = equity
    return list(reversed(history))


def portfolio(db):
    account = db.execute("SELECT capital FROM runs WHERE id='observations'").fetchone()
    if not account:
        return {"capital": None, "history": [], "trades": [], "comparisons": []}
    history = account_history(db, account["capital"])
    day = history[0]["day"] if history else None
    rows = db.execute(
        """SELECT c.id,c.day,c.symbol,c.isin,c.entry,c.stop,c.target,c.size,c.buy,
                  c.remaining,c.status,c.net,c.pending_exit,p.state,
                  m.close AS eod_close,m.pnl AS marked_pnl
        FROM calls c LEFT JOIN paper_position_marks m ON m.call_id=c.id AND m.day=?
        LEFT JOIN corporate_positions p ON p.call_id=c.id
        WHERE c.run='observations' AND c.allocation='portfolio'
        ORDER BY CASE c.status WHEN 'active' THEN 0 WHEN 'confirmed' THEN 1
                 WHEN 'awaiting_cash' THEN 2 ELSE 3 END, c.day DESC,c.id""",
        (day,),
    )
    trades = []
    for row in rows:
        state = json.loads(row["state"]) if row["state"] else None
        trades.append(corporate.effective_call(row, state))
    return {
        "capital": account["capital"],
        "history": history,
        "trades": trades,
        "comparisons": comparisons(db),
    }
