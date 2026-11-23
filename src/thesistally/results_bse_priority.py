"""Resolve the first exchange XBRL in already-covered quarters capable of changing the gate."""

import csv
import json
import os
import tempfile
from datetime import datetime

from . import results_bse_census as c
from .discovery_registry import digest

YEARS = {2025, 2026}


def progress_file(path, raw):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
        temporary = f.name
    os.replace(temporary, path)


def import_inline():
    from .results_bse_inline import import_receipts

    with c.connection(c.ROOT) as db:
        import_receipts(db)


def covered_rows():
    with (c.NSE / "stock-quarters.csv").open() as f:
        return [
            r for r in csv.DictReader(f) if r["covered"] == "True" and int(r["season"]) in c.SEASONS
        ]


def merge_catalogues(archive, integrated):
    from .results_bse_inline import CATALOGUE, _accession_equal, integrated_editions

    merged = {}
    codes = set()
    for code, (archive_sha, legacy) in archive.items():
        rows = {k: list(v) for k, v in legacy.items()}
        if value := integrated.get(CATALOGUE + code):
            codes.add(code)
            source, raw = value
            for key, editions in integrated_editions(raw, code).items():
                for d in editions:
                    later_clock = any(
                        r["at"]
                        and d["at"]
                        and c.stamp(r["at"]) > c.stamp(d["at"])
                        and _accession_equal(r["url"], d["url"], code)
                        for r in legacy.get(key, ())
                    )
                    rows.setdefault(key, []).append(
                        {**d, "publisher_sha": source, "requires_dated_bytes": later_clock}
                    )
        merged[code] = (archive_sha, rows)
    return merged, codes


def classify(case, catalog, routing, nse, sessions, integrated_codes=None):
    codes = routing[case["isin"]]
    all_catalogs = bool(codes) and all(x in catalog for x in codes)
    if integrated_codes is not None and int(case["season"]) in YEARS:
        all_catalogs = all_catalogs and all(x in integrated_codes for x in codes)
    confirmed = []
    unresolved = {b: [] for b in c.BASIS}
    for b in c.BASIS:
        ns = nse.get((case["isin"], case["period_end"], b))
        if not ns:
            continue
        nse_at = ns[0]
        nse_usable = c.first_session(nse_at, sessions)
        valid_nse = all_catalogs and ns[3] and nse_usable and nse_usable <= case["window_end"]
        for code in sorted(codes):
            if code not in catalog:
                continue
            sha, cats = catalog[code]
            rows = cats.get((case["period_end"], b), [])
            if any(i["at"] is None or datetime.fromisoformat(i["at"]) <= nse_at for i in rows):
                valid_nse = False
            orig, _ = c.first_editions(rows, case["window_end"], sessions)
            for doc, usable in orig:
                why, tied = c.chronology(doc, ns)
                if not why:
                    unresolved[b].append(
                        {
                            **doc,
                            "basis": b,
                            "usable": usable,
                            "code": code,
                            "catalogue_sha256": doc.get("publisher_sha", sha),
                            "coincident_nse_fields": tied,
                        }
                    )
        if valid_nse:
            confirmed.append(b)
    if confirmed:
        return confirmed, {b: [] for b in c.BASIS}
    # An alternative BSE-only basis has no NSE race. Check it if the other basis is unsettled.
    for b in c.BASIS:
        if nse.get((case["isin"], case["period_end"], b)):
            continue
        for code in sorted(codes):
            if code not in catalog:
                continue
            sha, cats = catalog[code]
            docs, _ = c.first_editions(
                cats.get((case["period_end"], b), []), case["window_end"], sessions
            )
            for doc, usable in docs:
                unresolved[b].append(
                    {
                        **doc,
                        "basis": b,
                        "usable": usable,
                        "code": code,
                        "catalogue_sha256": doc.get("publisher_sha", sha),
                        "coincident_nse_fields": {},
                    }
                )
    return confirmed, unresolved


