"""Reuse archived official PDF bytes only with matching URL, clock, issuer and full digest."""

import json
from urllib.parse import urlsplit

from .discovery_worker import archive_root
from .fundamental_manifest import NOTICE_BATCHES
from .fundamental_media_recovery import package_path
from .fundamental_notice_proof import index_notices, notice_text
from .fundamental_nse import ROOT, digest, pack, write_json


def main():
    archive = archive_root()
    lookup = index_notices()
    plans = set()
    for filename, _ in NOTICE_BATCHES.values():
        p = ROOT / filename
        if p.exists():
            plans.update(json.loads(p.read_bytes()))
    receipts = {}
    for metadata in sorted((archive / "research").glob("*/*.json")):
        symbol = metadata.parent.name
        if symbol not in lookup:
            continue
        # Only take the deterministic downloader's four source-provenance fields.
        # No model-generated text, supplied numbers or prior analysis enters the ledger.
        container = json.loads(metadata.read_bytes())
        if not isinstance(container, dict):
            continue
        for item in container.get("filings", []):
            if not isinstance(item, dict):
                continue
            url = item.get("url") or ""
            sha = item.get("sha256") or ""
            if (
                url not in plans
                or len(sha) != 64
                or not url.lower().endswith(".pdf")
                or urlsplit(url).hostname
                not in {
                    "nsearchives.nseindia.in",
                    "nsearchives.nseindia.com",
                    "archives.nseindia.com",
                }
            ):
                continue
            clock = item.get("date") or ""
            notes = [
                n
                for n in lookup[symbol]
                if n.get("source_url") == url and n["announced_at"][:19] == clock[:19]
            ]
            if not notes or len({n["announced_at"] for n in notes}) != 1:
                continue
            when = notes[0]["announced_at"]
            files = list((archive / "filings" / symbol).glob(f"{when[:10]}_*_{sha[:12]}.pdf"))
            if (
                len(files) != 1
                or digest(raw := files[0].read_bytes()) != sha
                or not raw.startswith(b"%PDF")
            ):
                continue
            key = digest(url.encode())
            receipts[url] = {
                "source_url": url,
                "sha256": sha,
                "symbol": symbol,
                "announced_at": when,
                "raw_file": str(files[0].relative_to(archive)),
                "exchange_sequence": notes[0].get("sequence"),
            }
            if package_path(ROOT, url).exists():
                continue
            docs = notice_text(raw)
            pack(ROOT / "notice-objects" / sha[:2] / (sha + ".gz"), raw)
            write_json(
                ROOT / "notice-raw-receipts" / (key + ".json"),
                {
                    "url": url,
                    "retrieved_url": url,
                    "sha256": sha,
                    "source": "previous_official_download",
                },
            )
            pack(
                ROOT / "notice-text" / (key + ".json.gz"),
                json.dumps(
                    {"source_url": url, "retrieved_url": url, "sha256": sha, "pdfs": docs},
                    ensure_ascii=False,
                ).encode(),
            )
    write_json(ROOT / "notice-previous-copies.json", [receipts[k] for k in sorted(receipts)])
