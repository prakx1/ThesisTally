"""Find the first NSE dissemination of the identical issuer attachment across announcement categories."""

from __future__ import annotations

import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from .fundamental_notice_language import accession, read_period, sha
from .fundamental_nse import ROOT, published_at, write_json


def archive_key(url):
    if not url:
        return None
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or p.hostname
        not in {"nsearchives.nseindia.in", "nsearchives.nseindia.com", "archives.nseindia.com"}
        or not p.path.startswith(("/corporate/", "/content/corporate/"))
    ):
        return None
    return (p.path, p.query)


def before(first, known):
    diff = datetime.fromisoformat(known) - datetime.fromisoformat(first)
    return timedelta(0) < diff <= timedelta(days=7)


def main():
    inputs = ("notice-market-rows.json", "notice-language-rows.json", "notice-market-sources.json")
    known = defaultdict(list)
    for name in inputs[:2]:
        for r in json.loads((ROOT / name).read_bytes()):
            href = archive_key(r["source_url"])
            if href:
                known[r["symbol"], href].append(r)
    known = {k: min(v, key=lambda r: r["announced_at"]) for k, v in known.items()}
    originals = {}
    early = []
    public_records = {}
    for receipt in json.loads((ROOT / inputs[2]).read_bytes()):
        if receipt["kind"] != "period":
            continue
        _, rows = read_period(receipt)
        for r in rows:
            ref = known.get((r.get("symbol"), archive_key(r.get("attchmntFile"))))
            if not ref or r.get("sm_isin") != ref.get("isin"):
                continue
            clock = published_at(r.get("exchdisstime")) or published_at(r.get("an_dt"))
            if not clock:
                continue
            stamp = clock.isoformat() + "+05:30"
            key = r["symbol"], archive_key(r["attchmntFile"])
            meta = {
                "catalogue_sha256": receipt["sha256"],
                "catalogue_url": receipt["url"],
                "accession_sha256": accession(r),
                "sequence": r.get("seq_id"),
                "description": r["desc"],
                "source_url": r["attchmntFile"],
                "announced_at": stamp,
            }
            if stamp == ref["announced_at"] and r["desc"] == ref["description"]:
                originals[key] = (meta, r)
            elif before(stamp, ref["announced_at"]):
                early.append(
                    (
                        key,
                        dict(
                            meta,
                            symbol=r["symbol"],
                            isin=r["sm_isin"],
                            text=r.get("attchmntText") or "",
                            release_language="identical_earnings_attachment",
                        ),
                        r,
                    )
                )
    out = []
    for key, item, raw in early:
        if key in originals:
            known_meta, known_raw = originals[key]
            out.append(dict(item, later_matching_notice=known_meta))
            public_records[item["accession_sha256"]] = raw
            public_records[known_meta["accession_sha256"]] = known_raw
    out.sort(key=lambda r: (r["announced_at"], r["symbol"], r["accession_sha256"]))
    write_json(ROOT / "notice-language-identical-rows.json", out)
    raw = json.dumps(
        public_records, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    path = ROOT / "notice-language-identical-records.json.gz"
    path.write_bytes(gzip.compress(raw, mtime=0))
    write_json(
        ROOT / "notice-language-identical-manifest.json",
        {
            "inputs": {name: sha((ROOT / name).read_bytes()) for name in inputs},
            "records_sha256": sha(raw),
            "rows_sha256": sha((ROOT / "notice-language-identical-rows.json").read_bytes()),
            "recovered": len(out),
            "categories": dict(Counter(x["description"] for x in out)),
            "cross_midnight": sum(
                x["announced_at"][:10] != x["later_matching_notice"]["announced_at"][:10]
                for x in out
            ),
            "cross_same_day_close": sum(
                x["announced_at"][:10] == x["later_matching_notice"]["announced_at"][:10]
                and x["announced_at"][11:19]
                <= "15:30:00"
                < x["later_matching_notice"]["announced_at"][11:19]
                for x in out
            ),
        },
    )


if __name__ == "__main__":
    main()
