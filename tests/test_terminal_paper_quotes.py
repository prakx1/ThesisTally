import asyncio
from datetime import datetime

from textual.widgets import DataTable, Static

from thesistally import kite_session, market, terminal
from thesistally.dashboard import Dashboard
from thesistally.paper_quotes import paper_live_view


def test_quote_poll_updates_paper_pnl_without_changing_the_close(tmp_path, monkeypatch):
    now = datetime(2026, 11, 23, 11, tzinfo=market.IST)
    monkeypatch.setattr(
        terminal,
        "paper_live_view",
        lambda account, quotes: paper_live_view(account, quotes, now=now),
    )
    data = Dashboard(tmp_path)
    monkeypatch.setattr(kite_session, "check_status", lambda: "Connected")
    monkeypatch.setattr(data, "start_automatic", lambda: None)
    history = {
        "day": "2026-11-20",
        "equity": 1010,
        "cash": 510,
        "invested": 500,
        "daily_pnl": 10,
        "total_pnl": 10,
    }
    trade = {
        "id": 1,
        "day": "2026-11-19",
        "symbol": "ABC",
        "isin": "INE000000001",
        "status": "active",
        "remaining": 5,
        "size": 5,
        "entry": 98,
        "buy": 98,
        "eod_close": 100,
        "marked_pnl": 10,
        "net": None,
        "pending_exit": None,
    }
    book = {"capital": 1000, "history": [history], "trades": [trade], "comparisons": []}
    monkeypatch.setattr(data, "paper_account", lambda: book)
    quote = {
        "timestamp": now.isoformat(),
        "price": 102,
        "previous_close": 100,
        "isin": trade["isin"],
    }
    feed = {"error": None, "quotes": {"ABC": quote}, "requested": 1, "fetched_at": now.isoformat()}
    monkeypatch.setattr(data, "quotes", lambda: feed)

    async def interact():
        app = terminal.RadarTerminal(data)
        async with app.run_test(size=(100, 27)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("4")
            trades = app.query_one("#paper-trades", DataTable)
            estimate = app.query_one("#paper-estimate", Static)
            balance = app.query_one("#paper-balance", Static)
            previous = app.query_one("#paper-history", DataTable).get_row(history["day"])
            assert trades.get_row("1")[5:7] == ["102.00", "20.00"]
            assert "Live" in trades.get_row("1")[7]
            assert "Live estimate · Total P&L ₹20.00" in str(estimate.content)
            assert "Holdings at 2026-11-20 close" in str(estimate.content)
            assert "Recorded close 2026-11-20" in str(balance.content)

            quote["price"] = 105
            app.poll_quotes()
            await app.workers.wait_for_complete()
            assert trades.get_row("1")[5:7] == ["105.00", "35.00"]
            assert "Equity ₹1,035.00" in str(estimate.content)

            feed["error"] = "Offline"
            app.poll_quotes()
            await app.workers.wait_for_complete()
            assert "Live" not in trades.get_row("1")[7]
            assert "Last quote" in str(estimate.content)

            feed["quotes"].clear()
            app.poll_quotes()
            await app.workers.wait_for_complete()
            assert trades.get_row("1")[5:7] == ["100.00", "10.00"]
            assert "EOD" in trades.get_row("1")[7]
            assert "No usable quote" in str(estimate.content)
            assert app.query_one("#paper-history", DataTable).get_row(history["day"]) == previous

            # The next scan can close all holdings and leave plans waiting for entry.
            settled = {**trade, "status": "closed", "remaining": 0, "net": 21}
            pending = {
                **trade,
                "id": 2,
                "status": "confirmed",
                "remaining": 0,
                "eod_close": None,
                "marked_pnl": None,
                "buy": None,
            }
            app.render_paper({**book, "trades": [settled, pending]})
            feed.update(error=None, quotes={"ABC": quote})
            app.poll_quotes()
            await app.workers.wait_for_complete()
            assert not estimate.display
            assert "1 funded plans awaiting entry" in str(
                app.query_one("#paper-status", Static).content
            )
            assert "Recorded close 2026-11-20" in str(balance.content)
            assert trades.get_row("1")[6] == "21.00"
            assert trades.get_row("2")[6] == "—"

    asyncio.run(interact())
