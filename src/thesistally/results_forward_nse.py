"""Fetch fresh market-wide NSE results and notices using their public exchange clocks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from .fundamental_notice_proof import REVISION
from .fundamental_notices import BOARD, NEGATIVE, RESULT
from .fundamental_nse import BASE, HEAD, dt, public_copy, published_at

FEEDS = ("legacy-quarterly", "legacy-annual", "integrated", "announcements")
PAGE_SIZE = 100
IST = ZoneInfo("Asia/Kolkata")
XBRL = "{http://www.xbrl.org/2003/instance}"
ARCHIVES = {"nsearchives.nseindia.in", "nsearchives.nseindia.com", "archives.nseindia.com"}


@dataclass(frozen=True)
class Page:
    url: str
    retrieved_url: str
    raw: bytes


def client():
    return httpx.Client(headers=HEAD, timeout=httpx.Timeout(30, connect=10), follow_redirects=True)


def _json_response(raw):
    try:
        return isinstance(json.loads(raw), (list, dict))
    except (ValueError, UnicodeDecodeError):
        return False


def _page(http, path, params):
    url = BASE + path + "?" + urlencode(params)
    raw, retrieved = public_copy(http, url, accept=_json_response)
    return Page(url, retrieved, raw)


def _timestamp(row, fields, *, latest=False):
    values = [(stamp, k) for k in fields if (stamp := published_at(row.get(k)))]
    if not values:
        return None, None
    stamp, field = max(values, key=lambda v: v[0]) if latest else values[0]
    return stamp.replace(tzinfo=IST).isoformat(), field


def _attachment(*urls, xml=False):
    for url in urls:
        if not isinstance(url, str):
            continue
        path = urlsplit(url)
        name = path.path.rsplit("/", 1)[-1].lower()
        if (
            path.scheme == "https"
            and path.hostname in ARCHIVES
            and name not in {"", "-", "null"}
            and (not xml or name.endswith(".xml"))
        ):
            return url
    return None


def _record(feed, day, row, url):
    """Keep original exchange columns alongside the selected structured release clock."""
    notice = feed == "announcements"
    integrated = feed == "integrated"
    if integrated:
        fields = ("creation_Date", "broadcast_Date", "revised_Date")
        selected = (row.get("type") or "").strip().casefold() == "integrated filing- financials"
        accession = row.get("seq_Id")
        release_type = str(row.get("type_Sub") or "").lower()
        revision = {"original": False, "revision": True, "revised": True}.get(release_type)
        attachment = _attachment(row.get("pdf_attach"), row.get("ixbrl"))
    elif notice:
        fields = ("exchdisstime", "an_dt")
        desc = row.get("desc") or ""
        text = row.get("attchmntText") or ""
        selected = not NEGATIVE.search(desc) and (
            BOARD.search(desc) or RESULT.search(desc) or RESULT.search(text)
        )
        accession = row.get("seq_id")
        revision = True if REVISION.search(desc) else None
        attachment = _attachment(row.get("attchmntFile"))
    else:
        fields = ("exchdisstime", "broadCastDate")
        selected = True
        if row.get("period") != feed.split("-")[1].title():
            raise ValueError("NSE financial results returned another requested reporting frequency")
        accession = row.get("seqNumber")
        revision = True if REVISION.search(row.get("resultDescription") or "") else None
        attachment = _attachment(row.get("resultDetailedDataLink"))

    stamps = [parsed for field in fields if (parsed := published_at(row.get(field)))]
    if stamps and not any(t.date() == day for t in stamps):
        raise ValueError("NSE results feed returned a record outside its requested day")
    if not selected:
        return None
    if not row.get("symbol") or accession in (None, ""):
        raise ValueError("NSE filing lacks its published symbol or accession")
    stamp, source = _timestamp(row, fields, latest=integrated)
    period = dt(row.get("toDate") or row.get("qe_Date"))
    basis = {
        "consolidated": "consolidated",
        "non-consolidated": "standalone",
        "standalone": "standalone",
    }.get((row.get("consolidated") or "").strip().lower())
    return {
        "feed": feed,
        "kind": "notice" if notice else "filing",
        "accession": str(accession),
        "symbol": row["symbol"],
        "isin": row.get("isin") or row.get("sm_isin") or None,
        "period_end": period.date().isoformat() if period else None,
        "basis": basis,
        "announced_at": stamp,
        "timestamp_source": source,
        "xml_url": _attachment(
            row.get("xbrl"), row.get("resultDetailedDataLink"), row.get("attchmntFile"), xml=True
        ),
        "attachment_url": attachment,
        "is_revision": revision,
        "page_url": url,
        "row": row,
    }


def _integrated(http, params):
    pages = []
    seen = set()
    expected = None
    page_no = 1
    while True:
        page = _page(
            http, "integrated-filing-results", {**params, "page": page_no, "size": PAGE_SIZE}
        )
        body = json.loads(page.raw)
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise TypeError("Malformed NSE integrated results response")
        rows = body["data"]
        size, number, total = body.get("size"), body.get("page"), body.get("totalCount")
        if (
            type(total) is not int
            or total < 0
            or number != page_no - 1
            or size != PAGE_SIZE
            or (expected is not None and expected != total)
            or len(rows) != min(PAGE_SIZE, total - len(seen))
        ):
            raise ValueError("Incomplete or changing NSE integrated results pagination")
        ids = [str(r["seq_Id"]) for r in rows if r.get("seq_Id") not in (None, "")]
        if len(set(ids)) != len(rows) or seen.intersection(ids):
            raise ValueError("Repeated or missing accession in NSE integrated results pagination")
        seen.update(ids)
        pages.append(page)
        if len(seen) == total:
            return pages
        page_no += 1
        expected = total


def collect(http, feed: str, day: date) -> tuple[list[Page], list[dict]]:
    if feed not in FEEDS:
        raise ValueError(f"Unknown NSE results feed {feed}")
    params = {"from_date": day.strftime("%d-%m-%Y"), "to_date": day.strftime("%d-%m-%Y")}
    if feed == "integrated":
        pages = _integrated(http, params)
    elif feed == "announcements":
        pages = [_page(http, "corporate-announcements", {"index": "equities", **params})]
    else:
        pages = [
            _page(
                http,
                "corporates-financial-results",
                {"index": "equities", "period": feed.split("-")[1].title(), **params},
            )
        ]
    records = []
    for page in pages:
        body = json.loads(page.raw)
        rows = body["data"] if feed == "integrated" else body
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError("Expected a dated NSE result list")
        for row in rows:
            if record := _record(feed, day, row, page.url):
                records.append(record)
    return pages, records


def _instance(raw):
    try:
        doc = ElementTree.fromstring(raw)
        return doc.tag == XBRL + "xbrl" and doc.find(XBRL + "context") is not None
    except (ElementTree.ParseError, ValueError):
        return False


def fetch_xml(http, url: str) -> Page:
    if not _attachment(url, xml=True):
        raise ValueError("Expected an official NSE XBRL attachment URL")
    raw, retrieved = public_copy(http, url, accept=_instance)
    return Page(url, retrieved, raw)
