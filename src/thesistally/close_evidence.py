"""Certify held closing-price releases using independent official market records."""

import argparse
import csv
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd

from . import convention_market, market

REVIEW = Path("stage3/close-verification")
VERSION = "independent-closes-v1"
ASSESSMENTS = Path("stage3/entry-price-gap-checks.json")
KITE_ARCHIVE = Path("sources/kite-history/2012-11-01_2026-11-20")
QUOTE_FIELDS = ("OpnPric", "HghPric", "LwPric", "ClsPric")
# Fixed terms were read from the original circulars, independently of either price feed.
UNIT_CHANGES = {
    "PCJEWELLER": ("2024-12-16", 10, ("CMPT65546.pdf",)),
    "BAJFINANCE": ("2025-06-16", 10, ("FAOP68547.pdf", "CML68472.pdf")),
}
CIRCULAR_SHA = {
    "CMPT65546.pdf": "b21da2c3469740b54bd03b9a242eef620dca0393f76702eae44cf4d4976c7086",
    "FAOP68547.pdf": "decb99ba1de23d800b34ffef3293ce8b951e994f14c6b88c70d80f75e0af1c34",
    "CML68472.pdf": "0513f355122e2af95a87ed9ccad859d7aca03625c59eaa5cf7795d8da7b7cb56",
}
TOKENS = {
    "BAJFINANCE": 81153,
    "PCJEWELLER": 7455745,
    "IDEA": 3677697,
    "POLYCAB": 2455041,
    "MANAPPURAM": 4879617,
    "ICICIPRULI": 4774913,
}


def source(root, path, url):
    return {
        "path": str(path.relative_to(root)),
        "url": url,
        "sha256": market.sha(path.read_bytes()),
    }


def exact_pair(original, reported, scale=1):
    """Require independently established units and exact decimal equality on both days."""
    return len(original) == len(reported) == 2 and all(
        Decimal(str(raw)) == Decimal(str(other)) * scale
        for raw, other in zip(original, reported, strict=True)
    )


def units(root, symbol, days):
    change = UNIT_CHANGES.get(symbol)
    if not change or min(days) >= change[0]:
        return {"adjusted_units_per_original": 1, "sources": []}
    effective, ratio, names = change
    if max(days) >= effective:
        raise market.MarketError("Price comparison crosses a share unit change")
    documents = []
    for name in names:
        doc = source(
            root,
            root / REVIEW / "sources" / name,
            "https://archives.nseindia.com/content/circulars/" + name,
        )
        if doc["sha256"] != CIRCULAR_SHA[name]:
            raise market.MarketError("Published share adjustment circular changed")
        documents.append(doc)
    return {"effective": effective, "adjusted_units_per_original": ratio, "sources": documents}


def groww_url(symbol, days):
    def ms(d):
        return int(datetime.combine(d, time.min, market.IST).timestamp()) * 1000

    return (
        "https://groww.in/v1/api/charting_service/v2/chart/exchange/NSE/segment/CASH/"
        + symbol
        + "?"
        + urlencode(
            {
                "startTimeInMillis": ms(date.fromisoformat(days[0]) - timedelta(days=1)),
                "endTimeInMillis": ms(date.fromisoformat(days[1]) + timedelta(days=1)),
                "intervalInMinutes": 1440,
            }
        )
    )


