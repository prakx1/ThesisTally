from contextlib import closing
from datetime import datetime, timedelta

import pandas as pd
import pytest
from test_research import CFG, DAY, MARKET, REGIME, calendar, pick, prices

from thesistally import market
from thesistally.dashboard import Dashboard
from thesistally.journal import journal, process
from thesistally.paper_quotes import paper_live_view

NOW = datetime.fromisoformat("2026-10-04T10:00:00+05:30")


def payload(**changes):
    return {
        "quotes": {
            "A": {
                "isin": "A",
                "price": 96.0,
                "previous_close": 93.0,
                "timestamp": NOW.isoformat(),
                **changes,
            }
        }
    }


def test_marks_use_remaining_shares_plus_booked_sales_fees_and_dividends(tmp_path):
    dashboard = Dashboard(tmp_path)

    def scan(db, day, candidates, bar, dividend=0):
        frames = calendar(day)
        frame = pd.DataFrame([vars(bar)], index=[day])
        frame.attrs["exchange"] = {**MARKET, "dividend": dividend}
        frames.update({"A": frame, "NIFTY 200": frame})
        process(db, "observations", day, REGIME, candidates, frames, CFG)

    with closing(journal(tmp_path / "journal.sqlite")) as db:
        scan(db, DAY, [pick()], prices())
        pending = paper_live_view(dashboard.paper_account(), payload(), now=NOW)
        assert pending["held_count"] == 0 and pending["total_pnl"] == 0
        assert next(iter(pending["positions"].values()))["pnl"] is None

        d1, d2, d3 = (DAY + timedelta(days=i) for i in (1, 2, 3))
        scan(db, d1, [], prices(high=125, close=115))
        scan(db, d2, [], prices(low=89, close=93), dividend=2)
        account = dashboard.paper_account()
        row, day = account["trades"][0], account["history"][0]
        qty, cid = row["remaining"], row["id"]
        assert 0 < qty < row["size"] and row["pending_exit"] == "Closing stop"
        first = paper_live_view(account, payload(), now=NOW)
        expected_equity = day["cash"] + 96 * qty
        assert first["positions"][cid] == {
            "price": 96.0,
            "pnl": pytest.approx(expected_equity - account["capital"]),
            "as_of": NOW.isoformat(),
            "source": "Live",
        }
        assert first["equity"] == pytest.approx(expected_equity)
        assert first["total_pnl"] == pytest.approx(first["positions"][cid]["pnl"])
        assert first["live_count"] == first["held_count"] == first["quoted_count"] == 1
        second = paper_live_view(account, payload(price=91.0), now=NOW)
        assert second["equity"] == pytest.approx(expected_equity - qty * 5)
        assert dashboard.paper_account() == account

        scan(db, d3, [], prices(open_=85))
        closed = dashboard.paper_account()
        view = paper_live_view(closed, payload(), now=NOW)
        assert view["held_count"] == view["live_count"] == 0
        assert view["positions"][cid]["source"] == "Settled"
        assert view["positions"][cid]["pnl"] == closed["trades"][0]["net"]
        assert view["total_pnl"] == pytest.approx(closed["trades"][0]["net"])


def simple_account():
    return {
        "trades": [
            {
                "id": 1,
                "isin": "A",
                "status": "active",
                "remaining": 12,
                "net": None,
                "eod_close": 93.0,
                "marked_pnl": -90.0,
            }
        ],
        "history": [{"day": "2026-10-03", "equity": 9910.0, "total_pnl": -90.0}],
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"timestamp": "bad"},
        {"timestamp": None},
        {"timestamp": "2026-10-04T09:59:00"},
        {"timestamp": "2026-10-03T15:29:59+05:30"},
        {"timestamp": "2026-10-04T10:01:00+05:30"},
        {"price": 0},
        {"price": float("nan")},
        {"previous_close": 46.5},
        {"previous_close": None},
        {"isin": "DIFFERENT"},
    ],
)
def test_mismatched_stale_basis_or_invalid_ticks_keep_the_official_mark(updates):
    view = paper_live_view(simple_account(), payload(**updates), now=NOW)
    assert view["positions"][1] == {
        "price": 93.0,
        "pnl": -90.0,
        "source": "EOD",
        "as_of": "2026-10-03",
    }
    assert view["equity"] == 9910 and view["quoted_count"] == 0


