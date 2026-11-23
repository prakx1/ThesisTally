"""Find earlier issuer results releases on the official NSE announcements tape."""

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import httpx

from .fundamental_nse import (
    BASE,
    CAT,
    END,
    HEAD,
    ROOT,
    START,
    catalogue,
    digest,
    members,
    published_at,
    write_json,
)

RESULT = re.compile(
    r"financial result|quarterly result|audited results|unaudited results", re.IGNORECASE
)
BOARD = re.compile(r"outcome of board meeting|board meeting outcome", re.IGNORECASE)
NEGATIVE = re.compile(
    r"newspaper|clarification|prior intimation|post.?facto|notice of board meeting|proposed to consider",
    re.IGNORECASE,
)


def collect(client, symbol):
    url = (
        BASE
        + "corporate-announcements?"
        + urlencode(
            {
                "index": "equities",
                "symbol": symbol,
                "from_date": START.strftime("%d-%m-%Y"),
                "to_date": END.strftime("%d-%m-%Y"),
            }
        )
    )
    try:
        try:
            rows = catalogue(client, url)
        except httpx.HTTPError:
            # A separately captured catalogue can have a shorter, known archive interval.
            shorter = url.replace("from_date=01-01-2011", "from_date=01-01-2013")
            if not (CAT / (digest(shorter.encode()) + ".json.gz")).is_file():
                raise
            url = shorter
            rows = catalogue(client, url)
        if not isinstance(rows, list):
            raise TypeError("Invalid announcement history")
        records = []
        for r in rows:
            if r.get("symbol") != symbol:
                continue
            desc = str(r.get("desc") or "")
            body = str(r.get("attchmntText") or "")
            if NEGATIVE.search(desc) or not (
                RESULT.search(desc) or BOARD.search(desc) or RESULT.search(body)
            ):
                continue
            stamp = published_at(r.get("exchdisstime")) or published_at(r.get("an_dt"))
            if not stamp or stamp.date() > END:
                continue
            records.append(
                {
                    "symbol": symbol,
                    "isin": r.get("sm_isin"),
                    "announced_at": stamp.isoformat() + "+05:30",
                    "description": desc,
                    "text": body,
                    "source_url": r.get("attchmntFile"),
                    "sequence": r.get("seq_id"),
                    "issuer_name": r.get("sm_name"),
                    "catalogue_url": url,
                }
            )
        return (
            records,
            {
                "url": url,
                "symbol": symbol,
                "total": len(rows),
                "candidates": len(records),
                "from_date": "2013-01-01" if "from_date=01-01-2013" in url else START.isoformat(),
            },
            None,
        )
    except (OSError, KeyError, TypeError, ValueError, httpx.HTTPError) as exc:
        return [], {"url": url, "symbol": symbol, "error": str(exc)[:250]}, str(exc)[:250]


def main():
    manifest = []
    records = []
    symbols = sorted({m["symbol"] for m in members()})
    with (
        httpx.Client(
            headers=HEAD, timeout=httpx.Timeout(65, connect=10), follow_redirects=True
        ) as client,
        ThreadPoolExecutor(6) as pool,
    ):
        for notices, receipt, error in pool.map(lambda symbol: collect(client, symbol), symbols):
            manifest.append(receipt)
            records.extend(notices)
    write_json(ROOT / "notice-census.json", records)
    write_json(ROOT / "notice-manifest.json", manifest)
    print(
        len(records),
        "potential earnings releases and board outcomes across",
        len(manifest),
        "symbols",
    )


if __name__ == "__main__":
    from .fundamental_sources import run

    run("notices")