def targets(root, db):
    _, _, sessions = c.signed_inputs()
    catalog = c.catalogues(db, routing := c.leads(root))
    nse = c.nse_editions()
    jobs = []
    for r in covered_rows():
        if int(r["season"]) not in YEARS:
            continue
        confirmed, alts = classify(r, catalog, routing, nse, sessions)
        if confirmed:
            continue
        for files in alts.values():
            for d in files:
                jobs.append(
                    {
                        "season": r["season"],
                        "isin": r["isin"],
                        "symbol": r["symbol"],
                        "period_end": r["period_end"],
                        "basis": d["basis"],
                        "scrip_code": d["code"],
                        "published_at": d["at"],
                        "archive_url": d["url"],
                        "catalogue_sha256": d["catalogue_sha256"],
                    }
                )
    jobs.sort(key=lambda j: (j["season"], j["period_end"], j["isin"], j["basis"], j["archive_url"]))
    raw = b"".join(
        (json.dumps(j, sort_keys=True, separators=(",", ":")) + "\n").encode() for j in jobs
    )
    c.write_once(root / "priority-plans" / (digest(raw) + ".jsonl"), raw)
    # Atomic progress pointer for the independently running inline reader.
    progress_file(root / "priority-targets.jsonl", raw)
    print(
        "priority target rows",
        len(jobs),
        "distinct URLs",
        len({j["archive_url"] for j in jobs}),
        flush=True,
    )
    return jobs


def collect():
    _, gaps, _ = c.signed_inputs()
    root = c.ROOT
    routing = c.leads(root)
    cases = [r for r in [*covered_rows(), *gaps] if int(r["season"]) in YEARS]
    codes = sorted({code for r in cases for code in routing[r["isin"]]})
    c.write_once(root / "priority-all-issuer-codes.json", json.dumps(codes).encode())
    with c.connection(root) as db:
        for attempt in range(1, 4):
            c.collect(db, [c.ARCHIVE + x for x in codes], "priority catalogue", attempt)
        targets(root, db)


def plan_only():
    with c.connection(c.ROOT) as db:
        targets(c.ROOT, db)


def legacy_documents():
    with c.connection(c.ROOT) as db:
        jobs = targets(c.ROOT, db)
        urls = [j["archive_url"] for j in jobs if "/FourOneUploadDocument/" in j["archive_url"]]
        for attempt in range(1, 4):
            c.collect(db, urls, "pre-integrated releases", attempt)


def reconcile(db, catalog, routing, sessions, good, integrated, integrated_codes):
    nse = c.nse_editions()
    details = []
    unresolved_urls = set()
    all_targets = set()
    for row in covered_rows():
        year = int(row["season"])
        confirmed, alts = classify(row, catalog, routing, nse, sessions, integrated_codes)
        base = {k: row[k] for k in ("season", "isin", "symbol", "period_end", "window_end")}
        result = {
            "supported": False,
            "basis": "",
            "venue": "",
            "filed_at": "",
            "usable_session": "",
            "url": "",
            "sha256": "",
            "edition_url": "",
            "catalogue_sha256": "",
            "inline_catalogue_sha256": "",
            "reason": "",
            "scrip_code": "",
            "revenue": "",
            "operating_profit": "",
            "net_profit": "",
            "eps": "",
            "tags": "",
        }
        if confirmed:
            basis = confirmed[0]
            ns = nse[(row["isin"], row["period_end"], basis)][3]
            result.update(
                supported=True,
                basis=basis,
                venue="NSE",
                filed_at=ns["filed_at"],
                usable_session=c.first_session(c.stamp(ns["filed_at"]), sessions),
                url=ns["structured_url"],
                sha256=ns["structured_sha256"],
                **ns["structured_values"],
                tags=json.dumps(ns.get("structured_tags", {}), sort_keys=True),
            )
        elif (
            year in YEARS
            and routing[row["isin"]]
            and all(code in catalog and code in integrated_codes for code in routing[row["isin"]])
        ):
            e = c.evaluate(db, row, alts, good, integrated)
            if e["covered"]:
                result.update(
                    {
                        k: e[k]
                        for k in (
                            "basis",
                            "filed_at",
                            "usable_session",
                            "url",
                            "sha256",
                            "edition_url",
                            "catalogue_sha256",
                            "inline_catalogue_sha256",
                            "scrip_code",
                            "revenue",
                            "operating_profit",
                            "net_profit",
                            "eps",
                            "tags",
                        )
                    }
                )
                result.update(supported=True, venue="BSE")
            else:
                result["reason"] = e["reason"]
        else:
            result["reason"] = "earlier_or_unresolved_bse_priority"
        if year in YEARS and not confirmed:
            urls = {item["url"] for files in alts.values() for item in files}
            all_targets.update(urls)
            if not result["supported"]:
                unresolved_urls.update(urls)
        details.append(base | result)
    return details, all_targets, unresolved_urls


