"""Bind manually audited earlier earnings packets to immutable exchange bulletin accessions."""

import gzip
import json

from .fundamental_notice_language import accession, read_period, sha
from .fundamental_nse import ROOT, published_at, write_json

CASES = {
    "GSKCONS": [
        (
            "2015-03-31",
            "NSEQ4AR15_05082015192503_.zip",
            "95407343133e52a94296952f9b8ea44b13498975ad3d5d1eea7c739d46e45247",
            None,
            "Dividend packet included six-page March results; first-page letter, March quarterly columns on page 4.",
        ),
    ],
    "NATCOPHARM": [
        (
            "2015-09-30",
            "SEletter_13112015160232_186.zip",
            "d7e2d7095cb90702c65cb45780750ed3a8bc44e43f964029dd1cb1e5abdb4ddc",
            None,
            "Record-date notice states that standalone and consolidated quarterly results were enclosed. The archive contains only the one-page board letter.",
        ),
        (
            "2015-09-30",
            "PressRelease7_13112015164455_207.zip",
            "f4b790eb9fdc290d7fb9c154302b27cd435f241252008a1238ae6e31bdabd386",
            "consolidated",
            "Issuer press release reports consolidated revenues and consolidated profit after tax for September quarter.",
        ),
    ],
}


def main():
    rows = []
    sources = ROOT / "notice-market-sources.json"
    for receipt in json.loads(sources.read_bytes()):
        if receipt["kind"] != "period" or not any(
            v in receipt["url"] for v in ("05-2015", "11-2015")
        ):
            continue
        _, bulletin = read_period(receipt)
        for r in bulletin:
            symbol = r.get("symbol")
            if symbol not in CASES:
                continue
            url = r.get("attchmntFile") or ""
            for period, filename, source_sha, basis, detail in CASES[symbol]:
                if not url.endswith("/" + filename):
                    continue
                raw = gzip.decompress(
                    (ROOT / "notice-case-originals" / (source_sha + ".gz")).read_bytes()
                )
                if sha(raw) != source_sha:
                    raise ValueError("Earlier issuer attachment fingerprint differs")
                stamp = published_at(r.get("exchdisstime")) or published_at(r["an_dt"])
                rows.append(
                    {
                        "symbol": symbol,
                        "isin": r["sm_isin"],
                        "announced_at": stamp.isoformat() + "+05:30",
                        "description": r["desc"],
                        "text": r["attchmntText"],
                        "source_url": url,
                        "retrieved_url": url.replace(
                            "nsearchives.nseindia.in", "archives.nseindia.com"
                        ),
                        "sequence": r.get("seq_id"),
                        "catalogue_url": receipt["url"],
                        "catalogue_sha256": receipt["sha256"],
                        "accession_sha256": accession(r),
                        "declared_period_end": period,
                        "declared_basis": basis,
                        "release_language": "audited_original_case",
                        "attachment_sha256": source_sha,
                        "case_evidence": detail,
                    }
                )
    if len({r["source_url"] for r in rows}) != sum(len(v) for v in CASES.values()):
        raise ValueError("A reviewed original is absent from the signed market bulletin")
    rows.sort(key=lambda r: (r["symbol"], r["announced_at"]))
    path = ROOT / "notice-case-rows.json"
    write_json(path, rows)
    write_json(
        ROOT / "notice-case-manifest.json",
        {
            "rows_sha256": sha(path.read_bytes()),
            "market_sources_sha256": sha(sources.read_bytes()),
            "selected": len(rows),
            "method": "All-category exchange-bulletin and original-document audit of the three eligible legacy HTML basis records with no earlier indexed release.",
            "sources": [
                {
                    k: r[k]
                    for k in (
                        "symbol",
                        "announced_at",
                        "source_url",
                        "retrieved_url",
                        "attachment_sha256",
                        "catalogue_sha256",
                        "accession_sha256",
                    )
                }
                for r in rows
            ],
        },
    )


if __name__ == "__main__":
    main()