def broker_pair(root, case, days, original, ratio):
    symbol = case["symbol"]
    path = root / REVIEW / "sources" / f"groww-{symbol}-{days[1]}.json"
    records = json.loads(path.read_bytes(), parse_float=Decimal)["candles"]
    quotes = {}
    for timestamp, op, hi, lo, close, volume in records:
        stamp = datetime.fromtimestamp(timestamp, market.IST)
        d = str(stamp.date())
        if d not in days:
            continue
        if d in quotes or volume <= 0 or not 0 < lo <= min(op, close) <= max(op, close) <= hi:
            raise market.MarketError(f"Invalid official Groww NSE daily quote {symbol} {d}")
        quotes[d] = [op, hi, lo, close]
    values = [quotes[d] for d in days if d in quotes]
    groww = {
        **source(root, path, groww_url(symbol, days)),
        "quoted_ohlc": [[float(x) for x in r] for r in values],
        "both_closes_exact_in_original_units": exact_pair(
            [r[-1] for r in original], [r[-1] for r in values], ratio
        ),
        "both_ohlc_exact_in_original_units": len(values) == 2
        and all(
            Decimal(str(a)) == Decimal(str(b)) * ratio
            for aa, bb in zip(original, values, strict=True)
            for a, b in zip(aa, bb, strict=True)
        ),
    }
    if len(values) < 2:
        groww["missing_dates"] = [d for d in days if d not in quotes]
    token = TOKENS[symbol]
    path = root / KITE_ARCHIVE / (case["isin"] + ".parquet")
    frame = pd.read_parquet(path)
    dates = frame.day.astype(str)
    selected = (
        frame.loc[dates.isin(days)]
        .assign(day=dates[dates.isin(days)])
        .set_index("day")
        .sort_index()
    )
    if list(selected.index) != days or set(selected.instrument_token) != {token}:
        raise market.MarketError(f"Wrong cached Kite identity or dates for {symbol}")
    quotes = selected[market.PRICES].values.tolist()
    kite = {
        **source(root, path, f"https://api.kite.trade/instruments/historical/{token}/day"),
        "instrument_token": token,
        "quoted_ohlc": quotes,
        "both_closes_exact_in_original_units": exact_pair(
            [r[-1] for r in original], [r[-1] for r in quotes], ratio
        ),
        "close_differences_in_original_rupees": [
            float(Decimal(str(b[-1])) * ratio - Decimal(str(a[-1])))
            for a, b in zip(original, quotes, strict=True)
        ],
    }
    return groww, kite


def corporate_records(root, case):
    symbol, isin, day = case["symbol"], case["isin"], date.fromisoformat(case["day"])
    # Annual payload and source receipt are kept together in the certified archive.
    path = next(
        p
        for p in (root / "sources/reference").glob(f"actions-{day.year}-01-01_*.json")
        if not p.name.endswith(".source.json")
    )
    raw = path.read_bytes()
    meta = json.loads(path.with_suffix(".json.source.json").read_bytes())
    if meta["sha256"] != market.sha(raw) or not meta["url"].startswith(
        "https://www.nseindia.in/api/corporates-corporateActions?"
    ):
        raise market.MarketError("NSE corporate action source failed integrity check")
    matches = [
        {"symbol": r["symbol"], "isin": r.get("isin"), "purpose": r["subject"]}
        for r in json.loads(raw)
        if r.get("exDate", "").casefold() == day.strftime("%d-%b-%Y").casefold()
        and (r.get("symbol", "").strip().upper() == symbol or r.get("isin") == isin)
    ]
    annual = {**source(root, path, meta["url"]), "matching_ex_date_events": matches}
    path = root / REVIEW / "sources" / f"announcements-{symbol}-{day}.json"
    params = {
        "index": "equities",
        "symbol": symbol,
        "from_date": (day - timedelta(days=3)).strftime("%d-%m-%Y"),
        "to_date": (day + timedelta(days=3)).strftime("%d-%m-%Y"),
    }
    rows = json.loads(path.read_bytes())
    announcements = []
    for row in rows:
        stamp = datetime.strptime(row["an_dt"], "%d-%b-%Y %H:%M:%S")
        if row["symbol"] != symbol or abs((stamp.date() - day).days) > 3:
            raise market.MarketError("Incorrect company/date in NSE announcement query")
        announcements.append(
            {
                "filed": row["an_dt"],
                "exchange_id": row["seq_id"],
                "subject": row["desc"],
                "attachment": row.get("attchmntFile"),
            }
        )
    filings = {
        **source(
            root, path, "https://www.nseindia.in/api/corporate-announcements?" + urlencode(params)
        ),
        "filings": announcements,
    }
    return annual, filings


