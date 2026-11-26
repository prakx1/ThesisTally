import json

import httpx
import pytest

from thesistally import nse_quotes
from thesistally.market import MarketError


def equity(symbol, series="EQ", **fields):
    return {
        "type": "CM",
        "symbol": symbol,
        "series": series,
        "lastTradedPrice": 105,
        "preClosePrice": 100,
        "perChange": 99.0,  # Published fields can disagree in the opening auction.
        "latestTimestamp": "2020-11-25 15:30:01",
        **fields,
    }


def equity_feed(*rows):
    return {"segment": "EQUITY", "stocks": list(rows), "updatedAt": "2020-11-26T04:00:00Z"}


@pytest.fixture
def mock_nse(monkeypatch):
    monkeypatch.setattr(nse_quotes, "REQUEST_INTERVAL", 0)
    calls = []

    def client_for(callback):
        def handler(request):
            assert str(request.url) == nse_quotes.ENDPOINT
            assert "authorization" not in request.headers
            if request.method == "DELETE":
                assert request.headers["MCP-Session-Id"] == "fixture"
                calls.append(("terminate", {}))
                return httpx.Response(204)
            body = json.loads(request.content)
            method = body["method"]
            if method == "initialize":
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "result": {"protocolVersion": nse_quotes.PROTOCOL},
                    },
                    headers={"Mcp-Session-Id": "fixture"},
                )
            assert request.headers["MCP-Session-Id"] == "fixture"
            assert request.headers["MCP-Protocol-Version"] == nse_quotes.PROTOCOL
            if method == "notifications/initialized":
                return httpx.Response(202)
            assert method == "tools/call"
            name = body["params"]["name"]
            arguments = body["params"]["arguments"]
            calls.append((name, arguments))
            data = callback(name, arguments)
            if isinstance(data, httpx.Response):
                return data
            event = {
                "id": body["id"],
                "jsonrpc": "2.0",
                "result": {"content": [{"type": "text", "text": json.dumps(data)}]},
            }
            stream = (
                'data:{"method":"notifications/ping"}\r\n\r\nevent:message\r\ndata:'
                + json.dumps(event)
                + "\r\n\r\n"
            )
            return httpx.Response(200, text=stream, headers={"Content-Type": "text/event-stream"})

        return httpx.MockTransport(handler)

    return client_for, calls


def test_grouped_equities_and_benchmarks_use_only_exchange_rows_and_timestamps(mock_nse):
    client_for, calls = mock_nse

    def data(name, params):
        if name == "cm_get_equity_stocks":
            assert params == {"symbolFilter": "SBI", "limit": 500}
            return equity_feed(
                equity("SBIN", openPrice=102, lowPrice=96, highPrice=106, volume=3200),
                equity("SBIN", "BE", lastTradedPrice=93),
                equity("SBICARD", latestTimestamp=None),
                equity("SBIN", "BZ", lastTradedPrice=199),
                equity("SBICARD", type="FO", lastTradedPrice=155),
            )
        assert name == "cm_get_live_indices"
        index = {"last": 101, "previousClose": 100, "timeVal": "26-Nov-2020 09:10"}
        return {
            "indices": {
                "broad": [{"indexName": "NIFTY 200", **index}],
                "derivatives": [{"indexName": "NIFTY 50", **index}],
            }
        }

    quotes, warning = nse_quotes.fetch_quotes(
        ["SBIN", "SBIN-BE", "SBICARD", *nse_quotes.BENCHMARKS], transport=client_for(data)
    )
    assert warning is None
    sbi = quotes["SBIN"]
    assert (sbi["symbol"], sbi["series"], sbi["price"], sbi["previous_close"]) == (
        "SBIN",
        "EQ",
        105,
        100,
    )
    assert (sbi["open"], sbi["high"], sbi["low"], sbi["volume"]) == (102, 106, 96, 3200)
    assert sbi["change_pct"] == pytest.approx(5)
    assert sbi["timestamp"] == "2020-11-25T15:30:01+05:30"
    assert "isin" not in sbi
    assert quotes["SBIN-BE"]["price"] == 93
    assert quotes["SBICARD"]["timestamp"] is None
    assert quotes["NIFTY 200"]["timestamp"] == "2020-11-26T09:10:00+05:30"
    assert quotes["NIFTY 50"]["price"] == 101
    assert "INDIA VIX" not in quotes
    assert [name for name, _ in calls] == [
        "cm_get_equity_stocks",
        "cm_get_live_indices",
        "terminate",
    ]


