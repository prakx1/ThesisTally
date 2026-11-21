"""Private Telegram delivery. Scans only append local outbox records."""

import asyncio
import json
import os
import re
import time
from contextlib import closing
from pathlib import Path

from telegram.error import TelegramError

from . import market

SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_outbox (
    id INTEGER PRIMARY KEY, dedupe TEXT NOT NULL UNIQUE, text TEXT NOT NULL,
    call_id INTEGER, attachment TEXT, attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt REAL NOT NULL DEFAULT 0, sent_at REAL, error TEXT
);
CREATE TABLE IF NOT EXISTS telegram_cursor (id INTEGER PRIMARY KEY CHECK(id=1), offset INTEGER NOT NULL);
"""
STRATEGY = {
    "trend_pullback": "trend pullback",
    "high_momentum": "52-week high momentum",
    "vcp_breakout": "volatility contraction breakout",
}


def enqueue(db, key, body, call_id=None, attachment=None):
    db.execute(
        "INSERT OR IGNORE INTO telegram_outbox(dedupe,text,call_id,attachment) VALUES(?,?,?,?)",
        (key, body, call_id, attachment),
    )


def scan_messages(db, run, day, regime):
    if run != "observations":
        return
    events = db.execute(
        """SELECT c.*,e.kind,e.detail,e.id AS event_id FROM events e
        JOIN calls c ON c.id=e.call_id WHERE c.run=? AND e.day=? AND c.allocation='portfolio'
        ORDER BY e.id""",
        (run, day),
    ).fetchall()
    for c in events:
        cid = c["id"]
        button = None
        if c["kind"] == "confirmed":
            rr = (c["target"] - c["entry"]) / (c["entry"] - c["stop"])
            status = "pass" if json.loads(c["regime"])["enabled"] else "fail"
            why = {
                "trend_pullback": "Reclaimed the prior high after a pullback to the fast EMA.",
                "high_momentum": "Top-ranked relative strength, a base breakout and expanding volume.",
                "vcp_breakout": "Price broke above contracting ranges on higher volume.",
            }[c["strategy"]]
            msg = (
                f"Research plan · #{cid} {c['symbol']} · {c['sector']}\n"
                f"{STRATEGY[c['strategy']]} · {c['revision']}\n"
                f"Entry ₹{c['entry']:.2f} · stop ₹{c['stop']:.2f} · target ₹{c['target']:.2f}\n"
                f"Reward/risk {rr:.2f} · simulated quantity {c['size']} · regime {status}\n{why}\nFor private research only."
            )
            button = cid
        elif c["kind"] in {"missed", "invalidated", "fill", "closed", "exit_due", "exit_deferred"}:
            msg = f"Research update · #{cid} {c['symbol']} · {c['kind']}\n{c['detail']}"
        else:
            continue
        enqueue(db, f"event:{c['event_id']}", msg, button)
    new, shadow = db.execute(
        "SELECT count(*),coalesce(sum(allocation!='portfolio'),0) FROM calls WHERE run=? AND day=?",
        (run, day),
    ).fetchone()
    opened = db.execute(
        "SELECT count(*) FROM calls WHERE run=? AND allocation='portfolio' AND status IN ('confirmed','active')",
        (run,),
    ).fetchone()[0]
    closed, net = db.execute(
        """SELECT count(*),coalesce(sum(c.outcome_r),0) FROM events e JOIN calls c ON c.id=e.call_id
          WHERE c.run=? AND e.day=? AND e.kind='closed' AND c.allocation='portfolio'""",
        (run, day),
    ).fetchone()
    enqueue(
        db,
        f"summary:{day}",
        f"Research summary · {day}\n{new} new ({shadow} shadow) · {opened} open or awaiting entry · {closed} closed\nRealized net {net:+.2f}R · regime {'pass' if regime['enabled'] else 'fail'}",
    )


def warning(root, key, message):
    from .journal import journal

    with closing(journal(root / "journal.sqlite")) as db, db:
        enqueue(db, "warning:" + key, "ThesisTally system warning\n" + message)


async def exchange(db, bot, chat):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    from .journal import record_action

    saved = db.execute("SELECT offset FROM telegram_cursor WHERE id=1").fetchone()
    updates = await bot.get_updates(
        offset=saved[0] if saved else None, timeout=0, allowed_updates=["callback_query"]
    )
    for update in updates:
        cb = update.callback_query
        if cb and cb.message and str(cb.message.chat.id) == str(chat):
            match = re.fullmatch(r"tt:(\d+):(taken|skip)", cb.data or "")
            allowed = (
                match
                and db.execute(
                    "SELECT 1 FROM calls WHERE id=? AND run='observations' AND allocation='portfolio'",
                    (int(match[1]),),
                ).fetchone()
            )
            if allowed:
                record_action(db, int(match[1]), match[2])
                await cb.answer("Recorded")
        with db:
            db.execute(
                "INSERT OR REPLACE INTO telegram_cursor VALUES(1,?)", (update.update_id + 1,)
            )
    for row in db.execute(
        "SELECT * FROM telegram_outbox WHERE sent_at IS NULL AND next_attempt<=? ORDER BY id LIMIT 80",
        (time.time(),),
    ).fetchall():
        # A short lease prevents two daemon instances from sending the same pending row.
        with db:
            claimed = db.execute(
                "UPDATE telegram_outbox SET next_attempt=? WHERE id=? AND sent_at IS NULL AND next_attempt<=?",
                (time.time() + 120, row["id"], time.time()),
            ).rowcount
        if not claimed:
            continue
        buttons = None
        if row["call_id"]:
            buttons = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(label, callback_data=f"tt:{row['call_id']}:{choice}")
                        for label, choice in [("Taken", "taken"), ("Skip", "skip")]
                    ]
                ]
            )
        try:
            if row["attachment"]:
                path = Path(row["attachment"])
                document = await asyncio.to_thread(path.read_bytes)
                await bot.send_document(
                    chat_id=chat, document=document, filename=path.name, caption=row["text"][:1024]
                )
            else:
                await bot.send_message(chat_id=chat, text=row["text"][:4096], reply_markup=buttons)
        except (TelegramError, OSError, TimeoutError) as exc:
            with db:
                db.execute(
                    "UPDATE telegram_outbox SET attempts=attempts+1,next_attempt=?,error=? WHERE id=?",
                    (
                        time.time() + min(3600, 10 * 2 ** min(row["attempts"], 8)),
                        type(exc).__name__,
                        row["id"],
                    ),
                )
            break
        else:
            with db:
                db.execute(
                    "UPDATE telegram_outbox SET sent_at=?,error=NULL WHERE id=?",
                    (time.time(), row["id"]),
                )


def deliver(root):
    from telegram import Bot

    from .journal import journal

    key, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (key and chat):
        return
    if not re.fullmatch(r"-?[0-9]+", chat):
        raise market.MarketError("Configure a numeric private chat ID for research delivery.")

    async def work():
        async with Bot(key) as bot:
            info = await bot.get_chat(int(chat))
            if info.type != "private" and (
                info.username or getattr(info, "active_usernames", None)
            ):
                raise market.MarketError("Configure a private chat for research delivery.")
            with closing(journal(root / "journal.sqlite")) as db:
                await exchange(db, bot, chat)

    asyncio.run(work())
