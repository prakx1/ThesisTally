import asyncio
import json
import threading

from textual.widgets import Button, DataTable, Static

from thesistally import kite_session, research
from thesistally.dashboard import Dashboard
from thesistally.stock_screen import StockReportScreen
from thesistally.terminal import RadarTerminal


def terminal(tmp_path, monkeypatch):
    dashboard = Dashboard(tmp_path)
    monkeypatch.setattr(kite_session, "check_status", lambda: "Status unavailable")
    monkeypatch.setattr(dashboard, "start_automatic", lambda: None)
    monkeypatch.setattr(dashboard, "quotes", lambda: {"quotes": {}})
    monkeypatch.setattr(
        dashboard,
        "archive",
        lambda: {
            "session": "2026-11-20",
            "members": [{"symbol": "SBIN", "sector": "Banks", "close": 800}],
        },
    )
    return RadarTerminal(dashboard)


def test_stock_report_saved_offline_then_reanalyze_once_across_reopen(tmp_path, monkeypatch):
    app = terminal(tmp_path, monkeypatch)
    saved = {"symbol": "SBIN", "as_of": "2026-11-20", "report": {"rating": "hold"}}
    fresh = {**saved, "as_of": "2026-11-23"}
    folder = tmp_path / "research" / "SBIN"
    folder.mkdir(parents=True)
    (folder / "earlier.json").write_text(json.dumps(saved))
    started, release = threading.Event(), threading.Event()
    calls = []

    def generate(root, symbol):
        calls.append((root, symbol))
        started.set()
        assert release.wait(timeout=5)
        return fresh

    monkeypatch.setattr(research, "run_report", generate)

    async def interact():
        async with app.run_test(size=(80, 24)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("enter")  # the first row is an index
            assert not isinstance(app.screen, StockReportScreen)
            await pilot.press("slash", "s", "b", "i", "n", "enter", "enter")
            await app.workers.wait_for_complete()
            view = app.screen
            assert isinstance(view, StockReportScreen) and view.report == saved
            assert not calls
            assert "Reanalyze" in str(view.query_one("#analyze-stock", Button).label)
            assert view.focused.id == "stock-report-scroll"
            await pilot.click("#analyze-stock")
            try:
                assert await asyncio.to_thread(started.wait, 2)
                await pilot.press("a", "escape", "enter")
                assert app.screen is not view and app.screen.symbol == "SBIN"
                await pilot.pause()
                assert app.screen.query_one("#analyze-stock", Button).disabled
                await pilot.press("a")
            finally:
                release.set()
            await app.workers.wait_for_complete()
            assert calls == [(tmp_path, "SBIN")]
            assert app.screen.report == fresh
            assert not app.screen.query_one("#analyze-stock", Button).disabled

    asyncio.run(interact())


def test_empty_stock_report_can_retry_after_failure_and_keep_saved_view(tmp_path, monkeypatch):
    app = terminal(tmp_path, monkeypatch)
    result = {"symbol": "SBIN", "as_of": "2026-11-23", "report": {"rating": "hold"}}
    replies = iter(
        [
            {"status": "ai_unavailable", "reason": "No archived session."},
            result,
            {"status": "disabled"},
        ]
    )
    monkeypatch.setattr(research, "run_report", lambda root, symbol: next(replies))

    async def interact():
        async with app.run_test(size=(70, 24)) as pilot:
            await app.workers.wait_for_complete()
            table = app.query_one("#prices-table", DataTable)
            table.move_cursor(row=table.get_row_index("SBIN"))
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            screen = app.screen
            status = screen.query_one("#stock-report-status", Static)
            assert "No saved report" in str(status.content)
            assert not screen.query_one("#analyze-stock", Button).disabled
            await pilot.press("a")
            await app.workers.wait_for_complete()
            assert "No archived session" in str(status.content) and screen.report is None
            await pilot.press("a")
            await app.workers.wait_for_complete()
            assert screen.report == result
            await pilot.press("a")
            await app.workers.wait_for_complete()
            assert "disabled" in str(status.content) and screen.report == result
            await pilot.press("escape")
            assert app.focused is table

    asyncio.run(interact())


def test_filter_opens_archived_report_without_kite_or_price_history(tmp_path, monkeypatch):
    app = terminal(tmp_path, monkeypatch)
    monkeypatch.setattr(app.data, "archive", lambda: {"session": None, "members": []})
    saved = {"symbol": "SBIN", "as_of": "2026-11-20", "report": {"rating": "hold"}}
    folder = tmp_path / "research" / "SBIN"
    folder.mkdir(parents=True)
    (folder / "archived.json").write_text(json.dumps(saved))

    async def interact():
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("slash", "s", "b", "i", "n", "enter")
            await app.workers.wait_for_complete()
            assert isinstance(app.screen, StockReportScreen) and app.screen.report == saved

    asyncio.run(interact())
