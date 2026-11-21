import asyncio
import json
from contextlib import closing

import httpx
from textual.widgets import DataTable, TabbedContent

from thesistally import market
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
