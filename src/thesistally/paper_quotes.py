"""Indicative values for journaled holdings, using identified exchange quotes."""

from datetime import datetime, time
from functools import lru_cache
from math import isfinite

import pandas as pd

from . import market


@lru_cache(maxsize=4)
def quote_identities(folder):
    """Match the archived NSE ISIN to the exact Kite symbol and instrument token."""
    tape = pd.read_csv(
        folder / "bhavcopy.csv",
        usecols=["TradDt", "TckrSymb", "SctySrs", "ISIN", "FinInstrmId"],
        dtype=str,
    )
    master = pd.read_csv(
        folder / "instruments.csv",
        usecols=["segment", "tradingsymbol", "instrument_token", "exchange_token"],
        dtype=str,
    )
    tape = tape.loc[
        (tape.TradDt == folder.name) & tape.SctySrs.str.strip().isin(["EQ", "BE", "BZ"])
    ]
    rows = tape.merge(
        master.loc[master.segment == "NSE"], left_on="FinInstrmId", right_on="exchange_token"
    )
    symbol = rows.TckrSymb
    valid = rows.tradingsymbol.eq(symbol) | rows.tradingsymbol.eq(
        symbol + "-" + rows.SctySrs.str.strip()
    )
    rows = rows.loc[valid]
    # An ambiguous identity must never value a position.
    rows = rows.loc[~rows.tradingsymbol.duplicated(keep=False) & ~rows.ISIN.duplicated(keep=False)]
    return {r.tradingsymbol: (r.ISIN, int(r.instrument_token)) for r in rows.itertuples()}


def _newer_mark(quote, close, day, now):
    """Check that the last trade is newer and quoted on the journal's share basis."""
    if not quote or not day or close is None:
        return None
    try:
        at = datetime.fromisoformat(quote["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None
    price, prior = quote.get("price"), quote.get("previous_close")
    if (
        at.tzinfo is None
        or at > now
        or at.astimezone(market.IST).date().isoformat() <= day
        or not isinstance(price, (int, float))
        or not isfinite(price)
        or price <= 0
        or not isinstance(prior, (int, float))
        or not isfinite(prior)
        or abs(prior - close) > market.TOLERANCE
    ):
        return None
    return price, at.astimezone(market.IST)


def quote_freshness(provider, at, now, *, cached=False, interval=120):
    """Classify the exchange timestamp independently of when the HTTP request completed."""
    # Allow one polling interval on top of NSE's published three-minute delay.
    max_age = 180 + interval if provider == "nse" else 120
    if (
        cached
        or not time(9, 15) <= now.astimezone(market.IST).time() < time(15, 30)
        or not 0 <= (now - at).total_seconds() <= max_age
    ):
        return "Last quote"
    return "Delayed" if provider == "nse" else "Live"


def paper_live_view(account, quote_data, *, now=None):
    """Overlay price changes on settled marks without changing journal balances or fills."""
    now = (now or datetime.now(market.IST)).astimezone(market.IST)
    provider = quote_data.get("provider", "kite")
    last = next(iter(account["history"]), {})
    day = last.get("day")
    quotes = {q["isin"]: q for q in quote_data.get("quotes", {}).values() if q.get("isin")}
    equity, total_pnl = last.get("equity"), last.get("total_pnl")
    change = 0.0
    live_count = delayed_count = held_count = 0
    stamps = []
    positions = {}
    for trade in account["trades"]:
        price, pnl = trade.get("eod_close"), trade.get("marked_pnl")
        qty = trade["remaining"]
        source, stamp = "—", None
        if trade["net"] is not None:
            pnl, source = trade["net"], "Settled"
        elif trade["status"] == "active" and qty > 0:
            held_count += 1
            if price is not None or pnl is not None:
                source, stamp = ("Frozen" if price is None else "EOD"), day
            quote = quotes.get(trade["isin"])
            mark = _newer_mark(quote, price, day, now)
            if mark and pnl is not None:
                ltp, at = mark
                # Adjust only shares still held. Booked proceeds, fees, dividends and
                # frozen entitlements are already included in both original marks.
                delta = qty * (ltp - price)
                price, pnl, stamp = ltp, pnl + delta, at.isoformat()
                change += delta
                source = quote_freshness(
                    provider,
                    at,
                    now,
                    cached=bool(quote_data.get("error") or quote.get("cached")),
                    interval=quote_data.get("interval", 120),
                )
                live_count += int(source == "Live")
                delayed_count += int(source == "Delayed")
                stamps.append(stamp)
        positions[trade["id"]] = {"price": price, "pnl": pnl, "source": source, "as_of": stamp}
    return {
        "positions": positions,
        "equity": None if equity is None else equity + change,
        "total_pnl": None if total_pnl is None else total_pnl + change,
        "as_of": min(stamps, default=None),
        "held_count": held_count,
        "live_count": live_count,
        "delayed_count": delayed_count,
        "quoted_count": len(stamps),
    }
