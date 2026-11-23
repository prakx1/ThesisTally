"""Recheck BSE references against every original in the terminal NSE acquisition."""

from __future__ import annotations

import gzip
import html
import io
import json
import re
import subprocess
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .discovery_registry import digest, encoded, write_once
from .fundamental_media import MAX_ENTRIES, MAX_MEMBER, MAX_TOTAL, media
from .fundamental_ocr import VERSION

URL = re.compile(
    r"(?<![\w/.-])(?:(?:https?://)?(?:[a-z0-9-]+\.)*bseindia\.(?:com|in))(?=[:/?#\s<>\x22'.,)]|$)(?:/[^<>\s()\x22']*)?",
    re.IGNORECASE,
)
DIRECT = re.compile(
    r"/(?:xml-data/corpfiling/|corporates?/xbrl/|xbrl/|attach(?:live|his)/)|\.(?:pdf|xml|zip|xlsx?|docx?)(?:$|[?#])",
    re.IGNORECASE,
)
BSE_EVIDENCE = (
    "source-access.json",
    "alternate-route-receipts.json",
    "cross-exchange-check.json",
    "cross-exchange-attachments.json",
    "public-route-reobservations.json",
    "filings.jsonl",
)


def references(text):
    if "bseindia." not in text.lower():
        return [], False
    urls = set()
    for m in URL.finditer(html.unescape(text)):
        url = m[0].rstrip(".,;:")
        if "://" not in url:
            url = "https://" + url
        host = urlsplit(url).hostname or ""
        if any(
            host == d or host.endswith("." + d) for d in ("bseindia.com", "bseindia.in")
        ) and DIRECT.search(unquote(urlsplit(url).path)):
            urls.add(url)
    return sorted(urls), True


def checked_gzip(file, sha):
    raw = gzip.decompress(file.read_bytes())
    if digest(raw) != sha:
        raise ValueError(f"Source bytes differ from SHA-256 at {file}")
    return raw


def signed_rows(root, meta):
    raw = checked_gzip(root / "source-manifests" / meta["file"], meta["sha256"])
    rows = [json.loads(s) for s in raw.splitlines()]
    if len(rows) != meta["records"]:
        raise ValueError("Source-manifest row count differs from signed bundle")
    return rows


def market_scan(root, census, membership_sha):
    inv = census["inventories"]
    inputs = {}
    for name in ("notice-market-manifest.json", "notice-alias-manifest.json"):
        m = inv[name]
        raw = checked_gzip(root / "source-manifests" / m["file"], m["sha256"])
        if (root / name).read_bytes() != raw:
            raise ValueError(f"Market catalogue changed at {name}")
        inputs[name] = json.loads(raw)
    alias = inputs["notice-alias-manifest.json"]
    market = inputs["notice-market-manifest.json"]
    raw = (root / "notice-market-sources.json").read_bytes()
    if (
        (
            alias["market_membership_sha256"]
            if "market_membership_sha256" in alias
            else alias["membership_sha256"]
        )
        != membership_sha
        or alias["market_manifest_sha256"] != inv["notice-market-manifest.json"]["sha256"]
        or digest(raw) != alias["market_sources_sha256"]
    ):
        raise ValueError("NSE market or alias catalogue is outside the pinned universe")
    sources = json.loads(raw)
    expected = {(b["url"], "period", b["total"]) for b in market["blocks"]} | {
        (b["cross"], "overlap", b["overlap"]) for b in market["blocks"]
    }
    if (
        len(sources) != len(expected)
        or {(s["url"], s["kind"], s["rows"]) for s in sources} != expected
    ):
        raise ValueError("Missing market block in final cross-exchange review")
    catalogue = {r["url"]: r["sha256"] for r in signed_rows(root, census["catalogues"])}
    symbols = set(market["symbols"]) | set(alias["symbols"])
    counts = Counter()
    hits = []
    for s in sources:
        if catalogue.get(s["url"]) != s["sha256"]:
            raise ValueError("Raw NSE market response differs from the signed catalogue")
        data = checked_gzip(
            root / "catalogues" / (digest(s["url"].encode()) + ".json.gz"), s["sha256"]
        )
        rows = json.loads(data)
        if len(rows) != s["rows"] or len(data) != s["bytes"]:
            raise ValueError("Market block row or byte count differs from its receipt")
        counts[s["kind"] + "_rows"] += len(rows)
        if s["kind"] != "period":
            continue
        for row in rows:
            if row.get("symbol") not in symbols:
                continue
            counts["eligible_symbol_announcements"] += 1
            body = " ".join(str(row.get(k) or "") for k in ("attchmntText", "attchmntFile", "desc"))
            urls, domain = references(body)
            counts["eligible_announcements_mentioning_bse"] += int(domain)
            for url in urls:
                hits.append(
                    {
                        "bse_url": url,
                        "symbol": row["symbol"],
                        "nse_announced_at": row.get("an_dt"),
                        "nse_source_url": s["url"],
                        "source_sha256": s["sha256"],
                        "record_sha256": digest(encoded(row)),
                    }
                )
    return {
        "sources_sha256": digest(raw),
        "market_sha256": alias["market_manifest_sha256"],
        "alias_sha256": inv["notice-alias-manifest.json"]["sha256"],
        "membership_sha256": membership_sha,
        "symbols": len(symbols),
        "period_blocks": len(market["blocks"]),
        "counts": dict(counts),
        "links": hits,
    }


