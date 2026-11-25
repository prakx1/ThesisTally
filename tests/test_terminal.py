import asyncio
import json
import threading
from contextlib import closing

import httpx
from textual import events
from textual.widgets import Button, DataTable, Static, TabbedContent

from thesistally import kite_session, market
from thesistally.dashboard import Dashboard, parse_quotes
from thesistally.journal import journal
from thesistally.terminal import Detail, RadarTerminal


def test_quote_refresh_missing_symbol_and_expired_session(tmp_path, monkeypatch):
    dashboard = Dashboard(tmp_path)
    monkeypatch.setattr(dashboard, "archive", lambda: {"members": [{"symbol": "INFY"}]})
    responses = [
        {
            "data": {
                "NSE:INFY": {
                    "last_price": 105,
                    "ohlc": {"close": 100},
                    "last_trade_time": "2026-11-20 15:29:10",
                }
            },
            "status": "success",
        },
        None,
    ]

    def fake_get(self, url, *, kite, params, attempts):
        assert url == market.KITE + "/quote" and kite
        assert ("i", "NSE:INFY") in params
        response = responses.pop(0)
        if response is None:
            raise market.MarketError("Refresh the local handoff.")
        return httpx.Response(200, json=response)

    monkeypatch.setattr(market.Feed, "get", fake_get)
    first = dashboard.quotes()
    assert first["requested"] == 4 and list(first["quotes"]) == ["INFY"]
    assert first["quotes"]["INFY"]["timestamp"].endswith("+05:30")
    stale = dashboard.quotes()
    assert stale["error"] and stale["fetched_at"] == first["fetched_at"]
    assert stale["quotes"] == first["quotes"]
    assert (
        parse_quotes({"NSE:A": {"last_price": 3, "ohlc": {"close": 0}}}, ["A"])["A"]["change_pct"]
        is None
    )


def test_terminal_navigation_when_feed_offline(tmp_path, monkeypatch):
    data = Dashboard(tmp_path)
    monkeypatch.setattr(kite_session, "check_status", lambda: "Status unavailable")
    starts = []
    monkeypatch.setattr(data, "start_automatic", lambda: starts.append(True))
    folder = tmp_path / "backtests"
    folder.mkdir()
    (folder / "demo.json").write_text(
        json.dumps(
            {
                "rules": "fixture",
                "start": "2026-10-01",
                "end": "2026-10-30",
                "folds": [],
                "passes_measured_thresholds": {"trend_pullback": False},
            }
        )
    )
    with closing(journal(tmp_path / "journal.sqlite")) as db, db:
        db.execute("INSERT INTO runs VALUES('observations','r1',100,100)")
        db.execute("""INSERT INTO calls(run,day,symbol,isin,sector,strategy,revision,regime,features,
            entry,stop,target,size,allocation,status) VALUES('observations','2026-11-20','INFY',
            'INE123','IT','trend_pullback','r1','{}','{}',100,90,120,1,'portfolio','confirmed')""")
        db.execute("INSERT INTO scans VALUES('observations','2026-11-20','{}',1)")
    monkeypatch.setattr(
        data,
        "quotes",
        lambda: {
            "quotes": {},
            "fetched_at": None,
            "requested": 0,
            "error": "Offline",
            "interval": 5,
        },
    )
    monkeypatch.setattr(
        data,
        "archive",
        lambda: {
            "session": "2026-11-20",
            "members": [{"symbol": "INFY", "sector": "IT", "close": 100}],
        },
    )

    async def interact():
        app = RadarTerminal(data)
        async with app.run_test(size=(110, 32)) as pilot:
            await app.workers.wait_for_complete()
            assert starts == [True]
            await pilot.press("slash", "i", "n", "f", "y", "enter")
            table = app.query_one("#prices-table", DataTable)
            assert table.row_count == 1 and "EOD" in table.get_row("INFY")[-1]
            await pilot.press("2", "enter")
            assert isinstance(app.screen, Detail) and "INFY" in app.screen.body
            await pilot.click("#skip")
            await pilot.pause()
            assert data.call(1)["action"] == "skip"
            await pilot.press("3", "enter")
            assert isinstance(app.screen, Detail) and "Research only" in app.screen.body
            await pilot.press("escape")
            assert app.query_one("#tabs", TabbedContent).active == "reports-pane"
            await pilot.press("4")
            assert app.query_one("#tabs", TabbedContent).active == "paper-pane"
            trades = app.query_one("#paper-trades", DataTable)
            assert trades.get_row("1")[1:5] == ["INFY", "Awaiting entry", "1", "0"]
            await pilot.press("enter")
            assert isinstance(app.screen, Detail) and app.screen.heading == "Paper trade #1"
            await pilot.press("escape")
            history = app.query_one("#paper-history", DataTable)
            assert history.get_row("2026-11-20")[1:] == ["100.00", "100.00", "0.00", "0.00", "0.00"]
            history.focus()
            await pilot.press("enter")
            assert app.screen.body == "No fills this session."

    asyncio.run(interact())


