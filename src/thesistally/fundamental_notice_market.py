"""Reconstruct the NSE market bulletin month by month, including retired symbols."""

import calendar
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from urllib.parse import urlencode

import httpx

from .fundamental_notice_fallback import identity, notices, source_evidence
from .fundamental_nse import (
    BASE,
    END,
    HEAD,
    ROOT,
    START,
    catalogue,
    members,
    published_at,
    write_json,
)


def market_url(a, b):
    return (
        BASE
        + "corporate-announcements?"
        + urlencode(
            {
                "index": "equities",
                "from_date": a.strftime("%d-%m-%Y"),
                "to_date": b.strftime("%d-%m-%Y"),
            }
        )
    )


def within_range(rows, a, b):
    if not isinstance(rows, list):
        raise TypeError("Unexpected response to dated exchange bulletin")
    out = []
    for r in rows:
        ts = published_at(r.get("an_dt"))
        if not ts or not a <= ts.date() <= b:
            raise ValueError("Publication falls outside dated bulletin")
        out.append((r, ts.date()))
    return out


def overlap(whole, sub, a, b):
    def key(r):
        return (r.get("symbol"), *identity(r))

    expected = {key(r) for r, d in whole if a <= d <= b}
    actual = {key(r) for r, _ in sub}
    return bool(expected and expected == actual)


def get_block(client, a, b, wanted):
    url = market_url(a, b)
    whole = within_range(catalogue(client, url), a, b)
    edges = [
        (a, min(a + timedelta(days=4), b)),
        (min(a + timedelta(days=1), b), min(a + timedelta(days=6), b)),
    ]
    failures = []
    for aa, bb in edges:
        cross = market_url(aa, bb)
        try:
            check = within_range(catalogue(client, cross), aa, bb)
            if not overlap(whole, check, aa, bb):
                raise ValueError("Archive differs from early-period overlap")
        except (TypeError, ValueError, httpx.HTTPError) as exc:
            failures.append([cross, str(exc)[:160]])
            continue
        selected = notices([(r, d) for r, d in whole if r.get("symbol") in wanted], url)
        return {
            "start": a.isoformat(),
            "end": b.isoformat(),
            "url": url,
            "total": len(whole),
            "cross": cross,
            "overlap": len(check),
            "selected": len(selected),
            "rows": selected,
        }
    raise ValueError("No corroborated early-interval overlap " + str(failures))


def run_month(client, a, b, wanted):
    directory = ROOT / "market-bulletins"
    path = directory / f"{a:%Y-%m}.json"
    if path.exists():
        return json.loads(path.read_bytes())
    error = []
    try:
        blocks = [get_block(client, a, b, wanted)]
    except (TypeError, ValueError, httpx.HTTPError) as exc:
        error.append([market_url(a, b), str(exc)[:300]])
        blocks = []
        for lo, hi in [(a, a.replace(day=15)), (a.replace(day=16), b)]:
            if lo > hi:
                continue
            try:
                blocks.append(get_block(client, lo, hi, wanted))
            except (TypeError, ValueError, httpx.HTTPError) as smaller:
                error.append([market_url(lo, hi), str(smaller)[:300]])
    obj = {"start": str(a), "end": str(b), "blocks": blocks, "failed_requests": error}
    write_json(path, obj)
    return obj


def intervals(blocks):
    segments = []
    for block in sorted(blocks, key=lambda b: b["start"]):
        a, b = block["start"], block["end"]
        if segments and a == (date.fromisoformat(segments[-1][1]) + timedelta(days=1)).isoformat():
            segments[-1][1] = b
        else:
            segments.append([a, b])
    return segments


def main():
    directory = ROOT / "market-bulletins"
    directory.mkdir(exist_ok=True)
    wanted = {r["symbol"] for r in members()}
    months = []
    for year in range(START.year, END.year + 1):
        for month in range(1, 13):
            a = date(year, month, 1)
            if a <= END:
                months.append((a, min(END, date(year, month, calendar.monthrange(year, month)[1]))))
    meta = []
    rows = []
    with (
        httpx.Client(
            headers=HEAD, timeout=httpx.Timeout(65, connect=10), follow_redirects=True
        ) as client,
        ThreadPoolExecutor(6) as pool,
    ):
        for i, obj in enumerate(pool.map(lambda ab: run_month(client, *ab, wanted), months), 1):
            for block in obj["blocks"]:
                rows.extend(block["rows"])
                meta.append({k: v for k, v in block.items() if k != "rows"})
            if i % 15 == 0:
                print(i, "months,", len(rows), "eligible issuer notices", flush=True)
    write_json(
        ROOT / "notice-market-manifest.json",
        {
            "segments": intervals(meta),
            "blocks": meta,
            "symbols": sorted(wanted),
            "expected_months": len(months),
        },
    )
    write_json(ROOT / "notice-market-rows.json", rows)
    checked = []
    for block in meta:
        checked.append(source_evidence(block["url"], {"rows": block["total"]}, "period"))
        checked.append(source_evidence(block["cross"], {"rows": block["overlap"]}, "overlap"))
    write_json(ROOT / "notice-market-sources.json", checked)
    print("segments", intervals(meta), "rows", len(rows), flush=True)


if __name__ == "__main__":
    from .fundamental_sources import run

    run("market-fallback")