def pdf_members(raw):
    budget = [0, 0]

    def walk(blob, depth):
        kind = media(blob)
        if kind == "pdf":
            yield digest(blob), blob
        if kind != "zip":
            return
        if depth >= 2:
            raise ValueError("Nested ZIP exceeds archived decoder limit")
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for f in z.infolist():
                if f.is_dir():
                    continue
                budget[0] += 1
                budget[1] += f.file_size
                if (
                    budget[0] > MAX_ENTRIES
                    or budget[1] > MAX_TOTAL
                    or f.file_size > MAX_MEMBER
                    or f.flag_bits & 1
                ):
                    raise ValueError("Opaque archive member outside the supported decoder")
                yield from walk(z.read(f), depth + 1)

    yield from walk(raw, 0)


def _annotation_values(raw):
    txt = raw.decode(errors="replace")
    hits = []
    for line in txt.splitlines():
        for u in references(line)[0]:
            n = re.match(r"\s*(\d+)\s+", line)
            hits.append({"url": u, "page": int(n[1]) if n else None})
    return {
        "direct": hits,
        "mentions_bse": "bseindia." in txt.lower(),
        "output_sha256": digest(raw),
    }


def annotation_read(root, sha, pdf, tool):
    store = root.parent / "bse/annotation-cache" / tool / sha[:2] / (sha + ".json")
    objects = root.parent / "bse/annotation-output-objects"
    if digest(pdf) != sha:
        raise ValueError("PDF annotation source differs from original")
    if store.is_file():
        saved = json.loads(store.read_bytes())
        if saved["source_sha256"] != sha or saved["tool_sha256"] != tool:
            raise ValueError("Annotation cache references another input")
        if saved.get("gap"):
            return saved
        output = objects / (saved["output_sha256"] + ".gz")
        if output.is_file():
            values = _annotation_values(checked_gzip(output, saved["output_sha256"]))
            if saved != {"source_sha256": sha, "tool_sha256": tool, **values}:
                raise ValueError("Annotation evidence differs from the archived reader output")
            return saved
    record = {"source_sha256": sha, "tool_sha256": tool}
    try:
        run = subprocess.run(
            ["pdfinfo", "-url", "-"], input=pdf, capture_output=True, timeout=45, check=False
        )
        if run.returncode:
            record.update(gap="unreadable PDF link annotations", return_code=run.returncode)
        else:
            record.update(_annotation_values(run.stdout))
            output = objects / (record["output_sha256"] + ".gz")
            if not output.is_file():
                write_once(output, gzip.compress(run.stdout, mtime=0))
    except subprocess.TimeoutExpired:
        record["gap"] = "PDF annotation extraction timed out"
    write_once(store, encoded(record))
    return record


