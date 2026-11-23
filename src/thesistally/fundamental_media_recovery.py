"""Recover issuer image folios in already archived NSE notice packages."""

from __future__ import annotations

import gzip
import io
import json
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from .fundamental_media import media
from .fundamental_notice_proof import notice_text
from .fundamental_nse import HEAD, ROOT, digest, pack, public_copy, write_json


def notice_packages(root):
    # A newer extraction augments an earlier archive; the earlier capsule remains intact.
    out = {p.name: p for p in (root / "notice-text").glob("*.json.gz")}
    out.update({p.name: p for p in (root / "notice-media").glob("*.json.gz")})
    return [out[k] for k in sorted(out)]


def package_path(root, url):
    name = digest(url.encode()) + ".json.gz"
    augment = root / "notice-media" / name
    return augment if augment.exists() else root / "notice-text" / name


def supplemental_needed(raw):
    if media(raw) != "zip":
        return False
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            with z.open(info) as member:
                kind = media(member.read(8))
            if kind in {"jpeg", "tiff", "bmp", "gif", "zip"} or (
                kind == "pdf"
                and (not info.filename.lower().endswith(".pdf") or info.file_size > 32_000_000)
            ):
                return True
    return False


def recover(client, key):
    checked = ROOT / "notice-media-checks" / (key + ".json")
    if checked.exists():
        prior = json.loads(checked.read_bytes())
        if "No supported PDF" not in (prior.get("detail") or ""):
            return
        receipt = ROOT / "notice-raw-receipts" / (key + ".json")
        if not receipt.exists():
            return
        sha = json.loads(receipt.read_bytes())["sha256"]
        archived = gzip.decompress((ROOT / "notice-objects" / sha[:2] / (sha + ".gz")).read_bytes())
        if digest(archived) != sha:
            raise ValueError("Changed original issuer folio")
        if media(archived) != "zip":
            return
        has_gif = False
        with zipfile.ZipFile(io.BytesIO(archived)) as z:
            for entry in z.infolist():
                if not entry.is_dir():
                    with z.open(entry) as member:
                        has_gif |= media(member.read(8)) == "gif"
        if not has_gif:
            return
        pack(ROOT / "notice-media-prior-checks" / (key + ".json.gz"), checked.read_bytes())
    failure = ROOT / "notice-request-failures" / (key + ".json")
    old = ROOT / "notice-text" / (key + ".json.gz")
    try:
        if old.exists():
            original = json.loads(gzip.decompress(old.read_bytes()))
            url, sha = original["source_url"], original["sha256"]
            actual = original.get("retrieved_url", url)
            if all("source_media" in doc for doc in original["pdfs"]):
                write_json(checked, {"url": url, "sha256": sha, "status": "current"})
                return
            if not url.lower().endswith(".zip"):
                write_json(checked, {"url": url, "sha256": sha, "status": "original_pdf"})
                return
            raw = gzip.decompress((ROOT / "notice-objects" / sha[:2] / (sha + ".gz")).read_bytes())
            if digest(raw) != sha:
                raise ValueError("Archived notice content changed")
            if not supplemental_needed(raw):
                write_json(checked, {"url": url, "sha256": sha, "status": "pdfs_only"})
                return
        elif failure.exists():
            url, reason = json.loads(failure.read_bytes())
            if "No PDF" not in reason and "No supported PDF" not in reason:
                return
            receipt = ROOT / "notice-raw-receipts" / (key + ".json")
            if receipt.exists():
                meta = json.loads(receipt.read_bytes())
                sha = meta["sha256"]
                actual = meta["retrieved_url"]
                raw = gzip.decompress(
                    (ROOT / "notice-objects" / sha[:2] / (sha + ".gz")).read_bytes()
                )
                if digest(raw) != sha:
                    raise ValueError("Archived notice content changed")
            else:
                raw, actual = public_copy(client, url, accept=lambda b: media(b) is not None)
                sha = digest(raw)
                pack(ROOT / "notice-objects" / sha[:2] / (sha + ".gz"), raw)
                write_json(receipt, {"url": url, "retrieved_url": actual, "sha256": sha})
        else:
            return
        docs = notice_text(raw)
        evidence = {"source_url": url, "retrieved_url": actual, "sha256": sha, "pdfs": docs}
        pack(
            ROOT / "notice-media" / (key + ".json.gz"),
            json.dumps(evidence, ensure_ascii=False).encode(),
        )
        write_json(
            checked, {"url": url, "sha256": sha, "documents": len(docs), "status": "recovered"}
        )
    except (
        httpx.HTTPError,
        ValueError,
        OSError,
        RuntimeError,
        subprocess.TimeoutExpired,
        zipfile.BadZipFile,
    ) as err:
        write_json(checked, {"key": key, "status": "unavailable", "detail": str(err)[:300]})


def candidate_keys(root):
    return {
        p.name.split(".")[0]
        for folder in ("notice-text", "notice-request-failures")
        for p in (root / folder).glob("*.json*")
    }


def main():
    keys = candidate_keys(ROOT)
    with (
        httpx.Client(headers=HEAD, timeout=30, follow_redirects=True) as client,
        ThreadPoolExecutor(3) as pool,
    ):
        list(pool.map(lambda k: recover(client, k), sorted(keys)))
    write_json(
        ROOT / "notice-folio-pass.json",
        {
            "candidate_urls": len(keys),
            "candidate_url_keys_sha256": digest(("\n".join(sorted(keys)) + "\n").encode()),
            "readable_source_packets": len(notice_packages(ROOT)),
            "checks": len(list((ROOT / "notice-media-checks").glob("*.json"))),
            "media_decoder_sha256": digest(
                Path(__file__).with_name("fundamental_media.py").read_bytes()
            ),
        },
    )


if __name__ == "__main__":
    from .fundamental_sources import run

    run("image-folios")
