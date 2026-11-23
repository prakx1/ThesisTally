"""Recover official issuer announcements using a shorter archive and a second date range."""

from __future__ import annotations

import calendar
import gzip
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urlencode

import httpx

from .fundamental_notices import BOARD, NEGATIVE, RESULT
from .fundamental_nse import BASE, CAT, END, HEAD, ROOT, catalogue, digest, published_at, write_json


def source_evidence(url, count, kind, **metadata):
    key = digest(url.encode())
    raw = gzip.decompress((CAT / (key + ".json.gz")).read_bytes())
    return {"url": url, "sha256": digest(raw), "bytes": len(raw), "kind": kind, **count, **metadata}


def url(symbol, begin, end):
    params = {
        "index": "equities",
        "symbol": symbol,
        "from_date": begin.strftime("%d-%m-%Y"),
        "to_date": end.strftime("%d-%m-%Y"),
    }
    return BASE + "corporate-announcements?" + urlencode(params)


def identity(r):
    return (str(r.get("seq_id") or ""), r.get("an_dt"), r.get("attchmntFile") or "", r.get("desc"))


def dated(rows, symbol, start, end):
    if not isinstance(rows, list):
        raise TypeError("Exchange did not return an announcement list")
    selected = []
    for r in rows:
        if r.get("symbol") != symbol:
            raise ValueError("Issuer filter ignored")
        stamp = published_at(r.get("an_dt"))
        if not stamp or not start <= stamp.date() <= end:
            raise ValueError("Returned records have unverified dates or outside the query scope")
        selected.append((r, stamp.date()))
    return selected


def notices(rows, source):
    out = []
    for r, _ in rows:
        desc, body = str(r.get("desc") or ""), str(r.get("attchmntText") or "")
        if NEGATIVE.search(desc) or not (
            RESULT.search(desc) or BOARD.search(desc) or RESULT.search(body)
        ):
            continue
        stamp = published_at(r.get("exchdisstime")) or published_at(r.get("an_dt"))
        if stamp and stamp.date() <= END:
            out.append(
                {
                    "symbol": r["symbol"],
                    "isin": r.get("sm_isin"),
                    "announced_at": stamp.isoformat() + "+05:30",
                    "description": desc,
                    "text": body,
                    "source_url": r.get("attchmntFile"),
                    "sequence": r.get("seq_id"),
                    "catalogue_url": source,
                }
            )
    return out


def corroborate(whole, month, start, end):
    expected = {identity(r) for r, d in whole if start <= d <= end}
    observed = {identity(r) for r, _ in month}
    return bool(expected and expected == observed)


def recover(client, symbol):
    errors = []
    for startyear in (2014, 2013, 2012, 2015):
        begin = date(startyear, 1, 1)
        source = url(symbol, begin, END)
        try:
            whole = dated(catalogue(client, source), symbol, begin, END)
            if not whole:
                raise ValueError("No dated issuer records")
            # Cross-check a nonempty period within the long response against a narrow
            # official query, including timestamps, category and attachment identity.
            observed = Counter((d.year, d.month) for _, d in whole)
            for y, m in [ym for ym, _ in observed.most_common(3)]:
                a, b = date(y, m, 1), min(date(y, m, calendar.monthrange(y, m)[1]), END)
                cross = url(symbol, a, b)
                try:
                    month = dated(catalogue(client, cross), symbol, a, b)
                except (TypeError, ValueError, httpx.HTTPError) as exc:
                    errors.append({"url": cross, "error": str(exc)[:180]})
                    continue
                if corroborate(whole, month, a, b):
                    result = notices(whole, source)
                    return (
                        {
                            "symbol": symbol,
                            "url": source,
                            "from_date": begin.isoformat(),
                            "to_date": END.isoformat(),
                            "total": len(whole),
                            "candidates": len(result),
                            "crosscheck_url": cross,
                            "crosscheck_rows": len(month),
                        },
                        result,
                        errors,
                    )
                errors.append({"url": cross, "error": "Nonmatching overlapping query"})
            raise ValueError("Could not corroborate a nonempty overlap")
        except (TypeError, ValueError, httpx.HTTPError) as exc:
            errors.append({"url": source, "error": str(exc)[:180]})
    return {"symbol": symbol, "error": "No corroborated public issuer history"}, [], errors


def main():
    original = json.loads((ROOT / "notice-manifest.json").read_bytes())
    todo = sorted(r["symbol"] for r in original if r.get("error") or not r.get("total"))
    output = ROOT / "supplemental-notices"
    output.mkdir(exist_ok=True)

    def run(symbol):
        marker = output / (symbol + ".json")
        if marker.exists():
            return json.loads(marker.read_bytes())
        manifest, rows, errors = recover(client, symbol)
        obj = {"manifest": manifest, "rows": rows, "errors": errors}
        write_json(marker, obj)
        return obj

    manifests = []
    rows = []
    with (
        httpx.Client(
            headers=HEAD, timeout=httpx.Timeout(30, connect=8), follow_redirects=True
        ) as client,
        ThreadPoolExecutor(6) as pool,
    ):
        for i, obj in enumerate(pool.map(run, todo), 1):
            manifests.append(obj["manifest"])
            rows.extend(obj["rows"])
            if i % 20 == 0:
                print(
                    f"{i}/{len(todo)} issuer histories examined; {len(rows)} potential results",
                    flush=True,
                )
    write_json(ROOT / "notice-fallback-manifest.json", manifests)
    write_json(ROOT / "notice-fallback-rows.json", rows)
    checked = []
    for item in manifests:
        if item.get("total"):
            checked.append(
                source_evidence(
                    item["url"], {"count": item["total"]}, "long_history", symbol=item["symbol"]
                )
            )
            checked.append(
                source_evidence(
                    item["crosscheck_url"],
                    {"count": item["crosscheck_rows"]},
                    "overlap",
                    symbol=item["symbol"],
                )
            )
    write_json(ROOT / "notice-fallback-sources.json", checked)
    print(
        f"{sum('error' not in m for m in manifests)} recovered out of {len(manifests)}", flush=True
    )


if __name__ == "__main__":
    from .fundamental_sources import run

    run("issuer-fallback")
