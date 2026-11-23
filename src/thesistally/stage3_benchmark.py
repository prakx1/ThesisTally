"""Collect the dated Nifty 200 total-return comparator for the preregistered study."""

import argparse
import csv
import io
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import httpx

from . import market
from .pilot_data import write_json

HISTORY = "https://www.niftyindices.com/reports/historical-data"
ENDPOINT = "https://www.niftyindices.com/BackPage/getTotalReturnIndexString"


def download(folder, client, interval):
    start, end = interval
    path = folder / f"{start}_{end}.json"
    receipt = path.with_suffix(".source.json")
    payload = {
        "name": "NIFTY 200",
        "indexName": "NIFTY 200",
        "startDate": start.strftime("%d-%b-%Y"),
        "endDate": end.strftime("%d-%b-%Y"),
    }
    if path.exists() and receipt.exists():
        raw = path.read_bytes()
        meta = json.loads(receipt.read_bytes())
        if meta["sha256"] != market.sha(raw) or meta["query"] != payload or meta["url"] != ENDPOINT:
            raise market.MarketError(f"Benchmark vintage changed for {start}–{end}")
    else:
        r = client.post(ENDPOINT, json={"cinfo": json.dumps(payload)})
        r.raise_for_status()
        raw = r.content
        if not isinstance(r.json(), list):
            raise market.MarketError("NSE Indices returned an unrecognized TRI response")
        meta = {"url": ENDPOINT, "query": payload, "sha256": market.sha(raw)}
        with market.staging(folder) as tmp:
            (tmp / path.name).write_bytes(raw)
            (tmp / path.name).replace(path)
        write_json(receipt, meta)
    rows = {}
    for row in json.loads(raw):
        when = datetime.strptime(row["Date"], "%d %b %Y").date()
        if row["Index Name"].strip().upper() != "NIFTY 200" or not start <= when <= end:
            raise market.MarketError("Wrong TRI identity or date")
        level = float(row["TotalReturnsIndex"].replace(",", ""))
        if not math.isfinite(level) or level <= 0 or when in rows:
            raise market.MarketError(f"Invalid TRI print on {when}")
        rows[when] = level
    return meta, rows


def build(root):
    baseline = root / "baseline"
    sessions = [
        date.fromisoformat(d)
        for d in json.loads((baseline / "preflight.json").read_bytes())["sessions"]
    ]
    first = next(i for i, day in enumerate(sessions) if day.year == sessions[0].year + 3)
    expected = sessions[first - 1 :]
    start, end = expected[0], expected[-1]
    folder = root / "stage3" / "sources" / "benchmark"
    folder.mkdir(parents=True, exist_ok=True)
    spans = [
        (max(first, start), min(last, end))
        for y in range(start.year, end.year + 1)
        for first, last in ((date(y, 1, 1), date(y, 6, 30)), (date(y, 7, 1), date(y, 12, 31)))
        if first <= end and last >= start
    ]
    official = {}
    evidence = []
    with (
        httpx.Client(
            timeout=45,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": HISTORY,
                "X-Requested-With": "XMLHttpRequest",
            },
            transport=httpx.HTTPTransport(retries=2),
        ) as client,
        ThreadPoolExecutor(max_workers=3) as pool,
    ):
        for meta, rows in pool.map(lambda span: download(folder, client, span), spans):
            evidence.append(meta)
            official.update(rows)
    gaps = sorted(set(expected) - official.keys())
    selected = "Nifty 200 gross total return" if not gaps else "Nifty 200 price return"
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["day", "close"])
    for day in expected:
        if not gaps:
            level = official[day]
        else:
            folder_day = baseline / "market/sessions" / str(day)
            raw = (folder_day / "indices.csv").read_bytes()
            manifest = json.loads((folder_day / "manifest.json").read_bytes())
            if market.sha(raw) != manifest["sha256"]["indices.csv"]:
                raise market.MarketError(f"Different price-index archive on {day}")
            matches = [
                r
                for r in csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
                if r["Index Name"].strip().upper() in {"NIFTY 200", "CNX 200"}
            ]
            if len(matches) != 1:
                raise market.MarketError(f"Missing Nifty 200 close on {day}")
            level = float(matches[0]["Closing Index Value"])
        writer.writerow([str(day), level])
    raw = output.getvalue().encode()
    destination = root / "stage3"
    with market.staging(destination) as tmp:
        (tmp / "benchmark.csv").write_bytes(raw)
        (tmp / "benchmark.csv").replace(destination / "benchmark.csv")
    result = {
        "series": selected,
        "from": str(start),
        "to": str(end),
        "sessions": len(expected),
        "sha256": market.sha(raw),
        "source_page": HISTORY,
        "sources": evidence,
        "missing_tri_dates": list(map(str, gaps)),
    }
    write_json(destination / "benchmark.json", result)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("data"))
    proof = build(p.parse_args().root)
    print({k: v for k, v in proof.items() if k != "sources"})
