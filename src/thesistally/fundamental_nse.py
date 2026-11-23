"""Archive dated NSE quarterly financial results and immutable issuer attachments."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "data/fundamentals/nse"
CAT = ROOT / "catalogues"
BASE = "https://www.nseindia.in/api/"
START = date(2011, 1, 1)
END = date(2026, 11, 20)
HEAD = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.in/"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pack(path, raw):
    """Never replace bytes. Content hashes also distinguish later exchange revisions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if gzip.decompress(path.read_bytes()) != raw:
            raise ValueError(f"Conflicting immutable object {path}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staging:
        staging.write(gzip.compress(raw, compresslevel=3, mtime=0))
    try:
        try:
            os.link(staging.name, path)
        except FileExistsError:
            if gzip.decompress(path.read_bytes()) != raw:
                raise ValueError(f"Conflicting immutable object {path}")
    finally:
        os.unlink(staging.name)


def _one_url(client, url):
    for attempt in range(3):
        try:
            response = client.get(url)
            response.raise_for_status()
            host = urlsplit(str(response.url)).hostname or ""
            if not host.endswith((".nseindia.in", ".nseindia.com")):
                raise ValueError("NSE source redirected outside official domains")
            return response.content, str(response.url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                raise
        except httpx.TransportError:
            # Move to the next official copy after a transport timeout.
            raise
        time.sleep(2**attempt)


def public_copy(client, url, *, accept=None):
    """NSE serves the same archive paths under its published .in and .com origins."""
    host = urlsplit(url).hostname
    if host in {"nsearchives.nseindia.in", "nsearchives.nseindia.com", "archives.nseindia.com"}:
        alternatives = [
            url.replace(host, h, 1)
            for h in (
                "archives.nseindia.com",
                "nsearchives.nseindia.com",
                "nsearchives.nseindia.in",
            )
        ]
    elif host == "www.nseindia.in":
        alternatives = [url, url.replace(host, "www.nseindia.com", 1)]
    else:
        raise ValueError(f"Unexpected NSE origin {host}")
    failures = []
    for alternative in alternatives:
        origin = urlsplit(alternative).hostname
        try:
            raw, retrieved = _one_url(client, alternative)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status not in (403, 404, 410, 500, 502, 503, 504):
                raise
            failures.append(f"{origin}=HTTP {status}")
        except httpx.TransportError as exc:
            failures.append(f"{origin}=connection {type(exc).__name__}: {str(exc)[:40]}")
        else:
            if accept is None or accept(raw):
                return raw, retrieved
            # Preserve an independently checkable receipt even for a successful
            # HTTP response containing an empty body, login page or different filing.
            failures.append(f"{origin}=invalid body ({len(raw)} bytes, sha256 {digest(raw)})")
    raise ValueError("Official archive responses: " + "; ".join(failures))


def catalogue(client, url):
    key = digest(url.encode())
    path = CAT / (key + ".json.gz")
    if path.exists():
        return json.loads(gzip.decompress(path.read_bytes()))
    raw, retrieved = public_copy(client, url)
    result = json.loads(raw)
    pack(path, raw)
    write_json(
        CAT / (key + ".receipt.json"),
        {"url": url, "retrieved_url": retrieved, "sha256": digest(raw)},
    )
    return result


def dt(text):
    if not text or text == "-":
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


def published_at(value):
    """A calendar date alone cannot establish an exchange announcement clock."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y %H:%M:%S")
    except ValueError:
        return None


def members():
    with (REPO / "data/universes/nifty200_membership.csv").open() as src:
        return list(csv.DictReader(src))


def dated_isin(intervals, quarter, reported):
    active = {i["isin"] for i in intervals if i["from_date"] <= quarter <= i["to_date"]}
    ids = active or {i["isin"] for i in intervals}
    if reported in ids:
        return reported, "exchange_and_membership"
    if len(ids) == 1:
        candidate = next(iter(ids))
        if not reported:
            return candidate, "dated_symbol"
        # A different share-class code is only a candidate. The original filing must
        # certify the dated ISIN before the earlier catalogue alias may be resolved.
        if candidate[:7] == reported[:7] and candidate[:3] == "INE":
            return candidate, "require_original_isin"
    return None, "unresolved_issuer_identity"


def inventories(client, *, historical_aliases=False):
    by_symbol = defaultdict(list)
    by_isin = defaultdict(list)
    for interval in members():
        by_isin[interval["isin"]].append(interval)
        by_symbol[interval["symbol"]].append(interval)
    jobs = []
    # The date-bounded all-company catalogue also locates retired symbols and changed ISINs.
    for year in range(START.year, END.year + 1):
        for period in ("Quarterly", "Annual"):
            params = {
                "index": "equities",
                "period": period,
                "from_date": f"01-01-{year}",
                "to_date": min(date(year, 12, 31), END).strftime("%d-%m-%Y"),
            }
            jobs.append(("legacy", "", BASE + "corporates-financial-results?" + urlencode(params)))
    for symbol in [] if historical_aliases else sorted(by_symbol):
        jobs.append(
            (
                "integrated",
                symbol,
                BASE
                + "integrated-filing-results?"
                + urlencode({"symbol": symbol, "page": 1, "size": 100}),
            )
        )

    def collect(job):
        typ, _symbol, url = job
        try:
            data = catalogue(client, url)
            if typ == "legacy":
                if not isinstance(data, list):
                    raise ValueError("Invalid legacy catalogue")
                return job, data, None
            rows = list(data["data"])
            expected = int(data.get("totalCount", len(rows)))
            while len(rows) < expected:
                page = len(rows) // 100 + 1
                later = catalogue(client, url.replace("page=1&", f"page={page}&"))["data"]
                if not later or later[0] in rows:
                    raise ValueError("Incomplete integrated pagination")
                rows.extend(later)
            return job, rows, None
        except (ValueError, httpx.HTTPError, OSError, KeyError) as exc:
            return job, [], str(exc)[:300]

    failures = []
    plans = {}
    counts = []
    with ThreadPoolExecutor(8) as pool:
        for (typ, symbol, url), rows, fail in pool.map(collect, jobs):
            if fail:
                failures.append([url, fail])
            counts.append({"url": url, "rows": len(rows), "error": fail})
            for r in rows:
                sym = r.get("symbol")
                if historical_aliases:
                    issuer_intervals = by_isin.get(r.get("isin"), [])
                    if not issuer_intervals:
                        continue
                elif sym in by_symbol:
                    issuer_intervals = by_symbol[sym]
                else:
                    continue
                period = dt(r.get("toDate") or r.get("qe_Date"))
                if not period or not START <= period.date() <= END:
                    continue
                # Admit data from before membership for the trailing-eight-change warmup.
                intervals = [
                    m
                    for m in issuer_intervals
                    if date.fromisoformat(m["from_date"]) - timedelta(days=3 * 366 + 100)
                    <= period.date()
                    <= date.fromisoformat(m["to_date"]) + timedelta(days=91)
                ]
                if not intervals:
                    continue
                if typ == "legacy":
                    if r.get("period") not in ("Quarterly", "Annual"):
                        continue
                    reported = r.get("isin")
                    # A catalogue may show the current security ISIN for all earlier filings.
                    # Honour the period's shareholder identity where the catalogue supports it.
                    isin, identity = dated_isin(intervals, period.date().isoformat(), reported)
                    if not isin:
                        failures.append([f"legacy/{sym}/{r.get('seqNumber')}", identity])
                        continue
                    links = [r.get("xbrl"), r.get("resultDetailedDataLink")]
                    stamp = (
                        r.get("exchdisstime")
                        if published_at(r.get("exchdisstime"))
                        else r.get("broadCastDate")
                    )
                    rid = str(r.get("seqNumber"))
                else:
                    if r.get("type") != "Integrated Filing- Financials":
                        continue
                    if r.get("symbol") != symbol:
                        continue
                    isin, identity = dated_isin(intervals, period.date().isoformat(), None)
                    if not isin:
                        failures.append([f"integrated/{sym}/{r.get('seq_Id')}", identity])
                        continue
                    links = [r.get("xbrl")]
                    stamp = max(
                        (
                            v
                            for v in [
                                r.get("creation_Date"),
                                r.get("broadcast_Date"),
                                r.get("revised_Date"),
                            ]
                            if published_at(v)
                        ),
                        key=published_at,
                        default=None,
                    )
                    rid = str(r.get("seq_Id"))
                stamp_dt = published_at(stamp)
                if not stamp_dt or stamp_dt.date() > END:
                    continue
                basis = {
                    "consolidated": "consolidated",
                    "non-consolidated": "standalone",
                    "standalone": "standalone",
                }.get((r.get("consolidated") or "").lower())
                if not basis:
                    continue
                document = next(
                    (s for s in links if s and s.lower().endswith((".xml", ".html"))), None
                )
                if not document or urlsplit(document).hostname not in {
                    "archives.nseindia.com",
                    "nsearchives.nseindia.in",
                    "nsearchives.nseindia.com",
                }:
                    failures.append([f"{typ}/{rid}", "No official original attachment"])
                    document = None
                key = "/".join((typ, sym, isin, str(period.date()), basis, rid))
                record = {
                    "id": key,
                    "identity": identity,
                    "isin": isin,
                    "symbol": sym,
                    "period_end": period.date().isoformat(),
                    "basis": basis,
                    "filed_at": stamp_dt.isoformat() + "+05:30",
                    "source_url": document,
                    "catalogue_url": url,
                    "metadata": r,
                }
                if key not in plans or r.get("period") == "Quarterly":
                    plans[key] = record
    if historical_aliases:
        previous = {r["id"] for r in json.loads((ROOT / "attachment-plan.json").read_bytes())}
        aliases = sorted(
            (r for key, r in plans.items() if key not in previous), key=lambda r: r["id"]
        )
        write_json(ROOT / "dated-alias-attachment-plan.json", aliases)
        write_json(ROOT / "dated-alias-catalogue-gaps.json", failures)
        return aliases
    write_json(ROOT / "catalogue-manifest.json", counts)
    write_json(ROOT / "catalogue-gaps.json", failures)
    write_json(ROOT / "attachment-plan.json", sorted(plans.values(), key=lambda x: x["id"]))
    print(f"{len(plans)} attachments, {len(failures)} catalogue gaps", flush=True)
    return list(plans.values())


def write_json(path, obj):
    raw = (
        json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staging:
        staging.write(raw)
    os.replace(staging.name, path)


def archive_structured(client, url):
    receipts = ROOT / "receipts"
    key = digest(url.encode())
    receipt = receipts / (key + ".json")
    if receipt.exists():
        return None
    try:

        def valid_attachment(raw):
            prefix = raw[:10000].lower()
            return (b"xbrl" in prefix) if url.endswith(".xml") else (b"financial" in prefix)

        raw, retrieved = public_copy(client, url, accept=valid_attachment)
        sha = digest(raw)
        pack(ROOT / "objects" / sha[:2] / (sha + ".gz"), raw)
        write_json(
            receipt, {"url": url, "sha256": sha, "bytes": len(raw), "retrieved_url": retrieved}
        )
        return None
    except (ValueError, httpx.HTTPError, OSError) as exc:
        return [url, str(exc)[:1200]]


def attachments(client, plans, *, gaps_name="attachment-gaps.json", workers=12):
    receipts = ROOT / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)

    urls = sorted({r["source_url"] for r in plans if r["source_url"]})
    gaps = []
    with ThreadPoolExecutor(workers) as pool:
        for i, result in enumerate(pool.map(lambda u: archive_structured(client, u), urls), 1):
            if result:
                gaps.append(result)
            if i % 1000 == 0:
                print(f"Attachments {i}/{len(urls)}, {len(gaps)} unavailable", flush=True)
    write_json(ROOT / gaps_name, gaps)


def load_plans(root):
    primary = json.loads((root / "attachment-plan.json").read_bytes())
    alias = root / "dated-alias-attachment-plan.json"
    if alias.exists():
        primary.extend(json.loads(alias.read_bytes()))
    return primary


def historical_aliases():
    with httpx.Client(
        headers=HEAD, timeout=httpx.Timeout(30, connect=12), follow_redirects=True
    ) as client:
        plans = inventories(client, historical_aliases=True)
        attachments(client, plans, gaps_name="dated-alias-attachment-gaps.json", workers=3)


def main():
    with httpx.Client(
        headers=HEAD, timeout=httpx.Timeout(30, connect=12), follow_redirects=True
    ) as client:
        plans = inventories(client)
        attachments(client, plans)


if __name__ == "__main__":
    from .fundamental_sources import run

    run("structured")
