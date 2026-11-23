"""Acquire and read dated AMFI six-month full-market-cap worksheets for research."""

import argparse
import hashlib
import html
import io
import json
import os
import re
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx

from . import discovery_guard

INDEX = "https://www.amfiindia.com/otherdata/categorisation-of-stocks"
PUBLICATION_RULE = "https://www.sebi.gov.in/sebi_data/attachdocs/jul-2018/1531376960294.pdf"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
IST = ZoneInfo("Asia/Kolkata")


def read_sheet(raw):
    """Read the header-labelled ISIN and full-cap fields, including cached formula values."""
    with zipfile.ZipFile(io.BytesIO(raw)) as doc:
        strings = ["".join(s.itertext()) for s in ET.fromstring(doc.read("xl/sharedStrings.xml"))]
        sheet = ET.fromstring(doc.read("xl/worksheets/sheet1.xml")).find(NS + "sheetData")
        core = ET.fromstring(doc.read("docProps/core.xml"))
    modified = core.findtext("{http://purl.org/dc/terms/}modified")
    rows = []
    for row in sheet:
        cells = {}
        for cell in row:
            value = cell.findtext(NS + "v")
            if value is not None:
                col = re.match(r"[A-Z]+", cell.attrib["r"])[0]
                cells[col] = strings[int(value)] if cell.get("t") == "s" else value
        rows.append(cells)
    title = next((v for row in rows[:3] for v in row.values() if "six month" in v.lower()), "")
    year = re.search(r"20\d\d", title)
    if not year or not re.search(r"\b(?:jun|dec)", title, re.IGNORECASE):
        raise ValueError(f"Unrecognized AMFI reporting period: {title!r}")
    june = re.search(r"\bjun", title, re.IGNORECASE)
    period = date(int(year[0]), 6 if june else 12, 30 if june else 31)
    header = next(r for r in rows[:5] if "ISIN" in r.values())
    isin = next(k for k, v in header.items() if v == "ISIN")
    cap = next(k for k, v in header.items() if "average of all exchanges" in v.casefold())
    amounts, conflicts = {}, set()
    for row in rows:
        code = row.get(isin, "").strip()
        if not re.fullmatch(r"IN[A-Z0-9]{10}", code):
            continue
        value = row.get(cap, "").strip()
        if not value or value in ("-", "NA", "N.A.", "#N/A"):
            continue
        number = float(value.replace(",", ""))
        if number <= 0:
            continue
        if code in amounts and amounts[code] != number:
            conflicts.add(code)
        amounts[code] = number
    for code in conflicts:
        del amounts[code]
    if len(amounts) < 1000 or not modified:
        raise ValueError(f"Incomplete AMFI cap sheet at {period}")
    return (
        period,
        datetime.fromisoformat(modified).astimezone(IST).date(),
        amounts,
        sorted(conflicts),
    )


def usable_date(period, modified, http_modified):
    """Allow the whole five-day publication period. Late revisions need public dating."""
    deadline = period + timedelta(days=5)
    if modified < period or http_modified < modified:
        raise ValueError("Inconsistent AMFI worksheet vintage")
    if http_modified <= period + timedelta(days=30) or modified > deadline:
        return max(deadline, http_modified) + timedelta(days=1)
    return deadline + timedelta(days=1)


def cache_file(directory, name, payload):
    """Never replace archived bytes, including a published manifest."""
    if not re.fullmatch(r"(?:[a-f0-9]{64}\.xlsx|manifest\.json)", name):
        raise ValueError("Unexpected AMFI cache name")
    folder = directory / "sources/amfi"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("AMFI data changed; archive a new discovery family revision")
    else:
        with path.open("xb") as stream:
            stream.write(payload)


def persist(directory, name, payload):
    command = [sys.executable, "-B", "-m", "thesistally.discovery_cap", "--store", name]
    env = {**os.environ, "PYTHONPATH": str(discovery_guard.REPO / "src")}
    discovery_guard.confined(command, directory, env=env, stdin=payload)


def acquire():
    directory = discovery_guard.output_root()
    with httpx.Client(follow_redirects=True, timeout=25) as client:
        index = client.get(INDEX)
        index.raise_for_status()
        urls = {
            html.unescape(u.strip())
            for u in re.findall('href="([^"]+)"', index.text)
            if u.strip().lower().endswith(".xlsx")
        }
        if not urls:
            raise ValueError("No AMFI cap worksheet links in the official index")

        def fetch(url):
            if urlparse(url).hostname not in ("www.amfiindia.com", "portal.amfiindia.com"):
                raise ValueError("AMFI link is not an allowed source")
            response = client.get(url)
            response.raise_for_status()
            payload = response.content
            digest = hashlib.sha256(payload).hexdigest()
            period, modified, data, conflicts = read_sheet(payload)
            header = response.headers["Last-Modified"]
            last = parsedate_to_datetime(header).astimezone(IST).date()
            first = usable_date(period, modified, last)
            persist(directory, digest + ".xlsx", payload)
            return {
                "period": str(period),
                "available": str(first),
                "workbook_modified": str(modified),
                "http_modified": header,
                "isin_count": len(data),
                "conflicting_isins": conflicts,
                "url": str(response.url),
                "sha256": digest,
            }

        with ThreadPoolExecutor(max_workers=3) as pool:
            entries = sorted(pool.map(fetch, sorted(urls)), key=lambda row: row["period"])
    if len({r["period"] for r in entries}) != len(entries):
        raise ValueError("Two official worksheets describe the same period")
    payload = (
        json.dumps(
            {"source": INDEX, "publication_rule": PUBLICATION_RULE, "sheets": entries},
            indent=2,
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    persist(directory, "manifest.json", payload)
    print([(r["period"], r["available"]) for r in entries])


def load(directory):
    folder = Path(directory) / "sources/amfi"
    manifest = json.loads((folder / "manifest.json").read_bytes())
    result = []
    for r in manifest["sheets"]:
        digest = r["sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid AMFI source fingerprint")
        raw = (folder / (digest + ".xlsx")).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("AMFI market cap source has changed")
        period, modified, amounts, conflicts = read_sheet(raw)
        last = parsedate_to_datetime(r["http_modified"]).astimezone(IST).date()
        usable = usable_date(period, modified, last)
        if (
            str(period) != r["period"]
            or str(usable) != r["available"]
            or conflicts != r["conflicting_isins"]
        ):
            raise ValueError("Changed AMFI usable date")
        result.append((period, usable, digest, amounts))
    return sorted(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.store:
        cache_file(discovery_guard.output_root(), args.store, sys.stdin.buffer.read())
    else:
        acquire()
