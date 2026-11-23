"""Verify the receipt and archived official bytes without importing study code."""

import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1] / "nse"


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def packed_digest(path):
    state = hashlib.sha256()
    size = 0
    with gzip.open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            state.update(block)
            size += len(block)
    return state.hexdigest(), size


def run():
    handover = (ROOT / "source-downloads-terminal.json").read_bytes()
    claim = json.loads(handover)
    assert claim["network_only"] is True
    b = gzip.decompress((ROOT / claim["source_census_file"]).read_bytes())
    assert sha(b) == claim["source_census_sha256"]
    stages = json.loads(b)
    assert stages["notice_acquisition"]["complete"] and stages["structured_acquisition_complete"]
    assert all(stages["notice_acquisition"]["completed_batches"].values())
    tables = {}
    for name, metadata in stages.items():
        if not isinstance(metadata, dict) or not {"file", "records", "sha256"}.issubset(metadata):
            continue
        raw = gzip.decompress((ROOT / "source-manifests" / metadata["file"]).read_bytes())
        assert sha(raw) == metadata["sha256"]
        rows = list(map(json.loads, raw.splitlines()))
        assert len(rows) == metadata["records"]
        tables[name] = rows

    def official(value):
        u = urlsplit(value)
        assert u.scheme == "https" and u.hostname in {
            "www.nseindia.in",
            "nsearchives.nseindia.in",
            "archives.nseindia.com",
            "nsearchives.nseindia.com",
        }, value

    url_files = {}
    total_footprint = Counter()
    empty_packages = []
    seen_files = set()
    for name in ("catalogues", "structured", "first_notice", "raw_notice_receipts"):
        rows = tables[name]
        assert len({r["url"] for r in rows}) == len(rows)
        for row in rows:
            url, claimed = row["url"], row["sha256"]
            official(url)
            official(row["retrieved_url"])
            if name == "catalogues":
                path = ROOT / "catalogues" / (sha(url.encode()) + ".json.gz")
                expected = claimed
            else:
                folder = "objects" if name == "structured" else "notice-objects"
                path = ROOT / folder / claimed[:2] / (claimed + ".gz")
                expected = claimed
            if path not in seen_files:
                actual, size = packed_digest(path)
                assert actual == expected
                if "bytes" in row:
                    assert row["bytes"] == size
                seen_files.add(path)
                total_footprint[folder if name != "catalogues" else name] += size
            if name != "catalogues":
                assert url_files.get(url, claimed) == claimed
                url_files[url] = claimed
            if name == "first_notice":
                filename = sha(url.encode()) + ".json.gz"
                choices = (ROOT / "notice-media" / filename, ROOT / "notice-text" / filename)
                # The census may point to a supplemental image folio package.
                fmt = next(
                    f for f in choices if f.is_file() and packed_digest(f)[0] == row["text_sha256"]
                )
                package = json.loads(gzip.decompress(fmt.read_bytes()))
                assert package["sha256"] == claimed and package["source_url"] == url
                if not package["pdfs"]:
                    empty_packages.append({"url": url, "sha256": claimed})
                assert [(doc["pdf_sha256"], len(doc["pages"])) for doc in package["pdfs"]] == [
                    (doc["pdf_sha256"], doc["pages"]) for doc in row["pdfs"]
                ]
    request_counts = {}
    snapshots = {
        "notice_requests": {r["url"]: r for r in tables["first_notice"]},
        "structured_requests": {r["url"]: r for r in tables["structured"]},
    }
    physically_unreadable = []
    empty_receipt = Path(__file__).with_name("two-empty-official-attachments.json").read_bytes()
    empty_responses = json.loads(empty_receipt)

    def empty_all_mirrors(url):
        leaf = urlsplit(url).path
        replies = [x for x in empty_responses if urlsplit(x["requested"]).path == leaf]
        return {urlsplit(x["url"]).hostname for x in replies} == {
            "archives.nseindia.com",
            "nsearchives.nseindia.com",
            "nsearchives.nseindia.in",
        } and all(
            x["status"] == 200 and x["bytes"] == 0 and x["body_sha256"] == sha(b"") for x in replies
        )

    for name, inventory in snapshots.items():
        req = tables[name]
        assert len({r["url"] for r in req}) == len(req)
        counts = Counter(x["status"] for x in req)
        assert (
            counts
            == claim["notice_outcomes" if name == "notice_requests" else "structured_outcomes"]
        )
        assert (
            len(req)
            == claim["notice_planned" if name == "notice_requests" else "structured_planned"]
        )
        for row in req:
            url = row["url"]
            official(url)
            if row["status"] == "archived":
                assert inventory[url]["sha256"] == row["sha256"]
            else:
                assert row["status"] == "unavailable" and row.get("error")
                if not row.get("terminal_official_error"):
                    assert (
                        (
                            row.get("media_recovery", {}).get("status") == "unavailable"
                            and url in url_files
                        )
                        or all(
                            host + "=HTTP 404" in row["error"]
                            for host in (
                                "archives.nseindia.com",
                                "nsearchives.nseindia.com",
                                "nsearchives.nseindia.in",
                            )
                        )
                        or empty_all_mirrors(url)
                    ), url
                assert url not in inventory
                if url in url_files:
                    physically_unreadable.append(
                        {"url": url, "sha256": url_files[url], "reason": row["error"]}
                    )
        request_counts[name] = dict(counts)
    return {
        "source_handover_sha256": sha(handover),
        "empty_mirror_receipt_sha256": sha(empty_receipt),
        "network_census_sha256": sha(b),
        "rechecked_tables": {k: len(v) for k, v in tables.items()},
        "request_statuses": request_counts,
        "distinct_archived_files_hashed": len(seen_files),
        "uncompressed_bytes_hashed": dict(total_footprint),
        "retained_raw_sources_with_unreadable_statements": physically_unreadable,
        "indexed_first_notice_packages": len(tables["first_notice"]),
        "first_notice_packages_with_decoded_documents": len(tables["first_notice"])
        - len(empty_packages),
        "indexed_notice_packages_with_no_decoded_documents": empty_packages,
        "jubilant_mirror_receipt_sha256": sha(
            Path(__file__).with_name("jubilant-truncated-pdf-mirror-receipts.json").read_bytes()
        ),
        "raw_and_readable_notice_overlap": len(
            {r["url"] for r in tables["raw_notice_receipts"]}
            & {r["url"] for r in tables["first_notice"]}
        ),
        "additional_relevant_urls_unplanned": claim[
            "verified_additional_first_document_urls_unplanned"
        ],
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
