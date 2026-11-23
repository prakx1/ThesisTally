"""Backfill NSE controls published before the July 2024 bhavcopy format change."""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from itertools import pairwise
from pathlib import Path

import httpx

from . import exchange, market
from .pilot_data import write_json

SURVEILLANCE_NOTICE = "https://nsearchives.nseindia.com/content/circulars/SURV60281.zip"
MASTER_NOTICE = "https://nsearchives.nseindia.com/content/circulars/MSD60315.pdf"
FIRST_INDICATOR = date(2024, 1, 17)
FIRST_MASTER = date(2024, 2, 5)
FIRST_EXISTING = date(2024, 7, 8)


def source_urls(day, prior):
    sources = {}
    if day >= FIRST_INDICATOR:
        # Published at this close for the security's eligibility on the next session.
        sources["surveillance.csv"] = f"{market.ARCHIVE}/cm/REG_IND{day:%d%m%y}.csv"
    if prior >= FIRST_MASTER:
        # An opening price range must come from the previous completed session.
        sources["security.csv.gz"] = f"{market.ARCHIVE}/cm/NSE_CM_security_{prior:%d%m%Y}.csv.gz"
    return sources


def fetch(client, url):
    for attempt in range(4):
        try:
            response = client.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.content
        except httpx.HTTPError:
            if attempt == 3:
                raise
            time.sleep(2**attempt)


def verify_day(folder, day, prior, members):
    record = json.loads((folder / "sources.json").read_bytes())
    if record["prior"] != str(prior):
        raise market.MarketError(f"Incorrect master reference session on {day}")
    expected = source_urls(day, prior)
    if set(record["files"]) | set(record["not_published"]) != set(expected):
        raise market.MarketError(f"Incomplete control inventory on {day}")
    raw = {}
    for name, proof in record["files"].items():
        raw[name] = (folder / name).read_bytes()
        if proof["url"] != expected[name] or market.sha(raw[name]) != proof["sha256"]:
            raise market.MarketError(f"Control source mismatch for {name} on {day}")
    for name, url in record["not_published"].items():
        if url != expected[name]:
            raise market.MarketError(f"Unexpected missing source on {day}")
    decoded = exchange.decode_controls(
        members, raw.get("surveillance.csv"), raw.get("security.csv.gz")
    )
    saved = (folder / "symbols.json").read_bytes()
    if market.sha(saved) != record["symbols_hash"] or decoded != json.loads(saved):
        raise market.MarketError(f"Decoded controls differ on {day}")
    return {
        "day": str(day),
        "files": list(raw),
        "not_published": record["not_published"],
        "member_observations": len(decoded),
        "known_surveillance": sum(v["surveillance"] != "unknown" for v in decoded.values()),
        "known_opening_bands": sum(
            v["floor"] is not None and v["ceiling"] is not None for v in decoded.values()
        ),
        "sources_sha256": market.sha((folder / "sources.json").read_bytes()),
    }


def collect(root):
    out = root / "baseline" / "earlier-controls"
    out.mkdir(parents=True, exist_ok=True)
    pairs = [
        (d, p) for p, d in pairwise(market.sessions(root)) if FIRST_INDICATOR <= d < FIRST_EXISTING
    ]

    def one(pair):
        day, prior = pair
        folder = out / str(day)
        members = market.read_members((root / "sessions" / str(day) / "members.csv").read_bytes())
        if not folder.exists():
            with market.staging(out) as tmp:
                raw, files, absent = {}, {}, {}
                for name, url in source_urls(day, prior).items():
                    data = fetch(client, url)
                    if data is None:
                        absent[name] = url
                        continue
                    raw[name] = data
                    (tmp / name).write_bytes(data)
                    files[name] = {"url": url, "sha256": market.sha(data)}
                decoded = exchange.decode_controls(
                    members, raw.get("surveillance.csv"), raw.get("security.csv.gz")
                )
                decoded_raw = json.dumps(decoded, sort_keys=True).encode()
                (tmp / "symbols.json").write_bytes(decoded_raw)
                (tmp / "sources.json").write_text(
                    json.dumps(
                        {
                            "files": files,
                            "symbols_hash": market.sha(decoded_raw),
                            "prior": str(prior),
                            "not_published": absent,
                        },
                        sort_keys=True,
                    )
                )
                tmp.rename(folder)
        return verify_day(folder, day, prior, members)

    with (
        httpx.Client(
            timeout=35, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
        ) as client,
        ThreadPoolExecutor(max_workers=4) as pool,
    ):
        records = list(pool.map(one, pairs))
    result = {
        "complete": True,
        "source_notices": [SURVEILLANCE_NOTICE, MASTER_NOTICE],
        "from": str(pairs[0][0]),
        "to": str(pairs[-1][0]),
        "sessions": len(records),
        "sessions_with_files": sum(bool(r["files"]) for r in records),
        "all_reports_archive_from": "2024-01-22",
        "publication_basis": "EOD flags on signal session D govern next session; opening price range from the prior exchange session master.",
        "known_surveillance": sum(r["known_surveillance"] for r in records),
        "known_opening_bands": sum(r["known_opening_bands"] for r in records),
        "days": records,
    }
    write_json(out / "coverage.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    result = collect(parser.parse_args().root)
    print({k: v for k, v in result.items() if k != "days"})
