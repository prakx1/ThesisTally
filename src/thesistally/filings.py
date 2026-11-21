"""Point-in-time NSE and BSE filing discovery, archiving, and extraction references."""

import re
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from . import market

NSE = "https://www.nseindia.in/api/"
BSE = "https://api.bseindia.com/BseIndiaAPI/api/"
DOMAINS = {
    "nsearchives.nseindia.in",
    "nsearchives.nseindia.com",
    "archives.nseindia.com",
    "www.nseindia.com",
    "www.nseindia.in",
    "www.bseindia.com",
    "www.bseindia.in",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
 filing_id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, exchange TEXT NOT NULL, type TEXT NOT NULL,
 filed_at TEXT NOT NULL, source_url TEXT NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL,
 extract_json TEXT, UNIQUE(symbol,source_url,sha256));
CREATE TABLE IF NOT EXISTS filing_fetches (id INTEGER PRIMARY KEY, symbol TEXT, as_of TEXT, source TEXT, status TEXT, detail TEXT);
"""


def timestamp(value):
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime(str(value).split(".")[0], fmt)
        except ValueError:
            pass
    return None


def kind(description):
    d = description.lower()
    for label, words in (
        ("transcript", ("transcript",)),
        ("presentation", ("presentation", "investor deck")),
        ("results", ("financial results", "financial result", "quarterly result")),
        ("shareholding", ("shareholding", "pledge")),
    ):
        if any(w in d for w in words):
            return label
    return "announcement"


def collect(root, db, symbol, isin, day, client, lookback_days=100):
    if not re.fullmatch(r"[A-Z0-9&.-]{1,32}", symbol) or symbol in {".", ".."}:
        raise market.MarketError("Invalid filing symbol.")
    before = datetime.combine(day, time(15, 45))
    start = day - timedelta(days=lookback_days)
    dates = {"from_date": start.strftime("%d-%m-%Y"), "to_date": day.strftime("%d-%m-%Y")}
    findings = []

    def index(label, fn):
        try:
            items = fn()
            status, detail = "ok", str(len(items))
            findings.extend(items)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as e:
            status, detail = "unavailable", type(e).__name__
        with db:
            db.execute(
                "INSERT INTO filing_fetches(symbol,as_of,source,status,detail) VALUES(?,?,?,?,?)",
                (symbol, str(day), label, status, detail),
            )

    def get(url, params):
        r = client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    def announcements():
        rows = get(
            NSE + "corporate-announcements", {"symbol": symbol, "index": "equities", **dates}
        )
        return [
            (
                "NSE",
                kind(str(r.get("desc", "")) + " " + str(r.get("attchmntText", ""))),
                r.get("exchdisstime") or r.get("an_dt"),
                r.get("attchmntFile"),
            )
            for r in rows
            if r.get("symbol") == symbol
        ]

    def result_files():
        out = []
        seen = set()
        for page in range(1, 100):
            obj = get(
                NSE + "integrated-filing-results", {"symbol": symbol, "page": page, "size": 100}
            )
            new = [
                r for r in obj["data"] if r.get("seq_Id") not in seen and r.get("symbol") == symbol
            ]
            if not new:
                break
            for r in new:
                seen.add(r.get("seq_Id"))
                for k in ("xbrl", "pdf_attach"):
                    out.append(("NSE", "results", r.get("creation_Date"), r.get(k)))
            if len(seen) >= obj.get("totalCount", len(seen)):
                break
        return out

    def ownership():
        rows = get(
            NSE + "corporate-share-holdings-master",
            {"symbol": symbol, "index": "equities", **dates},
        )
        return [
            ("NSE", "shareholding", r["broadcastDate"], r.get("xbrl"))
            for r in rows
            if r.get("symbol") == symbol
        ]

    def bse():
        master = get(
            BSE + "ListofScripData/w",
            {"Group": "", "Scripcode": "", "industry": "", "segment": "Equity", "status": "Active"},
        )
        code = next(
            (str(r["SCRIP_CD"]) for r in master if str(r.get("ISIN_NUMBER", "")).strip() == isin),
            None,
        )
        if not code:
            raise ValueError("No BSE code for this ISIN")
        out = []
        for page in range(1, 100):
            obj = get(
                BSE + "AnnSubCategoryGetData/w",
                {
                    "pageno": page,
                    "strCat": "-1",
                    "strPrevDate": start.strftime("%Y%m%d"),
                    "strScrip": code,
                    "strSearch": "P",
                    "strToDate": day.strftime("%Y%m%d"),
                    "strType": "C",
                    "subcategory": "-1",
                },
            )
            rows = obj["Table"]
            if not rows:
                break
            for r in rows:
                att = r.get("ATTACHMENTNAME") or ""
                if re.fullmatch(r"[\w.-]+\.pdf", att, re.IGNORECASE):
                    out.append(
                        (
                            "BSE",
                            kind(str(r.get("HEADLINE", "")) + " " + str(r.get("SUBCATNAME", ""))),
                            r.get("DT_TM"),
                            "https://www.bseindia.com/xml-data/corpfiling/AttachLive/" + att,
                        )
                    )
            total = (obj.get("Table1") or [{}])[0].get("ROWCNT")
            if total and page * 50 >= int(total):
                break
        return out

    for source, fn in (
        ("NSE announcements", announcements),
        ("NSE results", result_files),
        ("NSE shareholding", ownership),
        ("BSE announcements", bse),
    ):
        index(source, fn)
    target = root / "filings" / symbol
    for exchange, typ, raw_date, url in findings:
        stamp = timestamp(raw_date)
        if not stamp or not start <= stamp.date() or stamp > before or not url:
            continue
        parsed = urlparse(url)
        ext = Path(parsed.path).suffix.lower()
        if (
            parsed.scheme != "https"
            or parsed.hostname not in DOMAINS
            or ext not in {".pdf", ".xml", ".xbrl"}
        ):
            continue
        cached = db.execute(
            "SELECT path,sha256 FROM filings WHERE symbol=? AND source_url=? ORDER BY filing_id DESC LIMIT 1",
            (symbol, url),
        ).fetchone()
        if (
            cached
            and (root / cached["path"]).is_file()
            and market.sha((root / cached["path"]).read_bytes()) == cached["sha256"]
        ):
            continue
        try:
            response = client.get(url)
            if response.status_code == 404 and exchange == "BSE":
                response = client.get(url.replace("/AttachLive/", "/AttachHis/"))
            response.raise_for_status()
            raw = response.content
            if ext == ".pdf" and not raw.startswith(b"%PDF"):
                raise ValueError("Invalid PDF")
            if ext != ".pdf" and "xbrl" not in ET.fromstring(raw).tag.lower():
                raise ValueError("Invalid XBRL")
            digest = market.sha(raw)
            dest = target / f"{stamp.date()}_{typ}_{digest[:12]}{ext}"
            with market.staging(target) as tmp:
                (tmp / "download").write_bytes(raw)
                (tmp / "download").replace(dest)
            with db:
                db.execute(
                    "INSERT OR IGNORE INTO filings(symbol,exchange,type,filed_at,source_url,sha256,path) VALUES(?,?,?,?,?,?,?)",
                    (
                        symbol,
                        exchange,
                        typ,
                        stamp.isoformat(),
                        str(response.url),
                        digest,
                        str(dest.relative_to(root)),
                    ),
                )
        except (OSError, httpx.HTTPError, ValueError, ET.ParseError) as exc:
            with db:
                db.execute(
                    "INSERT INTO filing_fetches(symbol,as_of,source,status,detail) VALUES(?,?,?,?,?)",
                    (symbol, str(day), url, "unavailable", type(exc).__name__),
                )
    return [
        dict(r)
        for r in db.execute(
            "SELECT * FROM filings WHERE symbol=? AND filed_at BETWEEN ? AND ? ORDER BY filed_at DESC, filing_id",
            (symbol, str(start), before.isoformat()),
        )
    ]


def sections(root, record):
    path = root / record["path"]
    if market.sha(path.read_bytes()) != record["sha256"]:
        raise market.MarketError("Filing checksum mismatch")
    if path.suffix.lower() == ".pdf":
        raw = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True
        ).stdout.decode(errors="replace")
        for page, content in enumerate(raw.split("\f"), 1):
            for start in range(0, len(content), 10000):
                if content[start : start + 10000].strip():
                    yield f"page {page}", content[start : start + 10000]
    else:
        document = ET.parse(path)
        facts = []
        for node in document.iter():
            if node.get("contextRef") and node.text:
                label = node.tag.split("}")[-1]
                facts.append(
                    f"{label} [{node.get('contextRef')}, {node.get('unitRef', '')}] {node.text.strip()}"
                )
            elif node.tag.endswith("}context"):
                facts.append(f"context {node.get('id')} " + " ".join(node.itertext()).strip())
        part = []
        length = 0
        for fact in facts:
            if part and length + len(fact) > 10000:
                yield "XBRL facts", "\n".join(part)
                part = []
                length = 0
            part.append(fact)
            length += len(fact)
        if part:
            yield "XBRL facts", "\n".join(part)
