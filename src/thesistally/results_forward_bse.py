"""Collect BSE results notices and independently timestamped XBRL editions."""

from __future__ import annotations

import json
import re
from calendar import month_abbr, monthrange
from datetime import date, datetime
from urllib.parse import urlencode, urljoin, urlsplit
from xml.etree import ElementTree

import httpx

from .results_forward_nse import IST, XBRL, Page, _instance

FEEDS = ("bse-results",)
ISSUER_FEED = "bse-issuer-xbrl"
API = "https://api.bseindia.com/BseIndiaAPI/api/"
SITE = "https://www.bseindia.com"
CATALOGUE = API + "AnnSubCategoryGetData/w"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
    "Accept": "application/json",
}
PAGE_SIZE = 50


def client():
    return httpx.Client(
        headers=HEADERS, timeout=httpx.Timeout(30, connect=10), follow_redirects=True
    )


def official(url):
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (
        host in {"bseindia.com", "bseindia.in"} or host.endswith((".bseindia.com", ".bseindia.in"))
    )


def page(http, url):
    if not official(url):
        raise ValueError("Expected an official BSE source URL")
    response = http.get(url, headers=HEADERS)
    response.raise_for_status()
    final = str(response.url)
    if not official(final):
        raise ValueError("BSE response was redirected beyond the public exchange")
    return Page(url, final, response.content)


def as_stamp(value):
    if not isinstance(value, str) or len(value) < 19:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=IST)).astimezone(IST)


def _notice(raw, day, url):
    # Submission precedes dissemination; preserve both and use the exchange publication.
    stamped = [
        (field, t) for field in ("DissemDT", "NEWS_DT", "DT_TM") if (t := as_stamp(raw.get(field)))
    ]
    if stamped and not any(t.date() == day for _, t in stamped):
        raise ValueError("BSE result notice falls outside its requested day")
    if not raw.get("NEWSID") or not raw.get("SCRIP_CD") or raw.get("CATEGORYNAME") != "Result":
        raise ValueError("BSE result notice has unexpected category or missing accession")
    attachment = raw.get("ATTACHMENTNAME")
    # XML_NAME is an exchange news identifier, commonly ANN_<code>_<uuid>.
    # The announcement clock cannot be backdated into a different financial XBRL.
    xurl = next(
        (
            u
            for u in (raw.get("XML_NAME"), attachment)
            if official(u) and urlsplit(u).path.lower().endswith(".xml")
        ),
        None,
    )
    return {
        "feed": "bse-results",
        "kind": "notice",
        "accession": str(raw["NEWSID"]),
        "symbol": str(raw["SCRIP_CD"]),
        "isin": raw.get("ISIN") or None,
        "period_end": None,
        "basis": None,
        "announced_at": stamped[0][1].isoformat() if stamped else None,
        "timestamp_source": stamped[0][0] if stamped else None,
        "xml_url": xurl,
        "attachment_url": attachment if official(attachment) else None,
        "is_revision": None,
        "page_url": url,
        "row": raw,
    }


def collect(http, feed: str, day: date) -> tuple[list[Page], list[dict]]:
    if feed not in FEEDS:
        raise ValueError(f"Unknown BSE results feed {feed}")
    pages, notices = [], []
    seen = set()
    total = None
    while True:
        params = {
            "pageno": len(pages) + 1,
            "strCat": "Result",
            "strPrevDate": day.strftime("%Y%m%d"),
            "strScrip": "",
            "strSearch": "P",
            "strToDate": day.strftime("%Y%m%d"),
            "strType": "C",
            "subcategory": "-1",
        }
        fetched = page(http, CATALOGUE + "?" + urlencode(params))
        body = json.loads(fetched.raw)
        rows, count = body.get("Table"), body.get("Table1")
        if not isinstance(rows, list) or not isinstance(count, list) or len(count) != 1:
            raise ValueError("BSE did not supply a date-bounded result list and row count")
        current = count[0].get("ROWCNT")
        if (
            type(current) is not int
            or current < 0
            or total is not None
            and current != total
            or len(rows) != min(PAGE_SIZE, current - len(seen))
        ):
            raise ValueError("Incomplete or changing BSE result pagination")
        total = current
        pages.append(fetched)
        for raw in rows:
            notice = _notice(raw, day, fetched.url)
            if notice["accession"] in seen:
                raise ValueError("Repeated BSE accession while paging results")
            seen.add(notice["accession"])
            notices.append(notice)
        if len(seen) == total:
            return pages, notices


