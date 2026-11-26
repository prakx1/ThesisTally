from datetime import datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner

from thesistally import market, nse_quotes
from thesistally.cli import app
from thesistally.dashboard import Dashboard


def member(symbol, isin, series="EQ"):
    return {"symbol": symbol, "isin": isin, "series": series, "sector": "Banks"}


def test_default_quote_source_bootstraps_without_kite_and_checks_holding_identity(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("RADAR_QUOTE_PROVIDER", raising=False)
    dash = Dashboard(tmp_path)
    assert (dash.quote_provider, dash.interval) == ("nse", 120)
    dash._held_isins = {"INSBIN000001", "INOTHER00001"}
    rosters = []

    def roster(*, all_equities=False):
        rosters.append(all_equities)
        if all_equities:
            return [member("OTHER", "INOTHER00001", "BE")]
        return [member("SBIN", "INSBIN000001")]

    monkeypatch.setattr(dash, "_current_roster", roster)
    monkeypatch.setattr(
        market, "credentials", lambda: pytest.fail("NSE prices requested credentials")
    )
    stamp = datetime.now(market.IST).isoformat()

    def fetch(symbols):
        assert symbols == [*market.BENCHMARKS, "SBIN", "OTHER-BE"]
        return {
            "SBIN": {"symbol": "SBIN", "series": "EQ", "price": 100, "timestamp": stamp},
            "OTHER-BE": {"symbol": "OTHER", "series": "EQ", "price": 52, "timestamp": stamp},
        }, None

    monkeypatch.setattr(nse_quotes, "fetch_quotes", fetch)
    state = dash.quotes()
    assert state["provider"] == "nse" and state["requested"] == 5 and not state["error"]
    assert [r["symbol"] for r in state["members"]] == ["SBIN"]
    assert state["quotes"]["SBIN"]["isin"] == "INSBIN000001"
    assert "OTHER-BE" not in state["quotes"]
    assert rosters == [False, True]


def test_refresh_tracks_missing_quotes_and_outages(tmp_path, monkeypatch):
    dash = Dashboard(tmp_path, "nse")
    monkeypatch.setattr(dash, "_current_roster", lambda: [member("A", "A"), member("B", "B")])

    def row(symbol, price):
        return {
            "symbol": symbol,
            "series": "EQ",
            "price": price,
            "timestamp": datetime.now(market.IST).isoformat(),
        }

    responses = iter(
        [
            ({"A": row("A", 8), "B": row("B", 9)}, None),
            ({"B": row("B", 10)}, "NSE partial snapshot"),
            ({}, None),
        ]
    )
    monkeypatch.setattr(nse_quotes, "fetch_quotes", lambda _: next(responses))
    dash.quotes()
    partial = dash.quotes()
    assert partial["warning"] == "NSE partial snapshot"
    assert partial["quotes"]["A"]["cached"] and partial["quotes"]["B"]["price"] == 10
    failed = dash.quotes()
    assert failed["error"] and failed["fetched_at"] == partial["fetched_at"]
    assert failed["quotes"] == partial["quotes"]


def test_unavailable_universe_keeps_indices_and_explains_missing_constituents(
    tmp_path, monkeypatch
):
    dash = Dashboard(tmp_path, "nse")

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(dash, "_current_roster", unavailable)

    def fetch(symbols):
        assert symbols == list(market.BENCHMARKS)
        return {"NIFTY 200": {"price": 100}}, None

    monkeypatch.setattr(nse_quotes, "fetch_quotes", fetch)
    state = dash.quotes()
    assert state["quotes"]["NIFTY 200"]["price"] == 100
    assert "constituents unavailable" in state["warning"]


def test_security_master_matches_series_and_excludes_ambiguous_listings(tmp_path, monkeypatch):
    dash = Dashboard(tmp_path, "nse")
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        text = (
            "SYMBOL, SERIES, ISIN NUMBER\n"
            "NEW,BE,INE000000001\nAMB,EQ,INE000000002\nAMB,EQ,INE000000003\n"
        )
        return httpx.Response(200, request=httpx.Request("GET", url), text=text)

    monkeypatch.setattr(httpx, "get", get)
    expected = [{"symbol": "NEW", "series": "BE", "isin": "INE000000001"}]
    assert dash._current_roster(all_equities=True) == expected
    assert dash._current_roster(all_equities=True) == expected
    assert len(calls) == 1


def test_source_override_and_invalid_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_QUOTE_PROVIDER", "kite")
    assert Dashboard(tmp_path).interval == 5
    assert Dashboard(tmp_path, "nse").quote_provider == "nse"
    with pytest.raises(ValueError, match="nse or kite"):
        Dashboard(tmp_path, "typo")
    bad = CliRunner().invoke(app, ["--quote-provider", "typo"])
    assert bad.exit_code == 2 and "nse or kite" in bad.output


@pytest.mark.parametrize("with_archive", [False, True])
def test_cache_drops_departures_and_reused_tickers_even_when_refresh_fails(
    tmp_path, monkeypatch, with_archive
):
    dash = Dashboard(tmp_path, "nse")
    old_members = [member("A", "OLD"), member("B", "DEPARTED")]
    new_members = [member("A", "NEW")]
    rosters = iter([old_members, new_members, new_members])
    monkeypatch.setattr(dash, "_current_roster", lambda: next(rosters))
    today = datetime.now(market.IST)
    yesterday = (today - timedelta(days=1)).date().isoformat()
    monkeypatch.setattr(
        dash,
        "archive",
        lambda: {
            "session": yesterday if with_archive else None,
            "members": old_members if with_archive else [],
        },
    )

    def tick(s, at):
        return {"symbol": s, "series": "EQ", "price": 10, "timestamp": at.isoformat()}

    responses = iter(
        [
            ({"A": tick("A", today), "B": tick("B", today)}, None),
            ({}, None),
            ({"A": tick("A", today - timedelta(days=1)), "NIFTY 200": {"price": 50}}, None),
        ]
    )
    monkeypatch.setattr(nse_quotes, "fetch_quotes", lambda _: next(responses))
    assert set(dash.quotes()["quotes"]) == {"A", "B"}
    assert dash.quotes()["quotes"] == {}
    assert set(dash.quotes()["quotes"]) == {"NIFTY 200"}


def test_unverified_previous_session_tick_cannot_mark_current_holding(tmp_path, monkeypatch):
    dash = Dashboard(tmp_path, "nse")
    monkeypatch.setattr(dash, "_current_roster", lambda: [member("A", "NEW")])
    quote = {
        "symbol": "A",
        "series": "EQ",
        "price": 8,
        "timestamp": (datetime.now(market.IST) - timedelta(days=1)).isoformat(),
    }
    monkeypatch.setattr(nse_quotes, "fetch_quotes", lambda _: ({"A": quote}, None))
    assert "isin" not in dash.quotes()["quotes"]["A"]