def clearing_comparison(root, case, days, original):
    # The clearing price can differ from the exchange-specific cash-market final.
    # Preserve the difference rather than using a tolerance or a rounded value.
    day = date.fromisoformat(days[-1])
    name = f"FOVOLT_{day:%d%m%Y}.csv" if days[-1] == "2020-03-23" else f"CMVOLT_{day:%d%m%Y}.CSV"
    path = root / REVIEW / "sources" / name
    rows = [
        {k.strip(): v.strip() for k, v in r.items()}
        for r in csv.DictReader(path.read_text().splitlines())
    ]
    (row,) = [r for r in rows if r["Symbol"] == case["symbol"]]
    if row["Date"].upper() not in (
        day.strftime("%d-%b-%y").upper(),
        day.strftime("%d-%b-%Y").upper(),
    ):
        raise market.MarketError("Dated clearing prices belong to a different session")
    prices = [row["Underlying Previous Day Close Price (B)"], row["Underlying Close Price (A)"]]
    return {
        **source(root, path, "https://archives.nseindia.com/archives/nsccl/volt/" + name),
        "reported_closes": list(map(float, prices)),
        "both_closes_exact_in_original_units": exact_pair([r[-1] for r in original], prices),
    }


def verify_case(root, case):
    days = [case["prior_day"], case["day"]]
    originals, sources = [], []
    for d in days:
        row, meta = convention_market.final_bar(root, date.fromisoformat(d), case["isin"])
        if (
            row is None
            or str(row.TckrSymb) != case["symbol"]
            or row.SctySrs.strip() != "EQ"
            or int(row.TtlTradgVol) <= 0
        ):
            raise market.MarketError(f"Missing qualifying exchange final on {d}")
        originals.append([float(row[k]) for k in QUOTE_FIELDS])
        sources.append(meta)
    if (
        originals[0][-1] != case["prior_close"]
        or originals[1][-1] != case["close"]
        or float(row.PrvsClsgPric) != originals[0][-1]
    ):
        raise market.MarketError("Held price gap differs from NSE final")
    share_units = units(root, case["symbol"], days)
    groww, kite = broker_pair(
        root, case, days, originals, share_units["adjusted_units_per_original"]
    )
    ca, filings = corporate_records(root, case)
    clearing = clearing_comparison(root, case, days, originals)
    comparisons = {
        "Groww NSE cash chart": groww,
        "Zerodha Kite day archive": kite,
        "NSE Clearing volatility report": clearing,
    }
    if case["symbol"] == "ICICIPRULI":
        path = root / REVIEW / "sources" / f"upstox-{case['symbol']}-{days[1]}.json"
        url = f"https://api.upstox.com/v3/historical-candle/NSE_EQ%7C{case['isin']}/days/1/{days[1]}/{days[0]}"
        rows = json.loads(path.read_bytes())["data"]["candles"]
        by_date = {
            datetime.fromisoformat(r[0]).astimezone(market.IST).date().isoformat(): r[1:5]
            for r in rows
        }
        if len(by_date) != len(rows) or any(r[5] <= 0 for r in rows):
            raise market.MarketError("Invalid daily Upstox prices")
        prices = [by_date[d] for d in days]
        comparisons["Upstox NSE cash history"] = {
            **source(root, path, url),
            "quoted_ohlc": prices,
            "both_closes_exact_in_original_units": exact_pair(
                [r[-1] for r in originals],
                [r[-1] for r in prices],
                share_units["adjusted_units_per_original"],
            ),
        }
    matching = ["NSE equity final"] + [
        label for label, item in comparisons.items() if item["both_closes_exact_in_original_units"]
    ]
    return {
        **{k: case[k] for k in ("symbol", "isin", "day", "prior_day", "prior_close", "close")},
        "verified": len(matching) >= 2 and not ca["matching_ex_date_events"],
        "matching_price_sources": matching,
        "official_nse_finals": sources,
        "nse_ohlc": originals,
        "historical_share_units": share_units,
        "independent_checks": comparisons,
        "corporate_action_register": ca,
        "corporate_announcements": filings,
    }


