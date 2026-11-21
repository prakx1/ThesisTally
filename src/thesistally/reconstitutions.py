"""Dated NSE index weights and published index maintenance notices."""

import html
import json
import re
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import pandas as pd

from . import market

REGISTER = "https://www.niftyindices.com/media"
TARGET = re.compile(
    r"\b(?:CNX|NIFTY)\s*200\b(?!\s+(?:Quality|Momentum|Alpha|Value|Growth))", re.IGNORECASE
)


def notice_register(raw, start, end):
    notices = []
    for item in re.findall(r"<li\b[^>]*data-publishdate=.*?</li>", raw, re.IGNORECASE | re.DOTALL):
        stamp = re.search(r"<span>\s*(.*?)\s*</span>", item, re.DOTALL)
        link = re.search(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', item, re.DOTALL | re.IGNORECASE)
        if not stamp or not link:
            continue
        issued = pd.to_datetime(stamp[1], format="mixed").date()
        if start <= issued <= end:
            title = " ".join(html.unescape(link[2]).split())
            url = urljoin(REGISTER, html.unescape(link[1].strip()))
            if urlsplit(url).hostname in {
                "www.niftyindices.com",
                "niftyindices.com",
                "archives.nseindia.com",
                "nsearchives.nseindia.com",
            }:
                notices.append((issued, title, url))
    return notices


def fetch_notices(root, start, end):
    ref = root / "sources" / "reference"
    raw = (ref / "press-release-register.html").read_text()
    notices = notice_register(raw, start, end)
    folder = ref / "notices"
    folder.mkdir(parents=True, exist_ok=True)

    def one(notice):
        day, title, url = notice
        key = market.sha(url.encode())[:16]
        dest = folder / (key + ".json")
        if dest.exists():
            return json.loads(dest.read_text())
        try:
            r = client.get(url)
            r.raise_for_status()
            if not r.content.startswith(b"%PDF"):
                raise ValueError("Expected PDF")
            body = subprocess.run(
                ["pdftotext", "-layout", "-", "-"], input=r.content, capture_output=True, check=True
            ).stdout.decode()
            if len(body.strip()) < 60:
                raise ValueError("PDF requires transcription")
            related = bool(TARGET.search(body))
            info = {
                "issued": str(day),
                "title": title,
                "url": url,
                "sha256": market.sha(r.content),
                "mentions_index": related,
            }
            with market.staging(folder) as temp:
                if related:
                    (temp / "notice.pdf").write_bytes(r.content)
                    (temp / "notice.pdf").replace(folder / (key + ".pdf"))
                    (temp / "notice.txt").write_text(body)
                    (temp / "notice.txt").replace(folder / (key + ".txt"))
                (temp / "meta").write_text(json.dumps(info))
                (temp / "meta").replace(dest)
            return info
        except (httpx.HTTPError, ValueError, subprocess.SubprocessError) as exc:
            return {"issued": str(day), "title": title, "url": url, "error": type(exc).__name__}

    with (
        httpx.Client(
            timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
        ) as client,
        ThreadPoolExecutor(max_workers=5) as pool,
    ):
        rows = list(pool.map(one, notices))
    with market.staging(ref) as temp:
        (temp / "inventory").write_text(json.dumps(rows, indent=2))
        (temp / "inventory").replace(ref / "notice-inventory.json")
    return rows


def weight_anchors(root):
    """Read the symbol and industry printed on the dated Nifty 200 PDF itself."""
    folder = root / "sources" / "reference"
    by_date = {}
    for path in sorted(folder.glob("weights-*.zip")):
        with zipfile.ZipFile(path) as z:
            names = [
                n
                for n in z.namelist()
                if re.search(r"(?:^|/)(?:cnx|nifty)[ _-]*200[_ -].*\.pdf$", n, re.IGNORECASE)
            ]
            if len(names) != 1:
                continue
            data = z.read(names[0])
        body = subprocess.run(
            ["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, check=True
        ).stdout.decode()
        stamp = re.search(
            r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}",
            body,
            re.IGNORECASE,
        )
        if not stamp:
            continue
        day = pd.Timestamp(stamp[0]).date()
        members = {}
        for line in body.splitlines():
            match = re.match(r"^\s{0,7}([A-Z0-9][A-Z0-9&.\-]+)\s+(.*)$", line)
            if not match:
                continue
            cols = re.split(r"\s{2,}", match[2].strip())
            price = next((i for i, x in enumerate(cols) if re.fullmatch(r"[\d,]+\.\d+", x)), None)
            if price is not None:
                members[match[1]] = cols[price - 1] if price else "Unknown"
        if len(members) >= 200:
            by_date[day] = (
                members,
                {"pdf": names[0], "sha256": market.sha(data), "archive": path.name},
            )
    return by_date


def rebalance_events(folder):
    """Parse symbol tables under the exact broad-index heading in change notices."""
    events = []
    for path in sorted(folder.glob("*.txt")):
        text = path.read_text()
        meta = json.loads(path.with_suffix(".json").read_text())
        effective = re.search(
            r"(?:effective\s+(?:from|on)|w\.e\.f\.?)\s*[:,]?\s*((?:[A-Za-z]+\s+[0-9]{1,2},?\s+[0-9]{4})|(?:[0-9]{1,2}\s+[A-Za-z]+,?\s+[0-9]{4}))",
            text,
            re.IGNORECASE,
        )
        if not effective:
            continue
        try:
            day = pd.Timestamp(effective[1]).date()
        except ValueError:
            continue
        lines = text.replace("\f", "\n").splitlines()
        for i, line in enumerate(lines):
            if not re.fullmatch(
                r"\s*(?:\(?(?:\d+|[a-z])[).:]?\s*)?(?:CNX|NIFTY)\s*200(?:\s+index)?\s*",
                line,
                re.IGNORECASE,
            ):
                continue
            added = set()
            removed = set()
            side = None
            for row in lines[i + 1 :]:
                if re.match(r"^\s*\(?(?:\d+|[a-z])[).:]\s*(?:Nifty|CNX)", row, re.IGNORECASE):
                    break
                if re.search(
                    r"being excluded|being removed|following exclusions", row, re.IGNORECASE
                ):
                    side = removed
                elif re.search(
                    r"being included|being added|following inclusions", row, re.IGNORECASE
                ):
                    side = added
                elif side is not None and (
                    symbol := re.match(r"^\s*\d+\s+.+?\s{2,}([A-Z0-9][A-Z0-9&.\-]+)\s*$", row)
                ):
                    side.add(symbol[1])
            if added or removed:
                events.append((day, added, removed, meta["url"]))
    review = Path(__file__).with_name("revisions") / "constituent-notices.json"
    for row in json.loads(review.read_text()):
        path = folder / (row["notice"] + ".json")
        if path.exists():
            meta = json.loads(path.read_text())
            pdf = path.with_suffix(".pdf")
            if pdf.exists() and market.sha(pdf.read_bytes()) == meta["sha256"]:
                events.append(
                    (
                        date.fromisoformat(row["effective"]),
                        set(row["add"]),
                        set(row["remove"]),
                        meta["url"],
                    )
                )
    return sorted(events, key=lambda x: x[0])
