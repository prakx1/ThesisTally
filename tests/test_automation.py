import asyncio
import csv
import io
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from telegram.error import NetworkError
from test_research import CFG, DAY, REGIME, calendar, pick
from typer.testing import CliRunner

from thesistally import cli, daemon, market, notifier
from thesistally.journal import journal, process


class Bot:
    def __init__(self):
        self.updates = []
        self.sent = []
        self.fail = True

    async def get_updates(self, **_):
        return self.updates

    async def send_message(self, **m):
        if self.fail:
            raise NetworkError("offline")
        self.sent.append(m)


def test_delivery_retry_callback_and_script_exports(tmp_path, monkeypatch):
    db = journal(tmp_path / "journal.sqlite")
    process(db, "observations", DAY, REGIME, [pick()], calendar(DAY), CFG)
    process(db, "observations", DAY, REGIME, [pick()], calendar(DAY), CFG)
    assert db.execute("select count(*) from telegram_outbox").fetchone()[0] == 2
    bot = Bot()
    asyncio.run(notifier.exchange(db, bot, "42"))
    assert db.execute("select attempts from telegram_outbox order by id limit 1").fetchone()[0] == 1

    async def ack(_):
        pass

    class Callback:
        def __init__(self, chat):
            self.message = SimpleNamespace(chat=SimpleNamespace(id=chat))
            self.data = "tt:1:taken"

        answer = staticmethod(ack)

    bot.fail = False
    with db:
        db.execute("update telegram_outbox set next_attempt=0")
    bot.updates = [SimpleNamespace(update_id=1, callback_query=Callback(9))]
    asyncio.run(notifier.exchange(db, bot, "42"))
    assert db.execute("select action from calls").fetchone()[0] == "unreviewed"
    bot.updates = [SimpleNamespace(update_id=2, callback_query=Callback(42))]
    asyncio.run(notifier.exchange(db, bot, "42"))
    assert db.execute("select action from calls").fetchone()[0] == "taken"
    assert len(bot.sent) == 2 and bot.sent[0]["reply_markup"]
    monkeypatch.setenv("RADAR_DATA", str(tmp_path))
    runner = CliRunner()
    calls = runner.invoke(cli.app, ["calls", "--open", "--strategy", "trend_pullback", "--csv"])
    assert (
        calls.exit_code == 0
        and next(csv.DictReader(io.StringIO(calls.stdout)))["action"] == "taken"
    )
    row = json.loads(runner.invoke(cli.app, ["call", "1", "--json"]).stdout)
    assert row["action"] == "taken" and row["model_version"] == "rs126-v1"
    assert (
        json.loads(runner.invoke(cli.app, ["report", "--by", "regime", "--json"]).stdout)[0][
            "regime"
        ]
        == "pass"
    )


def test_scheduler_week_holiday_and_nightly_restore(tmp_path, monkeypatch):
    data = tmp_path / "data"
    copy = tmp_path / "secondary"
    monkeypatch.setenv("RADAR_BACKUP_DIR", str(copy))
    monkeypatch.setattr(daemon, "inputs", lambda *args: None)
    monkeypatch.setattr(market.Feed, "session", lambda _: None)
    monkeypatch.setattr(
        daemon.httpx,
        "head",
        lambda *a, **kw: SimpleNamespace(status_code=200, raise_for_status=lambda: None),
    )
    monkeypatch.setattr(cli, "collect_session", lambda *args: None)
    monkeypatch.setattr(cli, "scan_session", lambda *args: None)
    monkeypatch.setattr(notifier, "deliver", lambda *args: None)
    monkeypatch.setattr(daemon, "ping", lambda *args: None)
    for n in range(7):
        day = date(2026, 11, 5) + timedelta(days=n)
        for hour, minute in ((8, 30), (16, 0), (23, 5)):
            daemon.tick(
                data, datetime(day.year, day.month, day.day, hour, minute, tzinfo=market.IST)
            )
    db = journal(data / "journal.sqlite")
    days = {
        r[0]
        for r in db.execute("select day from scheduled_jobs where job='scan' and status='done'")
    }
    assert days == {"2026-11-05", "2026-11-06", "2026-11-08", "2026-11-09", "2026-11-11"}
    assert (copy / "journal.sqlite").is_file()
    archived = journal(copy / "journal.sqlite")
    assert archived.execute("pragma quick_check").fetchone()[0] == "ok"
    assert (
        archived.execute(
            "select count(*) from scheduled_jobs where job='scan' and status='done'"
        ).fetchone()[0]
        == 5
    )
