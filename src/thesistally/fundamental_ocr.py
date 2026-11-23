"""Offline scan transcription, accepted solely on four independent same-quarter numeric matches."""

import argparse
import gzip
import json
import os
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

from .discovery_guard import REPO, confined
from .discovery_registry import digest, encoded, write_once
from .fundamental_media import archive_documents
from .fundamental_media_recovery import notice_packages
from .fundamental_pdf_grid import corroborate_grid
from .fundamental_pdf_match import match

NATIVE = REPO / "research/results_ocr.swift"
VERSION = "vision-r3-layout-v1"
DATA = REPO / "data/fundamentals"
ROOT = DATA / "nse"


def layout(boxes):
    lines = []
    for box in sorted(boxes, key=lambda b: (-b["y"], b["x"])):
        if not lines or abs(lines[-1][0]["y"] - box["y"]) > 0.55 * min(
            box["height"], lines[-1][0]["height"]
        ):
            lines.append([])
        lines[-1].append(box)
    formatted = []
    for boxes_in_line in lines:
        line = ""
        for box in sorted(boxes_in_line, key=lambda b: b["x"]):
            column = round(box["x"] * 185)
            space = max(2 if line else 0, column - len(line))
            line += " " * space + box["text"]
        formatted.append(line)
    return "\n".join(formatted)


def capsule(root, sha):
    return root / "notice-ocr" / VERSION / sha[:2] / (sha + ".json.gz")


def corroborate(root, filing, package, url, *, available=None):
    fields = ("revenue", "operating_profit", "net_profit", "eps")
    if package["source_url"] != url or any(filing.get(k) is None for k in fields):
        return None
    for doc in package["pdfs"]:
        sha = doc["pdf_sha256"]
        if available is not None and sha not in available:
            continue
        path = capsule(root, sha)
        if not path.exists():
            continue
        record = json.loads(gzip.decompress(path.read_bytes()))
        if record["pdf_sha256"] != sha or record["method"] != VERSION:
            raise ValueError("OCR proof has the wrong input identity")
        for index, text in record["pages"].items():
            pages = [{"pdf_sha256": sha, "pages": [text]}]
            result = match(filing, pages) or corroborate_grid(filing, pages)
            if result:
                return {
                    **result,
                    "page": int(index),
                    "method": VERSION,
                    "recognition_receipt_sha256": digest(path.read_bytes()),
                    "source_member": doc.get("source_member"),
                    "source_member_sha256": doc.get("source_member_sha256", sha),
                    "rendering": doc.get("rendering", "original_pdf"),
                }
    return None


def scans(root, path, program):
    package = json.loads(gzip.decompress(path.read_bytes()))
    missing = {}
    for doc in package["pdfs"]:
        sha = doc["pdf_sha256"]
        if capsule(root, sha).exists():
            continue
        # pdftotext emits an extra empty string after the last page break.
        pages = doc["pages"][:-1] if doc["pages"] and not doc["pages"][-1].strip() else doc["pages"]
        target = [i for i, page in enumerate(pages, 1) if len(page.strip()) < 50]
        if target:
            missing[sha] = target
    if not missing:
        return 0
    sha = package["sha256"]
    blob = gzip.decompress((root / "notice-objects" / sha[:2] / (sha + ".gz")).read_bytes())
    if digest(blob) != sha:
        raise ValueError("Changed original scan")
    count = 0
    for info, raw in archive_documents(blob, root=root):
        key = info["pdf_sha256"]
        if key not in missing:
            continue
        pages = missing[key]
        proc = confined([str(program), *map(str, pages)], DATA, stdin=raw, check=False)
        if proc.returncode:
            raise ValueError(f"Offline OCR failed for {key} ({proc.returncode})")
        out = {i: layout(b) for i, b in json.loads(proc.stdout).items()}
        record = {
            "pdf_sha256": key,
            "method": VERSION,
            "engine_sha256": digest(NATIVE.read_bytes()),
            "pages": out,
        }
        write_once(capsule(root, key), gzip.compress(encoded(record), compresslevel=5, mtime=0))
        count += len(pages)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if sys.platform != "darwin" or not (REPO / ".git").is_file():
        raise ValueError("Offline Vision OCR requires macOS and the research worktree")
    build = DATA / "ocr-build"
    build.mkdir(exist_ok=True)
    program = build / ("ocr-" + digest(NATIVE.read_bytes())[:16])
    if not program.exists():
        env = {**os.environ, "TMPDIR": str(build)}
        subprocess.run(
            [
                "/usr/bin/swiftc",
                "-O",
                "-module-cache-path",
                str(build / "cache"),
                str(NATIVE),
                "-o",
                str(program),
            ],
            env=env,
            check=True,
        )
    paths = notice_packages(ROOT)
    gaps = []

    def work(p):
        try:
            return scans(ROOT, p, program)
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            gaps.append({"input": p.name, "reason": str(exc)})
            return 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        count = sum(pool.map(work, paths))
    info = {"examined": len(paths), "new_pages": count, "issues": gaps}
    if gaps:
        write_once(
            ROOT / "notice-ocr" / "issues" / (digest(encoded(info)) + ".json"), encoded(info)
        )
    print(json.dumps(info))


if __name__ == "__main__":
    main()