def latest_issuers(http) -> tuple[Page, set[str]]:
    """Discover issuers from the latest market-wide snapshot, which can lag today."""
    params = {"SCRIP_CD": "", "FlagDur": "1", "HFQ": "", "ISUBGROUP_CODE": "", "segment": "C"}
    fetched = page(http, API + "Corp_FinanceResult_ng_new/w?" + urlencode(params))
    body = json.loads(fetched.raw)
    if not isinstance(body, dict) or not isinstance(body.get("Table"), list):
        raise TypeError("Missing BSE latest financial catalogue table")
    codes = {str(row["Scrip_cd"]) for row in body["Table"]}
    if any(not re.fullmatch(r"[0-9]{6}", code) for code in codes):
        raise ValueError("Invalid BSE financial catalogue issuer")
    return fetched, codes


def _filing_url(value):
    if not isinstance(value, str) or not value:
        return None
    url = urljoin(SITE, value)
    if (
        not official(url)
        or not urlsplit(url).path.lower().startswith("/xbrlfiles/")
        or not urlsplit(url).path.lower().endswith((".xml", ".htm", ".html", ".xhtml"))
    ):
        return None
    return url


def collect_integrated(http, scrip: str, start: date, end: date) -> tuple[list[Page], list[dict]]:
    """Filter the publisher's entire issuer catalogue on each edition's own IST clock."""
    if not re.fullmatch(r"[0-9]{6}", scrip) or end < start:
        raise ValueError("Invalid BSE issuer or date range")
    fetched = page(http, API + "Integratedfinancedata/w?" + urlencode({"scripcode": scrip}))
    body = json.loads(fetched.raw)
    if not isinstance(body, dict) or not isinstance(body.get("Table"), list):
        raise TypeError("Missing BSE issuer financial filing table")
    records = []
    for row in body["Table"]:
        if not isinstance(row, dict) or not row.get("Quarter_Name") or row.get("Qtrid") is None:
            raise ValueError("Incomplete BSE structured financial filing")
        issuer_link = row.get("seourl")
        if issuer_link and urlsplit(issuer_link).path.rstrip("/").split("/")[-1] != scrip:
            raise ValueError("BSE catalogue returned a different issuer")
        status = (row.get("status") or "").lower()
        if status not in {"new", "revised"}:
            raise ValueError(f"Unrecognised BSE filing status {status!r}")
        revised = status == "revised"
        field = "revised_date_time" if revised else "filing_date_time"
        stamp = as_stamp(row.get(field))
        if not stamp:
            raise ValueError("Missing publication clock for BSE financial filing edition")
        if not start <= stamp.date() <= end:
            continue
        label = row["Quarter_Name"]
        parts = label.split("-")
        basis = parts[0].lower() if parts[0].lower() in {"standalone", "consolidated"} else None
        period = None
        if len(parts) >= 4 and parts[1].title() in month_abbr[1:] and parts[-1].isdigit():
            month = list(month_abbr).index(parts[1].title())
            year = int(parts[-1])
            period = date(year, month, monthrange(year, month)[1]).isoformat()
        xml_url = _filing_url(row.get("xbrlurl")) or _filing_url(row.get("XbrlFile"))
        records.append(
            {
                "feed": ISSUER_FEED,
                "kind": "filing",
                "accession": f"{scrip}/{row['Qtrid']}/{label}/{status}/{stamp.isoformat()}",
                "symbol": scrip,
                "isin": row.get("ISIN") or None,
                "period_end": period,
                "basis": basis,
                "announced_at": stamp.isoformat(),
                "timestamp_source": field,
                "xml_url": xml_url,
                "attachment_url": xml_url,
                "is_revision": revised,
                "page_url": fetched.url,
                "row": row,
            }
        )
    return [fetched], records


def _inline_instance(raw):
    try:
        root = ElementTree.fromstring(raw)
    except (ValueError, ElementTree.ParseError):
        return False
    contexts = {el.get("id") for el in root.iter(XBRL + "context") if el.get("id")}
    ix = ("{http://www.xbrl.org/2013/inlineXBRL}", "{http://www.xbrl.org/2008/inlineXBRL}")
    tags = {ns + tag for ns in ix for tag in ("nonNumeric", "nonFraction", "fraction")}
    return any(
        el.tag in tags and el.get("name") and el.get("contextRef") in contexts for el in root.iter()
    )


def fetch_xml(http, url: str) -> Page:
    if not official(url) or not urlsplit(url).path.lower().endswith(
        (".xml", ".htm", ".html", ".xhtml")
    ):
        raise ValueError("Expected an official BSE XBRL attachment")
    fetched = page(http, url)
    if not _instance(fetched.raw) and not _inline_instance(fetched.raw):
        raise ValueError("BSE attachment is missing XBRL contexts or inline facts")
    return fetched
