"""Read the stock reports published by research.run_report without opening a journal."""

import json
import re
from datetime import date
from pathlib import Path

from .market import MarketError


def latest_stock_report(root: Path, symbol: str, as_of: date | None = None) -> dict | None:
    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9&._-]*", symbol):
        raise MarketError("Enter an NSE stock symbol such as SBIN or M&M.")

    # Filenames record generation time in IST followed by a random suffix.
    # When runs finish within one second, prefer the last file written.
    reports = (root / "research" / symbol).glob("*.json")

    def generation_order(path):
        return path.stem.rpartition("-")[0], path.stat().st_mtime_ns, path.name

    for path in sorted(reports, key=generation_order, reverse=True):
        try:
            item = json.loads(path.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MarketError(f"Saved report {path.name} for {symbol} is unreadable.") from exc
        if (
            not isinstance(item, dict)
            or item.get("symbol") != symbol
            or not isinstance(item.get("report"), dict)
            or not item.get("as_of")
        ):
            raise MarketError(f"Saved report {path.name} is missing report data for {symbol}.")
        if as_of is None or item["as_of"] == as_of.isoformat():
            return item
    return None
