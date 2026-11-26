import asyncio
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from textual import events
from textual.widgets import DataTable, Static

from thesistally import kite_session, market
from thesistally.dashboard import Dashboard
from thesistally.paper_quotes import paper_live_view
from thesistally.stock_screen import StockReportScreen
from thesistally.terminal import RadarTerminal, paper_price_source, price_for_display

NOW = datetime(2026, 11, 23, 11, 0, tzinfo=market.IST)


@pytest.mark.parametrize(
    ("at", "isin", "recorded"),
    [
        ("2026-11-20T10:00:00+05:30", "A", True),
        ("2026-11-19T16:00:00+05:30", "A", True),
        ("2026-11-20T15:35:00+05:30", "A", False),
        ("2026-11-20T10:00:00+05:30", "REUSED", False),
        (None, "A", False),
    ],
)
def test_final_close_overrides_only_an_older_quote_on_the_same_security(at, isin, recorded):
    friday_close = NOW.replace(day=20, hour=15, minute=30)
    quote = {"price": 90, "isin": isin, "timestamp": at, "change_pct": -2, "open": 92}
    archive = {"close": 100, "isin": "A"}
    shown = price_for_display("ABC", {"isin": "A"}, archive, quote, friday_close)
    assert shown == ({"price": 100, "recorded": "2026-11-20"} if recorded else quote)
    assert price_for_display("ABC", {"isin": "A"}, archive, quote, None) == quote
    assert price_for_display("ABC", {"isin": "A"}, {}, quote, friday_close) == quote


@pytest.mark.parametrize(
    ("age_seconds", "cached", "error", "expected"),
    [
        (170, False, None, "Delayed"),
        (301, False, None, "Last quote"),
        (170, True, None, "Last quote"),
        (170, False, "Unavailable", "Last quote"),
    ],
)
def test_nse_paper_marks_follow_exchange_time_and_only_value_the_identified_holding(
    age_seconds, cached, error, expected
):
    trade = {
        "id": 1,
        "status": "active",
        "isin": "INE123",
        "net": None,
        "remaining": 2,
        "eod_close": 100,
        "marked_pnl": 0,
    }
    account = {
        "trades": [trade, {**trade, "id": 2, "isin": "INE456"}],
        "history": [{"day": "2026-11-20", "equity": 1_000, "total_pnl": 0}],
    }
    recorded = deepcopy(account)
    at = NOW - timedelta(seconds=age_seconds)
    quoted = {"price": 110, "previous_close": 100, "timestamp": at.isoformat(), "cached": cached}
    feed = {
        "provider": "nse",
        "interval": 120,
        "error": error,
        "fetched_at": NOW.isoformat(),
        "quotes": {"ABC": {**quoted, "isin": "INE123"}, "DEF": quoted},
    }
    view = paper_live_view(account, feed, now=NOW)
    assert view["live_count"] == 0
    assert view["delayed_count"] == int(expected == "Delayed")
    assert view["quoted_count"] == 1
    assert view["positions"][1]["source"] == expected
    assert view["positions"][1]["as_of"] == at.isoformat()
    assert "NSE Official" in paper_price_source(view["positions"][1], "nse")
    assert view["positions"][2]["source"] == "EOD"
    assert view["total_pnl"] == 20
    assert account == recorded


def test_default_terminal_shows_exchange_and_recorded_times_without_contacting_kite(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("RADAR_QUOTE_PROVIDER", raising=False)
    dash = Dashboard(tmp_path)
    attempted = []
    monkeypatch.setattr(kite_session, "check_status", lambda: attempted.append("check"))
    monkeypatch.setattr(kite_session, "open_login", lambda: attempted.append("login"))
    monkeypatch.setattr(dash, "start_automatic", lambda: None)
    monkeypatch.setattr(
        dash,
        "archive",
        lambda: {
            "session": "2026-11-20",
            "members": [
                {"symbol": "DEF", "isin": "SAME", "close": 50},
                {"symbol": "UNQUOTED", "isin": "OLD", "close": 60},
                {"symbol": "DEPARTED", "close": 70},
                {"symbol": "BSEK", "isin": "BEID", "series": "BE", "close": 75},
            ],
        },
    )
    feed = {
        "provider": "nse",
        "interval": 120,
        "error": None,
        "requested": 6,
        "warning": "Constituent list unavailable",
        "members": [
            {"symbol": "DEF", "isin": "SAME"},
            {"symbol": "UNQUOTED", "isin": "NEW"},
            {"symbol": "BSEK-BE", "exchange_symbol": "BSEK", "isin": "BEID", "series": "BE"},
        ],
        "quotes": {"ABC": {"price": 110, "timestamp": NOW.isoformat()}},
        "fetched_at": (NOW + timedelta(minutes=2)).isoformat(),
    }
    monkeypatch.setattr(dash, "quotes", lambda: feed)

    async def exercise():
        app = RadarTerminal(dash)
        async with app.run_test(size=(125, 32)) as pilot:
            await app.workers.wait_for_complete()
            status = app.query_one("#quote-status", Static)
            table = app.query_one("#prices-table", DataTable)
            assert "NSE Official · 1–3 min delayed" in str(status.content)
            assert "1/6 quoted" in str(status.content)
            assert "23 Nov 11:00:00 IST" in str(status.content)
            assert "120s poll" in str(status.content)
            assert "Constituent list unavailable" in str(status.content)
            assert table.get_row("DEF")[-1] == "EOD 2026-11-20"
            assert table.get_row("UNQUOTED")[1] == "—"
            assert "DEPARTED" not in [key.value for key in table.rows]
            assert not list(app.query("#kite-login"))
            app.post_message(events.AppFocus())
            await pilot.press("f5", "f6")
            await app.workers.wait_for_complete()
            assert attempted == []
            feed["quotes"]["BSEK-BE"] = {
                "price": 77,
                "change_pct": 1,
                "open": 76,
                "high": 78,
                "low": 75,
                "timestamp": "2026-11-20T10:00:00+05:30",
                "cached": True,
            }
            app.poll_quotes()
            await app.workers.wait_for_complete()
            row = table.get_row("BSEK-BE")
            assert [str(v) for v in row[1:]] == ["75.00", "—", "—", "—", "—", "EOD 2026-11-20"]
            await pilot.press("slash", "b", "s", "e", "k", "enter", "enter")
            await app.workers.wait_for_complete()
            assert isinstance(app.screen, StockReportScreen) and app.screen.symbol == "BSEK"
            assert app.screen.quote == "Recorded close ₹75.00 · EOD 2026-11-20"
            await pilot.press("escape")
            app.query_one("#filter").value = ""
            feed["quotes"].pop("BSEK-BE")
            feed["quotes"]["GHI"] = {"price": 106, "timestamp": NOW.isoformat(), "cached": True}
            app.poll_quotes()
            await app.workers.wait_for_complete()
            assert "1/6 quoted · 1 cached" in str(status.content)
            feed["error"] = "Unavailable"
            app.poll_quotes()
            await app.workers.wait_for_complete()
            assert "0/6 quoted · 2 cached" in str(status.content)
            assert "Cached" in table.get_row("ABC")[-1]
            feed["quotes"].clear()
            app.poll_quotes()
            await app.workers.wait_for_complete()
            assert "0/6 quoted" in str(status.content)
            assert "recorded closes" in str(status.content)

    asyncio.run(exercise())