def test_cached_quotes_frozen_positions_and_unknown_values_are_explicit():
    book = simple_account()
    template = book["trades"][0]
    book["trades"].extend(
        [
            {**template, "id": 2, "isin": "B", "eod_close": None, "marked_pnl": 5},
            {**template, "id": 3, "isin": "C", "eod_close": None, "marked_pnl": None},
        ]
    )
    quotes = payload()
    for isin in ["B", "C"]:
        quotes["quotes"][isin] = {**quotes["quotes"]["A"], "isin": isin}
    views = [
        paper_live_view(book, {**quotes, "error": "offline"}, now=NOW),
        paper_live_view(book, quotes, now=NOW + timedelta(hours=1)),
    ]
    for view in views:
        assert view["positions"][1]["source"] == "Last quote"
        assert view["positions"][1]["as_of"] == NOW.isoformat()
        assert view["positions"][2] == {
            "source": "Frozen",
            "price": None,
            "as_of": "2026-10-03",
            "pnl": 5,
        }
        assert view["positions"][3] == {"source": "—", "price": None, "as_of": None, "pnl": None}
        assert (view["live_count"], view["quoted_count"], view["held_count"]) == (0, 1, 3)
        assert view["equity"] == 9910 + 36
    book["history"][0].update(equity=None, total_pnl=None)
    unknown = paper_live_view(book, quotes, now=NOW)
    assert unknown["equity"] is unknown["total_pnl"] is None


def test_market_close_keeps_the_last_trade_without_calling_it_live():
    last_trade = NOW.replace(hour=15, minute=29, second=58)
    view = paper_live_view(
        simple_account(),
        payload(timestamp=last_trade.isoformat()),
        now=last_trade + timedelta(seconds=3),
    )
    assert view["positions"][1]["source"] == "Last quote"
    assert view["as_of"] == last_trade.isoformat()


def test_held_isin_outside_current_universe_is_quoted_only_with_an_identified_last_trade(
    tmp_path, monkeypatch
):
    dash = Dashboard(tmp_path, quote_provider="kite")
    day = "2026-10-03"
    folder = tmp_path / "sessions" / day
    folder.mkdir(parents=True)
    (folder / "bhavcopy.csv").write_text(
        f"TradDt,TckrSymb,SctySrs,ISIN,FinInstrmId\n{day},NEW,EQ,A,10\n{day},OLD,EQ,REUSED,11\n"
    )
    (folder / "instruments.csv").write_text(
        "segment,tradingsymbol,instrument_token,exchange_token\nNSE,NEW,2561,10\nNSE,OLD,2817,11\n"
    )
    monkeypatch.setattr(dash, "archive", lambda: {"session": day, "members": [{"symbol": "OLD"}]})
    dash._held_isins = frozenset(["A"])
    quote = {
        "instrument_token": 2561,
        "last_price": 96,
        "ohlc": {"close": 93},
        "last_trade_time": NOW.isoformat(),
        "timestamp": NOW.isoformat(),
    }

    class Feed:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def get(self, url, **kwargs):
            assert ("i", "NSE:NEW") in kwargs["params"] and ("i", "NSE:OLD") in kwargs["params"]
            return self

        def json(self):
            return {
                "status": "success",
                "data": {"NSE:NEW": quote, "NSE:OLD": {**quote, "instrument_token": 2817}},
            }

    monkeypatch.setattr(market, "Feed", Feed)
    quotes = dash.quotes()["quotes"]
    assert quotes["NEW"]["isin"] == "A" and "isin" not in quotes["OLD"]
    quote["instrument_token"] = 999
    assert "isin" not in dash.quotes()["quotes"]["NEW"]
    quote.update(instrument_token=2561, last_trade_time=None)
    assert "isin" not in dash.quotes()["quotes"]["NEW"]
