"""Locate historical BSE catalogues. These routes never certify a historical share class."""

import csv
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from . import results_forward_bse as bse
from .discovery_registry import digest, encoded, write_once
from .fundamental_nse import write_json
from .results_forward_roster import BSE_MASTER, ISIN

ROOT = Path("data/fundamentals/d2-bse")
QUARTERS = Path("data/fundamentals/d2-xbrl/stock-quarters.csv")
STATUSES = ("Active", "Suspended", "Delisted")


def official_masters(http, folder):
    """Pin the first complete active, suspended and delisted lists separately."""
    for status in STATUSES:
        url = BSE_MASTER.replace("status=Active", f"status={status}")
        manifest = folder / (digest(url.encode()) + ".json")
        if manifest.is_file():
            receipt = json.loads(manifest.read_bytes())
            raw = (folder / (receipt["sha256"] + ".raw")).read_bytes()
            if receipt["url"] != url or digest(raw) != receipt["sha256"]:
                raise ValueError("BSE master does not match its frozen receipt")
        else:
            page = bse.page(http, url)
            raw = page.raw
            receipt = {
                "url": url,
                "sha256": digest(raw),
                "retrieved_url": page.retrieved_url,
                "observed_at": datetime.now(UTC).isoformat(),
            }
        rows = json.loads(raw)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"No {status} BSE equity master records")
        for row in rows:
            if (
                row["Status"] != status
                or "Segment" not in row
                or not re.fullmatch(r"[0-9]{6}", str(row["SCRIP_CD"]))
            ):
                raise ValueError(f"Malformed row in {status} BSE equity list")
        write_once(folder / (receipt["sha256"] + ".raw"), raw)
        if not manifest.exists():
            write_once(manifest, encoded(receipt))
        yield receipt, [row for row in rows if row["Segment"] == "Equity"]


def build(quarters, masters):
    symbols = defaultdict(set)
    for row in quarters:
        if not ISIN.fullmatch(row["isin"]) or not row["symbol"]:
            raise ValueError("Historical quarter lacks an ISIN or symbol")
        symbols[row["isin"]].add(row["symbol"])
    exact, issuer, ticker = (defaultdict(list) for _ in range(3))
    for source, records in masters:
        for row in records:
            isin = str(row["ISIN_NUMBER"] or "").upper().strip()
            symbol = str(row["scrip_id"] or "").upper().strip()
            entry = {
                "scrip_code": str(row["SCRIP_CD"]),
                "source_isin": isin or None,
                "source_url": source["url"],
                "sha256": source["sha256"],
            }
            if ISIN.fullmatch(isin):
                exact[isin].append(entry)
                issuer[isin[:7]].append(entry)
            if symbol:
                ticker[symbol].append(entry)
    leads, gaps = [], []
    for isin, aliases in sorted(symbols.items()):
        # Include alternate scrips as archive-search routes even when an exact
        # current ISIN exists. Admission requires the original quarter's identity.
        candidates = {}
        for reason, group in (
            ("exact_current_master_isin", exact[isin]),
            ("issuer_prefix_search_only", issuer[isin[:7]]),
            ("symbol_search_only", [item for a in sorted(aliases) for item in ticker[a]]),
        ):
            for entry in group:
                candidates.setdefault(
                    entry["scrip_code"],
                    {
                        "isin": isin,
                        "symbol": min(aliases),
                        "symbols": sorted(aliases),
                        "match": reason,
                        **entry,
                    },
                )
        if not candidates:
            gaps.append({"isin": isin, "symbols": sorted(aliases)})
        leads.extend(candidates[k] for k in sorted(candidates))
    return {
        "status": "Discovery only; validate the historical issue in the original filing.",
        "historical_isins": len(symbols),
        "leads": leads,
        "unmatched": gaps,
    }


def main():
    raw = QUARTERS.read_bytes()
    with bse.client() as http:
        report = build(
            csv.DictReader(raw.decode().splitlines()),
            official_masters(http, ROOT / "identity-sources"),
        )
    report["quarters_sha256"] = digest(raw)
    write_json(ROOT / "issuer-leads.json", report)
    print(
        json.dumps(
            {
                "historic_isins": report["historical_isins"],
                "unmatched": report["unmatched"],
                "search_codes": len({r["scrip_code"] for r in report["leads"]}),
            }
        )
    )


if __name__ == "__main__":
    main()