def package_scan(root, frozen, meta, tool):
    checked = {r["url"]: r for r in signed_rows(root, meta)}
    ocr = {Path(f).name.split(".")[0]: f for f in frozen["ocr"]}
    if len(checked) != len(frozen["native"]):
        raise ValueError("The complete original package list differs from the numerical edition")
    counts = Counter()
    links = []
    chunks = []
    gaps = []
    used_ocr = {}
    used_urls = set()
    seen_annotations = {}

    def scan(text, source, area, pdf, page):
        direct, domain = references(text)
        counts[area + "_pages"] += 1
        counts[area + "_pages_mentioning_bse"] += int(domain)
        for link in direct:
            links.append(
                {
                    "bse_url": link,
                    "nse_source_url": source,
                    "source_type": area,
                    "pdf_sha256": pdf,
                    "page": page,
                }
            )

    for file in frozen["native"]:
        record = gzip.decompress((root / file).read_bytes())
        package = json.loads(record)
        u = package["source_url"]
        m = checked.get(u)
        if (
            not m
            or u in used_urls
            or m["text_sha256"] != digest(record)
            or m["sha256"] != package["sha256"]
            or Path(file).name != digest(u.encode()) + ".json.gz"
        ):
            raise ValueError("Native result pack differs from its published original")
        used_urls.add(u)
        sha = package["sha256"]
        raw = checked_gzip(root / "notice-objects" / sha[:2] / (sha + ".gz"), sha)
        chunks.append([file, digest(record), sha])
        counts["original_packages"] += 1
        counts["packages_without_decoded_statements"] += int(not package["pdfs"])
        for doc in package["pdfs"]:
            leaf = doc["pdf_sha256"]
            pages = doc["pages"]
            pages = pages[:-1] if pages and pages[-1] == "" else pages
            counts["document_members"] += 1
            for n, txt in enumerate(pages, 1):
                scan(txt, u, "native", leaf, n)
            sparse = {i for i, text in enumerate(pages, 1) if len(text.strip()) < 50}
            counts["native_pages_under_50_characters"] += len(sparse)
            if leaf not in ocr:
                counts["sparse_native_pages_without_readable_optical_text"] += len(sparse)
                continue
            cap = (root / ocr[leaf]).read_bytes()
            read = json.loads(gzip.decompress(cap))
            if read["pdf_sha256"] != leaf or read["method"] != VERSION:
                raise ValueError("Optical text references a different original")
            used_ocr[leaf] = digest(cap)
            counts["sparse_native_pages_without_readable_optical_text"] += sum(
                len(read["pages"].get(str(i), "").strip()) < 50 for i in sparse
            )
            for num, txt in read["pages"].items():
                if not 1 <= int(num) <= len(pages):
                    raise ValueError("Unattributed optical page outside original")
                scan(txt, u, "optical", leaf, int(num))
        try:
            members = list(pdf_members(raw))
        except (ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
            gaps.append(
                {
                    "url": u,
                    "source_sha256": sha,
                    "reason": type(exc).__name__ + ": archive members unavailable for link review",
                }
            )
            continue
        for leaf, pdf in members:
            a = annotation_read(root, leaf, pdf, tool)
            seen_annotations[leaf] = digest(encoded(a))
            counts["original_pdf_annotation_reads"] += 1
            if a.get("gap"):
                gaps.append({"url": u, "pdf_sha256": leaf, "reason": a["gap"]})
            else:
                counts["source_pdfs_with_bse_annotations"] += int(a["mentions_bse"])
                for hit in a["direct"]:
                    links.append(
                        {
                            "bse_url": hit["url"],
                            "nse_source_url": u,
                            "source_type": "PDF annotation",
                            "pdf_sha256": leaf,
                            "page": hit["page"],
                        }
                    )
    return {
        "native_list_sha256": digest(encoded(frozen["native"])),
        "native_capsules_sha256": digest(encoded(sorted(chunks))),
        "native_packages_index_sha256": meta["sha256"],
        "optical_plan_sha256": digest(encoded(frozen["ocr"])),
        "optical_objects_sha256": digest(encoded(used_ocr)),
        "optical_members_used": len(used_ocr),
        "annotation_reviews_sha256": digest(encoded(seen_annotations)),
        "annotation_reader_sha256": tool,
        "counts": dict(counts),
        "read_gaps": gaps,
        "links": links,
    }


def previous_evidence(data):
    bse = data / "bse"
    files = {f: digest((bse / f).read_bytes()) for f in BSE_EVIDENCE}
    checked = set()
    missing = []
    denials = []
    for label in (
        "source-access.json",
        "alternate-route-receipts.json",
        "public-route-reobservations.json",
        "cross-exchange-attachments.json",
    ):
        rows = json.loads((bse / label).read_bytes())
        for row in rows:
            sha = row.get("sha256")
            if row.get("status") == 403:
                denials.append(
                    {"url": row["url"], "sha256": sha, "source_date": row.get("source_date")}
                )
            if not sha:
                continue
            path = next(
                (
                    bse / f / (sha + ".gz")
                    for f in ("objects", "source-objects")
                    if (bse / f / (sha + ".gz")).is_file()
                ),
                None,
            )
            if path is None:
                if label != "alternate-route-receipts.json":
                    raise ValueError("Referenced prior exchange response is missing")
                missing.append({"url": row["url"], "sha256": sha, "source": label})
                continue
            checked_gzip(path, sha)
            checked.add(sha)
    bse_rows = [json.loads(v) for v in (bse / "filings.jsonl").read_bytes().splitlines()]
    original = [v for v in bse_rows if v.get("first_reported") is True and v.get("announced_at")]
    return {
        "receipts": files,
        "independently_checked_objects": len(checked),
        "prior_body_unavailable": missing,
        "catalogue_access_denials": denials,
        "verified_original_bse_clocks": len(original),
        "clock_by_url": {r["source_url"]: r["announced_at"] for r in original},
    }


def recheck(data, frozen, source):
    """Verify and publish the BSE fallback for a completed, byte-pinned local acquisition."""
    import shutil

    root = data / "nse"
    coverage = json.loads((data / "coverage.json").read_bytes())
    if coverage["filing_sources"]["nse"] != source:
        raise ValueError("BSE recheck needs the same NSE vintage as the coverage receipt")
    ledger = source["sha256"]
    cs = source["source_census_sha256"]
    checkpoint = root / "checkpoints" / ledger / cs
    if (root / "withdrawals" / (ledger + ".json")).exists():
        raise ValueError("BSE recheck cannot seal a withdrawn NSE source vintage")
    index = (checkpoint / "index.json").read_bytes()
    census = json.loads(checked_gzip(checkpoint / "source-index.json.gz", cs))
    if (
        digest(index) != source["checkpoint_index_sha256"]
        or census["filings_sha256"] != ledger
        or not census["notice_acquisition"]["complete"]
        or census["notice_acquisition"] != source["source_acquisition"]
    ):
        raise ValueError("BSE recheck requires the complete original source census")
    upstream = json.loads((root / "filings-receipt.json").read_bytes())
    key = upstream["frozen_ledger_inputs_sha256"]
    if (
        upstream["sha256"] != ledger
        or json.loads(checked_gzip(root / "ledger-inputs" / (key + ".json.gz"), key)) != frozen
    ):
        raise ValueError("BSE recheck received a different frozen input set")
    market = market_scan(root, census, coverage["membership_sha256"])
    tool = digest(Path(shutil.which("pdfinfo")).read_bytes())
    packages = package_scan(root, frozen, census["first_notice"], tool)
    requests = signed_rows(root, census["notice_requests"])
    states = Counter(row["status"] for row in requests)
    if (
        dict(states) != census["notice_acquisition"]["by_status"]
        or len(requests) != census["notice_acquisition"]["planned_urls"]
    ):
        raise ValueError("Unresolved source counts differ from the complete acquisition receipt")
    before = previous_evidence(data)
    if before["receipts"]["filings.jsonl"] != coverage["filing_sources"]["bse"]["sha256"]:
        raise ValueError(
            "A BSE source disposition changed; rebuild canonical coverage before publication"
        )
    candidate_urls = sorted({q["bse_url"] for q in [*market["links"], *packages["links"]]})
    clocks = before["clock_by_url"]
    from .fundamental_bse_links import summary

    linked = summary(data, candidate_urls, clocks)
    by_sha = {}
    for link in linked["per_link"].values():
        by_sha.setdefault(link["attachment_sha256"], []).append(link)
        link["identical_nse_source_urls"] = []
    for record in signed_rows(root, census["first_notice"]):
        for sha in {record["sha256"], *(d["pdf_sha256"] for d in record["pdfs"])} & by_sha.keys():
            for link in by_sha[sha]:
                link["identical_nse_source_urls"].append(record["url"])

    result = {
        "filings_sha256": ledger,
        "census_sha256": cs,
        "source_requests_sha256": census["notice_requests"]["sha256"],
        "frozen_inputs_sha256": key,
        "market": market,
        "original_packages": packages,
        "prior_bse_evidence": before,
        "linked_bse_sources": linked,
        "historical_study_cutoff": coverage["as_of"],
        "original_source_statuses": dict(states),
        "planned_source_urls": len(requests),
        "unresolved_source_urls": sum(n for k, n in states.items() if k != "archived"),
        "candidate_direct_bse_urls": candidate_urls,
        "unverified_bse_dissemination": [u for u in candidate_urls if u not in clocks],
        "nse_clock_note": "NSE bulletin times date the NSE notice. Document signatures, website retrieval and HTTP response dates cannot establish a BSE public dissemination time.",
    }
    signature = digest(encoded(result))
    path = data / "bse" / "rechecks" / (signature + ".json")
    if path.exists():
        saved = json.loads(path.read_bytes())
        checked_at = saved.pop("checked_at_utc")
        if saved != result:
            raise ValueError("Existing BSE terminal recheck differs from its evidence")
    else:
        checked_at = datetime.now(UTC).isoformat()
        write_once(path, encoded(dict(result, checked_at_utc=checked_at)))
    return {
        "receipt": str(path.relative_to(data)),
        "receipt_sha256": digest(path.read_bytes()),
        "checked_at_utc": checked_at,
        "native_list_sha256": packages["native_list_sha256"],
        "market_sources_sha256": market["sources_sha256"],
        "source_census_sha256": cs,
        "packages": packages["counts"].get("original_packages", 0),
        "eligible_symbol_announcements": market["counts"]["eligible_symbol_announcements"],
        "candidate_direct_urls": len(candidate_urls),
        "verified_bse_clocks": before["verified_original_bse_clocks"],
        "unresolved_bse_clocks": len(result["unverified_bse_dissemination"]),
        "unresolved_nse_sources": result["unresolved_source_urls"],
        "unsearched_annotation_inputs": len(packages["read_gaps"]),
    }