def global_targets(db, *, root=None):
    from .results_bse_inline import catalogue_map, import_receipts

    root = root or c.ROOT
    import_receipts(db)
    _, gaps, sessions = c.signed_inputs()
    routes = c.leads(root)
    ing = catalogue_map(db)
    merged, codes = merge_catalogues(c.catalogues(db, routes), ing)
    nse = c.nse_editions()
    jobs = []
    # Earlier editions in signed faults and in the disputed already-covered years
    # use exactly the same publication rule across all three surfaces.
    for row in covered_rows():
        if int(row["season"]) not in YEARS:
            continue
        confirmed, alts = classify(row, merged, routes, nse, sessions, codes)
        if not confirmed:
            jobs.extend((row, i) for group in alts.values() for i in group)
    for row, alts in c.plan(
        db, [r for r in gaps if int(r["season"]) in YEARS], routes, sessions, catalog=merged
    ):
        jobs.extend((row, i) for group in alts.values() for i in group)
    direct = []
    for row, item in jobs:
        direct.append(
            {
                **{k: row[k] for k in ("season", "isin", "symbol", "period_end")},
                "basis": item["basis"],
                "scrip_code": item["code"],
                "published_at": item["at"],
                "url": item["url"],
                "venue": item.get("venue", "BSE Result Archive"),
                "requires_dated_bytes": item.get("requires_dated_bytes", False),
                "coincident_nse_fields": {
                    k: sorted(v) for k, v in item["coincident_nse_fields"].items()
                },
                "catalogue_sha256": item["catalogue_sha256"],
            }
        )
    raw = b"".join(
        (json.dumps(j, sort_keys=True, separators=(",", ":")) + "\n").encode() for j in direct
    )
    c.write_once(root / "priority-plans" / (digest(raw) + ".jsonl"), raw)
    progress_file(root / "global-targets.jsonl", raw)
    return direct


def global_plan():
    with c.connection(c.ROOT) as db:
        jobs = global_targets(db)
        print(
            "Global-clock URL groups",
            {
                v: len({r["url"] for r in jobs if r["venue"] == v})
                for v in {r["venue"] for r in jobs}
            },
        )


def global_native():
    from .results_bse_inline import CATALOGUE, catalogue_map, match_original

    with c.connection(c.ROOT) as db:
        jobs = global_targets(db)
        ing = catalogue_map(db)
        unmatched = set()
        matched = set()
        for j in jobs:
            if j["venue"] == "BSE Integrated":
                continue
            t = j | {"archive_url": j["url"]}
            cat = ing.get(CATALOGUE + j["scrip_code"])
            match, _ = match_original(cat[1], t) if cat else (None, "no_catalogue")
            (matched if match else unmatched).add(j["url"])
        # The independent collector obtains exact corroborated renderings. Fetch
        # native originals even for matched renditions if their body remains unverified.
        for attempt in range(1, 4):
            c.collect(
                db, sorted(unmatched), "native editions without exact inline equivalent", attempt
            )
        print("Alternate inline rendition planned", len(matched))


def remaining_native():
    """Try native earliest editions when an equivalent original has not supplied the four fields."""
    from .results_bse_inline import catalogue_map, import_receipts

    with c.connection(c.ROOT) as db:
        jobs = global_targets(db)
        inline_owned = {j["url"] for j in jobs if j["venue"] == "BSE Integrated"}
        cases = {
            (j["isin"], j["period_end"]): {
                "symbol": j["symbol"],
                "isin": j["isin"],
                "period_end": j["period_end"],
            }
            for j in jobs
        }
        for attempt in range(1, 4):
            import_receipts(db)
            _, good = c.inventory(db)
            integrated = catalogue_map(db)
            grouped = {}
            for j in jobs:
                key = (j["isin"], j["period_end"])
                basis = j["basis"]
                grouped.setdefault(key, {b: [] for b in c.BASIS})[basis].append(
                    {
                        "at": j["published_at"],
                        "url": j["url"],
                        "code": j["scrip_code"],
                        "usable": "",
                        "basis": basis,
                        "catalogue_sha256": j["catalogue_sha256"],
                        "coincident_nse_fields": j.get("coincident_nse_fields", {}),
                        "requires_dated_bytes": j.get("requires_dated_bytes", False),
                    }
                )
            done = {
                k
                for k, alts in grouped.items()
                if c.evaluate(db, cases[k], alts, good, integrated)["covered"]
            }
            urls = [
                j["url"]
                for j in jobs
                if j["venue"] != "BSE Integrated"
                and j["url"] not in inline_owned
                and (j["isin"], j["period_end"]) not in done
            ]
            c.collect(db, urls, "native alternative for unverified first editions", attempt)
