"""Read-only delayed quotes from NSE's official CM Market MCP server."""

import json
from collections import defaultdict, deque
from contextlib import closing, suppress
from datetime import datetime
from math import isfinite
from os.path import commonprefix
from time import monotonic, sleep
from typing import NamedTuple

import httpx

from .market import BENCHMARKS, IST, MarketError

ENDPOINT = "https://mcp.nseindia.in/cmmkt/mcp"
PROTOCOL = "2025-03-26"
ROW_LIMIT = 500
REQUEST_INTERVAL = 0.4
# NSE's CM list is capped with a symbol prefix filter and has no pagination.
MAX_PREFIX_CALLS = 40


class _Session:
    def __init__(self, client):
        self.client = client
        self.counter = 0
        self.next_request = 0.0
        self.deadline = monotonic() + 45

    def request(self, method, params, *, notification=False):
        self.counter += 1
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            message["id"] = self.counter
        for attempt in range(2):
            if monotonic() >= self.deadline:
                raise MarketError("NSE quote snapshot timed out.")
            sleep(max(0.0, self.next_request - monotonic()))
            self.next_request = monotonic() + REQUEST_INTERVAL
            try:
                response = self.client.post(ENDPOINT, json=message)
                response.raise_for_status()
                break
            except httpx.ConnectError:
                if attempt:
                    raise MarketError("Could not connect to the NSE quote service.") from None
            except httpx.HTTPStatusError as exc:
                raise MarketError(
                    f"NSE quote service returned HTTP {exc.response.status_code}."
                ) from None
            except httpx.RequestError:
                raise MarketError("NSE quote service unavailable.") from None
        if notification:
            return None
        if method == "initialize" and response.headers.get("mcp-session-id"):
            self.client.headers["MCP-Session-Id"] = response.headers["mcp-session-id"]
        if "text/event-stream" in response.headers.get("content-type", ""):
            messages = []
            for event in response.text.replace("\r\n", "\n").split("\n\n"):
                data = [
                    line[5:].lstrip() for line in event.splitlines() if line.startswith("data:")
                ]
                if data:
                    messages.append(json.loads("\n".join(data)))
        else:
            messages = [response.json()]
        for item in messages:
            if isinstance(item, dict) and item.get("id") == self.counter:
                if isinstance(item.get("result"), dict) and "error" not in item:
                    return item["result"]
                break
        raise MarketError("Invalid response from the NSE quote service.")

    def close(self):
        if "MCP-Session-Id" in self.client.headers:
            # Session termination is optional on streamable HTTP servers.
            with suppress(httpx.RequestError):
                self.client.delete(ENDPOINT, timeout=2.0)

    def tool(self, name, arguments):
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if not result.get("isError") and isinstance(result.get("content"), list):
            for item in result.get("content", []):
                if (
                    isinstance(item, dict)
                    and item.get("type") == "text"
                    and isinstance(item.get("text"), str)
                ):
                    data = json.loads(item["text"])
                    if isinstance(data, dict) and "error" not in data:
                        return data
                    break
        raise MarketError(f"NSE could not provide data from {name}.")


def _number(value):
    if type(value) in (int, float) and isfinite(value):
        return value
    return None


def _positive(value):
    value = _number(value)
    return value if value is not None and value > 0 else None


def _timestamp(value, pattern):
    if isinstance(value, str):
        try:
            timestamp = datetime.strptime(value, pattern).replace(tzinfo=IST)
            if timestamp <= datetime.now(IST):
                return timestamp.isoformat()
        except ValueError:
            pass
    return None


def _quote(row, *, index=False):
    price = _positive(row.get("last" if index else "lastTradedPrice"))
    if price is None:
        return None
    previous = _positive(row.get("previousClose" if index else "preClosePrice"))
    return {
        "price": price,
        "previous_close": previous,
        # Published percentage can lag LTP at the opening auction.
        "change_pct": 100 * (price / previous - 1)
        if previous
        else _number(row.get("percChange" if index else "perChange")),
        "open": _positive(row.get("open" if index else "openPrice")),
        "high": _positive(row.get("high" if index else "highPrice")),
        "low": _positive(row.get("low" if index else "lowPrice")),
        "volume": _number(row.get("volume")),
        # Wrapper updatedAt is a cache refresh, never an exchange quote time.
        "timestamp": (
            _timestamp(row.get("timeVal"), "%d-%b-%Y %H:%M")
            if index
            else _timestamp(row.get("latestTimestamp"), "%Y-%m-%d %H:%M:%S")
        ),
    }


