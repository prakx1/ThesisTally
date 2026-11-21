"""Daily valuations and account history for the forward paper portfolio."""

import json

from . import corporate

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_position_marks (
    call_id INTEGER NOT NULL REFERENCES calls(id), day TEXT NOT NULL, symbol TEXT NOT NULL,
    qty INTEGER NOT NULL, close REAL, value REAL NOT NULL, pnl REAL NOT NULL,
    PRIMARY KEY(call_id,day)
);
"""


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
        return {"capital": None, "history": [], "trades": []}
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
    return {"capital": account["capital"], "history": history, "trades": trades}
