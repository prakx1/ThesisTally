"""Match the current official Nifty 200 roster to active BSE equity codes by ISIN."""

import csv
import io
import json
import re
from collections import defaultdict

from . import results_forward_bse as bse
from .fundamental_nse import public_copy
from .results_forward_nse import Page

NIFTY200 = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
BSE_MASTER = (
    "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?"
    "Group=&Scripcode=&industry=&segment=Equity&status=Active"
)
ISIN = re.compile(r"IN[A-Z0-9]{9}[0-9]")


def _members(raw):
    table = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if not {"ISIN Code", "Symbol"}.issubset(table.fieldnames or ()):
        raise ValueError("NSE response lacks constituent columns")
    rows = []
    seen, symbols = set(), set()
    for line in table:
        isin = (line["ISIN Code"] or "").strip().upper()
        symbol = (line["Symbol"] or "").strip()
        if not symbol or not ISIN.fullmatch(isin) or isin in seen or symbol in symbols:
            raise ValueError("Invalid or duplicate NSE constituent")
        rows.append((isin, symbol))
        seen.add(isin)
        symbols.add(symbol)
    # Corporate-action placeholders can temporarily bring the roster above 200.
    if len(rows) < 200:
        raise ValueError("Incomplete public Nifty 200 constituent list")
    return rows


def _valid_members(raw):
    try:
        _members(raw)
    except (ValueError, csv.Error):
        return False
    return True


def fetch(nse_http, bse_http):
    """Return both dated-by-observation source pages and every NSE membership row."""
    raw, retrieved = public_copy(nse_http, NIFTY200, accept=_valid_members)
    members = _members(raw)
    master = bse.page(bse_http, BSE_MASTER)
    listed = json.loads(master.raw)
    if not isinstance(listed, list) or len(listed) < len(members):
        raise ValueError("Incomplete BSE active equity list")
    codes = defaultdict(set)
    identities = defaultdict(set)
    for row in listed:
        required = {"ISIN_NUMBER", "SCRIP_CD", "Status", "Segment"}
        if not isinstance(row, dict) or not required.issubset(row):
            raise ValueError("Malformed BSE active equity list")
        if row["Status"] != "Active" or row["Segment"] != "Equity":
            continue
        isin = str(row["ISIN_NUMBER"] or "").strip().upper()
        code = str(row["SCRIP_CD"] or "").strip()
        if not re.fullmatch("[0-9]{6}", code):
            raise ValueError("Invalid BSE equity scrip code")
        identities[code].add(isin)
        if ISIN.fullmatch(isin):
            codes[isin].add(code)
    if len(codes) < len(members):
        raise ValueError("Incomplete BSE equity identity list")
    matches = []
    for isin, symbol in members:
        choices = codes.get(isin, set())
        code = next(iter(choices)) if len(choices) == 1 else None
        matches.append(
            {
                "isin": isin,
                "symbol": symbol,
                "scrip": code if identities.get(code) == {isin} else None,
            }
        )
    return [Page(NIFTY200, retrieved, raw), master], matches
