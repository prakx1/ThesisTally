"""Recover dated pre-REG_IND evidence from the exchange's public surveillance circulars."""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx

API = "https://www.nseindia.in/api/circulars"
ARCHIVE = "https://nsearchives.nseindia.in/content/circulars/"
FOLDER = Path("data/baseline/legacy-surveillance")
START = date(2014, 1, 1)
END = date(2024, 1, 21)
GSM = "gsm"
LT = "long_term_asm"
ST = "short_term_asm"
COMPONENTS = (GSM, LT, ST)
REVIEWED = json.loads((Path(__file__).parent / "validation/nse-legacy-notices.json").read_bytes())


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_bytes(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == raw:
        return
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)


def write_json(path, data):
    write_bytes(
        path, (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )


class EmptyResponse(ValueError):
    """The official archive returned 200 with an empty body on retry."""


class Cache:
    """Immutable source bytes, checksum-verified on reuse. Pace all worker requests together."""

    def __init__(
        self,
        folder,
        *,
        csv_header=b"Symbol,Series,Security Name,Band,Remarks",
        capture_modified=False,
    ):
        self.folder = folder
        self.csv_header = csv_header
        self.capture_modified = capture_modified
        self.lock = threading.Lock()
        self.last = 0.0
        self.client = httpx.Client(
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.nseindia.in/resources/exchange-communication-circulars",
            },
            timeout=30,
            follow_redirects=True,
        )

    def get(self, key, url):
        dst = self.folder / key
        record = dst.with_suffix(dst.suffix + ".source.json")
        if dst.is_file() and record.is_file():
            raw = dst.read_bytes()
            info = json.loads(record.read_bytes())
            if info["url"] != url or info["sha256"] != sha(raw):
                raise ValueError(f"Cached circular evidence disagrees with its receipt: {key}")
            return raw
        for attempt in range(4):
            with self.lock:
                time.sleep(max(0, self.last + 0.43 - time.monotonic()))
                self.last = time.monotonic()
            try:
                r = self.client.get(url)
                r.raise_for_status()
                raw = r.content
                if not raw:
                    raise EmptyResponse(url)
                if key.endswith(".json"):
                    json.loads(raw)
                elif key.endswith(".csv"):
                    if not raw.lstrip(b"\xef\xbb\xbf").startswith(self.csv_header):
                        raise ValueError("NSE returned an unrecognized daily complete-list schema")
                elif not raw.startswith((b"%PDF", b"PK")):
                    raise ValueError("NSE returned non-document content")
                write_bytes(dst, raw)
                receipt = {"url": url, "sha256": sha(raw)}
                if self.capture_modified:
                    receipt["last_modified"] = r.headers.get("Last-Modified")
                write_json(record, receipt)
                return raw
            except (httpx.HTTPError, ValueError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
                    raise
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
        raise RuntimeError("Unreachable download state")


def month_inventory(cache, start, end):
    qs = urlencode({"fromDate": start.strftime("%d-%m-%Y"), "toDate": end.strftime("%d-%m-%Y")})
    key = f"catalogue/{start.year}-{start.month:02}.json"
    inventory = json.loads(cache.get(key, API + "?" + qs))
    entries = inventory["data"]
    if inventory.get("fromDate") != start.strftime("%d-%m-%Y") or inventory.get(
        "toDate"
    ) != end.strftime("%d-%m-%Y"):
        raise ValueError(f"Returned circular inventory has different requested dates for {start}")
    if not entries or any(not start <= date.fromisoformat(r["cirDate"]) <= end for r in entries):
        raise ValueError(f"Empty or out-of-period circular inventory for {start}")
    return [r for r in entries if r["fileDept"] == "SURV"]


def inventory(cache):
    # First GSM announcement is in February 2017; earlier dates predate both frameworks.
    start = date(2017, 2, 1)
    months = []
    while start <= END:
        stop = min(END, start.replace(day=calendar.monthrange(start.year, start.month)[1]))
        months.append((start, stop))
        start = stop + timedelta(days=1)
    with ThreadPoolExecutor(max_workers=3) as pool:
        groups = list(pool.map(lambda span: month_inventory(cache, *span), months))
    return sorted(
        (r for group in groups for r in group), key=lambda r: (r["cirDate"], r["circDisplayNo"])
    )


def reconcile_catalogue(folder):
    """Detect an unadvertised monthly row cap using independently requested date halves."""
    cache = Cache(folder)
    months = sorted((folder / "catalogue").glob("????-??.json"))

    def month(path):
        full = json.loads(path.read_bytes())
        left = date.fromisoformat(path.stem + "-01")
        right = min(END, left.replace(day=calendar.monthrange(left.year, left.month)[1]))
        split = left.replace(day=15)

        def keys(rows):
            return {
                (r["cirDate"], r["circFilename"], r["circFilelink"], r["sub"]): r
                for r in rows
                if r["fileDept"] == "SURV"
            }

        def section(lo, hi, kind):
            q = urlencode({"fromDate": lo.strftime("%d-%m-%Y"), "toDate": hi.strftime("%d-%m-%Y")})
            url = API + "?" + q
            key = f"catalogue-{kind}/{lo}_{hi}.json"
            raw = cache.get(key, url)
            data = json.loads(raw)
            if data["fromDate"] != lo.strftime("%d-%m-%Y") or data["toDate"] != hi.strftime(
                "%d-%m-%Y"
            ):
                raise ValueError("Returned circular inventory date differs")
            if any(not lo <= date.fromisoformat(r["cirDate"]) <= hi for r in data["data"]):
                raise ValueError("Unexpected publication outside the requested period")
            sources.append({"file": key, "url": url, "sha256": sha(raw)})
            return keys(data["data"])

        sources = []
        monthly = keys(full["data"])
        halves = section(left, split, "halves") | section(
            split + timedelta(days=1), right, "halves"
        )
        weekly = {}
        start = left
        while start <= right:
            last = min(right, start + timedelta(days=6))
            weekly.update(section(start, last, "weeks"))
            start = last + timedelta(days=1)
        union = monthly | halves | weekly
        # A discrepancy between the narrower requests requires checking that
        # date's own official response. Never silently favor a wider request.
        differing = (union.keys() - weekly.keys()) | (union.keys() - halves.keys())
        uncertain = []
        for stamp in sorted({k[0] for k in differing}):
            d = date.fromisoformat(stamp)
            one = section(d, d, "days")
            three = section(
                max(left, d - timedelta(days=1)), min(right, d + timedelta(days=1)), "checks"
            )
            three = {k: v for k, v in three.items() if k[0] == stamp}
            union.update(one)
            union.update(three)
            expected = {k for k in union if k[0] == stamp}
            votes = sum(
                {k for k in source if k[0] == stamp} == expected
                for source in (halves, weekly, one, three)
            )
            if votes < 2:
                uncertain.append(str(d))
        name = path.stem
        rows = sorted(union.values(), key=lambda r: (r["cirDate"], r["circDisplayNo"]))
        output = folder / "catalogue-verified" / (name + ".json")
        write_json(output, {"data": rows})
        return {
            "month": name,
            "unique_surveillance_notices": len(rows),
            "unresolved_daily_inventory": uncertain,
            "monthly_omissions": sorted(k[1] for k in union.keys() - monthly.keys()),
            "half_month_omissions": sorted(k[1] for k in union.keys() - halves.keys()),
            "weekly_omissions": sorted(k[1] for k in union.keys() - weekly.keys()),
            "monthly_sha256": sha(path.read_bytes()),
            "verified_sha256": sha(output.read_bytes()),
            "observed_sources": sources,
        }

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            checks = list(pool.map(month, months))
        if len(checks) != 84:
            raise ValueError("All 84 monthly inventories are required")
        write_json(
            folder / "inventory-reconciliation.json", {"months": checks, "checked": len(checks)}
        )
    finally:
        cache.client.close()


def verified_inventory(folder):
    receipt = json.loads((folder / "inventory-reconciliation.json").read_bytes())
    if receipt["checked"] != 84 or len(receipt["months"]) != 84:
        raise ValueError("Incomplete certified circular inventories")
    rows = []
    for rec in receipt["months"]:
        f = folder / "catalogue-verified" / (rec["month"] + ".json")
        raw = f.read_bytes()
        if sha(raw) != rec["verified_sha256"]:
            raise ValueError(f"Corrupt verified inventory {f}")
        rows.extend(json.loads(raw)["data"])
    return rows


def component(row):
    title = re.sub(r"[^a-z0-9]+", " ", row["sub"].casefold())
    if "graded surveillance" in title or re.search(r"\bgsm\b", title):
        return GSM
    if "additional surveillance" not in title and not re.search(r"\basm\b", title):
        return None
    if any(
        word in title
        for word in (
            "insolvency",
            "inter creditor",
            "ibc",
            "ica framework",
            "derivative",
            "commodit",
        )
    ):
        return None
    if "short term" in title or "st asm" in title:
        return ST
    return LT


def acquire(folder):
    cache = Cache(folder)
    try:
        inventory(cache)
        manifest = folder / "inventory-reconciliation.json"
        available = manifest.is_file() and all(
            (folder / "catalogue-verified" / (r["month"] + ".json")).is_file()
            for r in json.loads(manifest.read_bytes())["months"]
        )
        if not available:
            reconcile_catalogue(folder)
        rows = verified_inventory(folder)
        selected = [r for r in rows if component(r) or r["circFilename"] in REVIEWED]

        # Source archives reference their own attachments by the same exact filename.
        def one(row):
            name = row["circFilename"]
            if (
                not re.fullmatch(r"(?i)surv\d+\.(?:zip|pdf)", name)
                or row["circFilelink"].split("/")[-1] != name
            ):
                return {"file": name, "error": "unexpected filename", "url": row["circFilelink"]}
            try:
                cache.get("raw/" + name, ARCHIVE + name)
            except (httpx.HTTPError, ValueError) as exc:
                return {"file": name, "error": str(exc), "url": ARCHIVE + name}

        print(f"{len(rows)} surveillance circulars, {len(selected)} relevant files", flush=True)
        gaps = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            for i, out in enumerate(pool.map(one, selected), 1):
                if out:
                    gaps.append(out)
                if i % 250 == 0:
                    print(f"{i}/{len(selected)} documents checked", flush=True)
        write_json(
            folder / "acquisition.json",
            {"inventory_rows": len(rows), "selected": len(selected), "errors": gaps},
        )
        return rows
    finally:
        cache.client.close()


def acquire_gsm(folder, sessions):
    cache = Cache(folder)
    days = [
        day.name
        for day in sorted(sessions.iterdir())
        if "2017-03-03" <= day.name <= END.isoformat()
    ]

    def fetch(day):
        day = date.fromisoformat(day)
        name = f"sec_list_{day:%d%m%Y}.csv"
        key = "raw/" + name
        url = "https://nsearchives.nseindia.in/content/equities/" + name
        missing = folder / "missing" / (name + ".json")
        if missing.exists():
            return json.loads(missing.read_bytes())["status"]
        try:
            cache.get(key, url)
            return 200
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 404:
                write_json(missing, {"url": url, "status": status})
            return status
        except EmptyResponse:
            write_json(missing, {"url": url, "status": "empty_http_200", "sha256": sha(b"")})
            return "empty_http_200"
        except (ValueError, httpx.HTTPError):
            return "read_error"

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            stats = {}
            for i, status in enumerate(pool.map(fetch, days), 1):
                stats[str(status)] = stats.get(str(status), 0) + 1
                if i % 250 == 0:
                    print(i, stats, flush=True)
        write_json(folder / "daily-gsm-acquisition.json", {"requested": len(days), "counts": stats})
    finally:
        cache.client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=FOLDER)
    parser.add_argument("--gsm-daily", action="store_true")
    parser.add_argument("--reconcile-catalogue", action="store_true")
    parser.add_argument("--sessions", type=Path, default=Path("data/sessions"))
    args = parser.parse_args()
    if args.reconcile_catalogue:
        reconcile_catalogue(args.folder)
    elif args.gsm_daily:
        acquire_gsm(args.folder, args.sessions)
    else:
        acquire(args.folder)