def _group(symbols, length):
    groups = defaultdict(list)
    for symbol in sorted(symbols):
        groups[symbol[:length]].append(symbol)
    return [(commonprefix(names), names) for names in groups.values()]


class QuoteSnapshot(NamedTuple):
    quotes: dict[str, dict]
    warning: str | None = None


def fetch_quotes(symbols: list[str], *, transport=None) -> QuoteSnapshot:
    """Return completed batches and an explanation if a later request failed."""
    requested = set(symbols)
    quotes = {}
    if not requested:
        return QuoteSnapshot(quotes)
    try:
        _collect_quotes(requested, quotes, transport)
    except (MarketError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        if not quotes:
            raise
        return QuoteSnapshot(
            quotes, f"Incomplete NSE refresh ({len(quotes)}/{len(requested)} quotes). {exc}"
        )
    if not quotes:
        raise MarketError("NSE returned no usable quotes for the requested symbols.")
    return QuoteSnapshot(quotes)


def _collect_quotes(requested, quotes, transport):
    benchmarks = requested.intersection(BENCHMARKS)
    stocks = defaultdict(dict)
    for key in requested.difference(BENCHMARKS):
        if not key:
            continue
        base, sep, suffix = key.rpartition("-")
        symbol, series = (base, suffix) if sep and suffix in {"BE", "BZ"} else (key, "EQ")
        stocks[symbol][series] = key

    ambiguous = set()

    def add(key, value):
        if value is None or key in ambiguous:
            return
        if key in quotes and quotes[key] != value:
            del quotes[key]
            ambiguous.add(key)
        else:
            quotes[key] = value

    with (
        httpx.Client(
            headers={
                "Accept": "application/json, text/event-stream",
                "User-Agent": "ThesisTally/0.1",
            },
            timeout=httpx.Timeout(8.0, connect=4.0),
            transport=transport,
        ) as client,
        closing(_Session(client)) as session,
    ):
        init = session.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "ThesisTally", "version": "0.1"},
            },
        )
        if init.get("protocolVersion") != PROTOCOL:
            raise MarketError("Unsupported NSE quote service protocol version.")
        client.headers["MCP-Protocol-Version"] = PROTOCOL
        session.request("notifications/initialized", {}, notification=True)

        groups = deque(_group(stocks, 1))
        for _ in range(MAX_PREFIX_CALLS):
            if not groups:
                break
            prefix, names = groups.popleft()
            data = session.tool(
                "cm_get_equity_stocks", {"limit": ROW_LIMIT, "symbolFilter": prefix}
            )
            rows = data.get("stocks")
            if not isinstance(rows, list) or data.get("segment") != "EQUITY":
                raise MarketError("Invalid NSE equity quote snapshot.")
            for row in rows:
                if not isinstance(row, dict) or row.get("type") != "CM":
                    continue
                symbol, series = row.get("symbol"), row.get("series")
                if not isinstance(symbol, str) or not isinstance(series, str):
                    continue
                key = stocks.get(symbol, {}).get(series)
                if key is None or not symbol.startswith(prefix):
                    continue
                value = _quote(row)
                if value:
                    add(key, {**value, "symbol": symbol, "series": series})
            if len(rows) >= ROW_LIMIT:
                missing = [
                    s
                    for s in names
                    if len(s) > len(prefix)
                    and any(
                        key not in quotes and key not in ambiguous for key in stocks[s].values()
                    )
                ]
                groups.extend(_group(missing, len(prefix) + 1))

        if benchmarks:
            indices = session.tool("cm_get_live_indices", {"group": "all", "nameFilter": ""}).get(
                "indices"
            )
            if not isinstance(indices, dict):
                raise MarketError("Invalid NSE index quote snapshot.")
            for rows in indices.values():
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if (
                        isinstance(row, dict)
                        and isinstance(row.get("indexName"), str)
                        and row["indexName"] in benchmarks
                    ):
                        add(row["indexName"], _quote(row, index=True))