def test_capped_prefix_refines_only_missing_constituents(mock_nse, monkeypatch):
    monkeypatch.setattr(nse_quotes, "ROW_LIMIT", 2)
    client_for, calls = mock_nse
    feeds = {
        "A": equity_feed(equity("ALPHA"), equity("AXIS")),
        "AMBER": equity_feed(equity("AMBER")),
    }
    got, warning = nse_quotes.fetch_quotes(
        ["ALPHA", "AMBER", "AXIS"],
        transport=client_for(lambda _, params: feeds[params["symbolFilter"]]),
    )
    assert warning is None
    assert got.keys() == {"ALPHA", "AXIS", "AMBER"}
    assert [p["symbolFilter"] for name, p in calls if name == "cm_get_equity_stocks"] == [
        "A",
        "AMBER",
    ]


def test_unsafe_series_prices_and_conflicting_rows_stay_absent(mock_nse):
    client_for, _ = mock_nse
    rows = equity_feed(
        equity("SBIN", "BE"),
        equity(["SBIN"]),
        equity("SBIN", series=[]),
        equity("SBICARD", lastTradedPrice=0),
        equity("SBIGOLD", lastTradedPrice=3),
        equity("SBIGOLD", lastTradedPrice=8),
        equity("SBILIFE", latestTimestamp="2099-12-31 15:30:00", openPrice=0),
    )
    got, warning = nse_quotes.fetch_quotes(
        ["SBIN", "SBICARD", "SBIGOLD", "SBILIFE"], transport=client_for(lambda *_: rows)
    )
    assert warning is None
    assert set(got) == {"SBILIFE"}
    assert got["SBILIFE"]["timestamp"] is None
    assert got["SBILIFE"]["open"] is None


@pytest.mark.parametrize(
    "result",
    [
        {"error": "temporarily unavailable"},
        httpx.Response(429),
        {"segment": "FNO", "stocks": []},
        equity_feed(),
    ],
)
def test_service_errors_surface_and_close_session(mock_nse, result):
    client_for, calls = mock_nse
    with pytest.raises(MarketError):
        nse_quotes.fetch_quotes(["SBIN"], transport=client_for(lambda *_: result))
    assert calls[-1] == ("terminate", {})


@pytest.mark.parametrize("status", [429, 503])
def test_failed_middle_batch_keeps_previous_quotes_and_stops_polling(mock_nse, status):
    client_for, calls = mock_nse

    def data(name, params):
        if params.get("symbolFilter") == "AXIS":
            return equity_feed(equity("AXIS"))
        assert params["symbolFilter"] == "SBIN"
        return httpx.Response(status)

    rows, warning = nse_quotes.fetch_quotes(
        ["AXIS", "SBIN", "TCS", "NIFTY 50"], transport=client_for(data)
    )
    assert rows.keys() == {"AXIS"}
    assert "1/4" in warning and f"HTTP {status}" in warning
    assert [name for name, _ in calls] == [
        "cm_get_equity_stocks",
        "cm_get_equity_stocks",
        "terminate",
    ]


@pytest.mark.parametrize("invalid", [httpx.Response(200, text="bad-json"), {"indices": []}])
def test_malformed_index_batch_keeps_successful_equity_rows(mock_nse, invalid):
    client_for, calls = mock_nse

    def data(name, params):
        return equity_feed(equity("SBIN")) if name == "cm_get_equity_stocks" else invalid

    rows, warning = nse_quotes.fetch_quotes(["SBIN", "NIFTY 200"], transport=client_for(data))
    assert rows.keys() == {"SBIN"}
    assert "Incomplete NSE refresh (1/2 quotes)" in warning
    assert calls[-1] == ("terminate", {})