def test_kite_login_and_status_in_the_terminal(tmp_path, monkeypatch):
    data = Dashboard(tmp_path)
    monkeypatch.setattr(data, "start_automatic", lambda: None)
    monkeypatch.setattr(data, "quotes", lambda: {"quotes": {}})
    started, release = threading.Event(), threading.Event()
    session = "Login required"
    opened = []

    def check():
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError()
        return session

    def login():
        opened.append(True)
        return "Complete sign-in in the browser."

    monkeypatch.setattr(kite_session, "check_status", check)
    monkeypatch.setattr(kite_session, "open_login", login)

    async def interact():
        nonlocal session
        app = RadarTerminal(data)
        notices = []
        monkeypatch.setattr(
            app.kite_controls, "notify", lambda message, **kwargs: notices.append(message)
        )
        async with app.run_test(size=(80, 24)) as pilot:
            assert await asyncio.to_thread(started.wait, 2)
            status = app.query_one("#kite-status", Static)
            assert "Checking" in str(status.content)
            assert app.query_one("#kite-check", Button).disabled
            # Navigating away must work while the profile request is in flight.
            await pilot.press("3")
            assert app.query_one("#tabs", TabbedContent).active == "reports-pane"
            release.set()
            await app.workers.wait_for_complete()
            assert "Login required" in str(status.content) and "IST" in str(status.content)

            await pilot.click("#kite-login")
            await app.workers.wait_for_complete()
            assert opened == [True] and notices == ["Complete sign-in in the browser."]
            assert "Login required" in str(status.content)
            session = "Connected"
            app.post_message(events.AppFocus())
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert "Connected" in str(status.content)

            session = "Status unavailable"
            await pilot.click("#kite-check")
            await app.workers.wait_for_complete()
            assert "Status unavailable" in str(status.content)
            session = "Login required"
            await pilot.press("f5")
            await app.workers.wait_for_complete()
            assert "Login required" in str(status.content)

            def unavailable():
                raise market.MarketError("Login helper unavailable.")

            monkeypatch.setattr(kite_session, "open_login", unavailable)
            await pilot.press("f6")
            await app.workers.wait_for_complete()
            assert notices[-1] == "Login helper unavailable."
            assert not app.query_one("#kite-login", Button).disabled

    asyncio.run(interact())


def test_terminal_automatic_scan_waits_for_close_and_trading_calendar(tmp_path, monkeypatch):
    from datetime import datetime

    from thesistally import daemon, dashboard, research_worker

    data = Dashboard(tmp_path)
    now = datetime(2026, 11, 20, 15, 44, tzinfo=market.IST)

    class FixedTime(datetime):
        @classmethod
        def now(cls, tz):
            return now

    scans = []
    monkeypatch.setattr(dashboard, "datetime", FixedTime)
    monkeypatch.setattr(research_worker, "start_pending", lambda root: None)
    monkeypatch.setattr(daemon, "scan_after_close", lambda root, day: scans.append(day) or True)
    data._automatic()
    assert not scans
    now = now.replace(minute=45)
    data._automatic()
    assert scans == [now.date()] and "scan complete" in data.activity()["message"]
    now = now.replace(day=21)
    data._automatic()
    assert len(scans) == 1


def test_stage2_report_can_be_selected_alongside_legacy_backtests(tmp_path):
    from thesistally.terminal import report_text

    data = Dashboard(tmp_path)
    folder = tmp_path / "backtests"
    folder.mkdir()
    report = {
        "type": "stage2_baseline",
        "start": "2014-01-01",
        "end": "2026-11-20",
        "rules": "Stage 2 · r2 and r1",
        "terminal_summary": "Equal-risk trades by strategy",
    }
    (folder / "stage2.json").write_text(json.dumps(report))
    (row,) = data.reports()
    assert row["holdout"] == {}
    assert row["rules"] == report["rules"]
    assert report_text(data.report(row["id"])) == report["terminal_summary"]


def test_reports_show_combined_stage3_study_with_machine_inputs_present(tmp_path):
    from thesistally.terminal import report_text

    data = Dashboard(tmp_path)
    folder = tmp_path / "backtests"
    folder.mkdir()
    (folder / "dip.json").write_text(json.dumps({"variants": {}, "proof": {}}))
    report = {
        "type": "stage3_study",
        "rules": "Stage 3",
        "start": "2017-01-02",
        "end": "2026-11-20",
        "terminal_summary": "Steady momentum and Dip in steady risers",
    }
    (folder / "study.json").write_text(json.dumps(report))
    (row,) = data.reports()
    assert report_text(data.report(row["id"])) == report["terminal_summary"]


def test_superseded_entry_study_keeps_archived_bytes_and_shows_status(tmp_path):
    folder = tmp_path / "backtests"
    folder.mkdir()
    path = folder / "stage3-study.json"
    raw = json.dumps(
        {
            "type": "stage3_study",
            "rules": "Stage 3",
            "start": "2017-01-02",
            "end": "2026-11-20",
            "terminal_summary": "Original measurements",
        }
    )
    path.write_text(raw)
    data = Dashboard(tmp_path)
    (row,) = data.reports()
    assert "Superseded" in row["rules"]
    assert data.report(row["id"])["terminal_summary"].startswith("Superseded for decisions")
    assert path.read_text() == raw