def assemble(root):
    baseline = json.loads((root / ASSESSMENTS).read_bytes())["cases"]
    return {
        "version": VERSION,
        "original_scope_sha256": market.sha((root / ASSESSMENTS).read_bytes()),
        "independence": "Each source independently publishes an observation of the same exchange or a clearing risk price. None supplies another execution venue. Exact comparison uses published share-unit factors only. Later corporate-action records are evidence of the broker chart units, never trading inputs.",
        "kite_provenance": "Authenticated historical NSE day candles archived by security and instrument in the original historical backfill. A fresh read on 21 November 2026 was rejected by Kite (session or entitlement). These are the stored observations.",
        "cases": [verify_case(root, c) for c in baseline],
    }


def with_held_cases(root, previous, scope_name):
    # Scope receipts pin each newly encountered holding and its last verifiable mark.
    path = root / REVIEW / scope_name
    scope = json.loads(path.read_bytes())
    parent = root / REVIEW / scope["previous_evidence"]
    if (
        market.sha(parent.read_bytes()) != scope["previous_sha256"]
        or json.loads(parent.read_bytes()) != previous
    ):
        raise market.MarketError("Prior signed close decisions changed")
    existing = {(c["day"], c["isin"]) for c in previous["cases"]}
    extra = []
    for c in scope["cases"]:
        if (c["day"], c["isin"]) in existing:
            raise market.MarketError("Duplicate held close in supplementary scope")
        state = c["last_account_state_json"]
        if market.sha(state.encode()) != c["account_state_sha256"]:
            raise market.MarketError("Changed historical holder state")
        pos = json.loads(state)["positions"].get(c["isin"], {})
        if (
            pos.get("qty", 0) <= 0
            or pos.get("symbol") != c["symbol"]
            or pos.get("mark") != c["prior_close"]
        ):
            raise market.MarketError("Additional close does not correspond to a held stock")
        extra.append(
            {
                **verify_case(root, c),
                "discovery": {
                    "run": c["run"],
                    "journal": c["journal"],
                    "shares": pos["qty"],
                    "last_mark": c["prior_day"],
                    "account_state_sha256": c["account_state_sha256"],
                    **{k: c[k] for k in ("witness_method", "witness_original_day") if k in c},
                },
            }
        )
        existing.add((c["day"], c["isin"]))
    return {
        **previous,
        "supplemental_scopes": previous.get("supplemental_scopes", [])
        + [
            {
                "name": scope_name,
                "sha256": market.sha(path.read_bytes()),
                "parent_sha256": scope["previous_sha256"],
            }
        ],
        "cases": previous["cases"] + extra,
    }


def expected(root, path):
    base = assemble(root)
    if path.name in {"evidence-followup.json", "evidence-held-pair.json"}:
        base = with_held_cases(root, base, "followup-held-1.json")
    if path.name == "evidence-held-pair.json":
        base = with_held_cases(root, base, "followup-held-2.json")
    return base


def certify(data_root, evidence_path=None):
    root = Path(data_root)
    path = Path(evidence_path) if evidence_path else root / REVIEW / "evidence.json"
    stored = json.loads(path.read_bytes())
    if stored != expected(root, path):
        raise market.MarketError(
            "Historical close release evidence differs from independently recomputed sources"
        )
    return stored


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--evidence", type=Path, help="Use a separately signed follow-up receipt.")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Build a new receipt without overwriting an earlier decision",
    )
    args = parser.parse_args()
    path = args.evidence or args.root / REVIEW / "evidence.json"
    if args.write and not path.exists():
        path.write_text(
            json.dumps(expected(args.root, path), indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        )
    print([(r["symbol"], r["verified"]) for r in certify(args.root, path)["cases"]])
