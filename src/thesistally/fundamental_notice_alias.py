"""Find results under dated successor ticker spellings across the verified NSE market bulletin."""

from __future__ import annotations

import gzip
import json
from collections import Counter, defaultdict

from .fundamental_notice_fallback import notices
from .fundamental_notice_language import accession, classify, read_period, sha
from .fundamental_nse import END, REPO, ROOT, members, published_at, write_json


def selected(row, valid_isins, receipt):
    # Retain the actual announced ISIN even when it differs or is missing. These
    # records can flag a possible previous release, never silently reassign it.
    clock = published_at(row.get("exchdisstime")) or published_at(row.get("an_dt"))
    if not clock or clock.date() > END:
        return None
    language = classify(row)
    base = notices([(row, clock.date())], receipt["url"])
    if not language and not base:
        return None
    isin = row.get("sm_isin")
    return dict(
        symbol=row["symbol"],
        isin=isin,
        isin_scope="exact_plan_isin"
        if isin in valid_isins
        else "other_reported_isin"
        if isin
        else "unspecified",
        announced_at=clock.isoformat() + "+05:30",
        description=row.get("desc") or "",
        text=row.get("attchmntText") or "",
        source_url=None if row.get("attchmntFile") in (None, "", "-") else row["attchmntFile"],
        sequence=row.get("seq_id"),
        catalogue_url=receipt["url"],
        catalogue_sha256=receipt["sha256"],
        accession_sha256=accession(row),
        **(language or {}),
    )


def main():
    plan = ROOT / "dated-alias-attachment-plan.json"
    historical = members()
    symbols = {r["symbol"] for r in historical}
    isins = {r["isin"] for r in historical}
    aliases = defaultdict(set)
    for r in json.loads(plan.read_bytes()):
        if r["symbol"] not in symbols and r["isin"] in isins:
            aliases[r["symbol"]].add(r["isin"])
    market = ROOT / "notice-market-manifest.json"
    market_meta = json.loads(market.read_bytes())
    expected = {b["url"]: b for b in market_meta["blocks"]}
    blocks = {}
    results = []
    seen = Counter()
    scope = defaultdict(Counter)
    for s in json.loads((ROOT / "notice-market-sources.json").read_bytes()):
        if s["kind"] != "period":
            continue
        if s["url"] not in expected or s["url"] in blocks:
            raise ValueError("Unrecognized or duplicate all-market period")
        zipped, rows = read_period(s)
        if len(rows) != expected[s["url"]]["total"]:
            raise ValueError("Market-period row count differs from the coverage certificate")
        blocks[s["url"]] = len(rows)
        inline = False
        for r in rows:
            if r.get("symbol") not in aliases:
                continue
            symbol = r["symbol"]
            seen[symbol] += 1
            n = selected(r, aliases[symbol], s)
            if n:
                results.append(n)
                scope[symbol][n["isin_scope"]] += 1
                inline |= (
                    n.get("release_language") == "legacy_results_summary"
                    and n["declared_duration"] == "quarter"
                )
        if inline:
            path = ROOT / "notice-language-sources" / (s["sha256"] + ".json.gz")
            if not path.exists():
                path.write_bytes(zipped)
            if sha(gzip.decompress(path.read_bytes())) != s["sha256"]:
                raise ValueError("Legacy summary archive integrity mismatch")
    if blocks.keys() != expected.keys():
        raise ValueError("An all-market period is missing")
    results.sort(key=lambda r: (r["symbol"], r["announced_at"], r["accession_sha256"]))
    path = ROOT / "notice-alias-rows.json"
    write_json(path, results)
    write_json(
        ROOT / "notice-alias-manifest.json",
        {
            "segments": market_meta["segments"],
            "symbols": sorted(aliases),
            "rows_sha256": sha(path.read_bytes()),
            "market_manifest_sha256": sha(market.read_bytes()),
            "market_sources_sha256": sha((ROOT / "notice-market-sources.json").read_bytes()),
            "membership_sha256": sha(
                (REPO / "data/universes/nifty200_membership.csv").read_bytes()
            ),
            "alias_plan_sha256": sha(plan.read_bytes()),
            "period_blocks": len(blocks),
            "raw_all_market_rows": sum(blocks.values()),
            "selected": len(results),
            "symbol_counts": {
                s: {
                    "expected_isins": sorted(aliases[s]),
                    "all_announcements": seen[s],
                    "selected": sum(scope[s].values()),
                    "isin_scope": dict(scope[s]),
                }
                for s in sorted(aliases)
            },
        },
    )


if __name__ == "__main__":
    main()
