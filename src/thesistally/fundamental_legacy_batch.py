"""Batch exact Poppler RGB pages for the offline legacy financial-statement check."""

import fcntl
import gzip
import json
import os
import re
import struct
import subprocess
import tempfile
import zlib
from functools import lru_cache
from pathlib import Path

from .discovery_guard import REPO, confined
from .discovery_registry import digest, encoded, write_once
from .fundamental_render_cache import page_inputs, raster

SIZE = 2400
LIMIT = 4
SOURCE = REPO / "research/results_legacy_batch.swift"
PROFILE = "poppler-p6-rgb8-png-filter0-zlib1-vision3-v1"


@lru_cache(maxsize=1)
def profile():
    return {
        "method": PROFILE,
        "recognizer_source_sha256": digest(SOURCE.read_bytes()),
        "renderer_source_sha256": digest(Path(__file__).read_bytes()),
        "zlib": zlib.ZLIB_RUNTIME_VERSION,
    }


def program(root):
    build = root.parent / "ocr-build"
    binary = build / ("legacy-batch-" + profile()["recognizer_source_sha256"][:16])
    with (build / "legacy-batch-compile.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not binary.exists():
            with tempfile.TemporaryDirectory(dir=build) as temp:
                out = Path(temp) / "program"
                subprocess.run(
                    [
                        "/usr/bin/swiftc",
                        "-O",
                        "-module-cache-path",
                        str(build / "cache"),
                        str(SOURCE),
                        "-o",
                        str(out),
                    ],
                    check=True,
                    env={**os.environ, "TMPDIR": str(build)},
                )
                os.replace(out, binary)
    return binary


def lossless_png(ppm):
    header = re.match(rb"\AP6\s+([0-9]+)\s+([0-9]+)\s+255[ \r\n\t]", ppm)
    if not header:
        raise ValueError("Expected Poppler 8-bit RGB original page")
    width, height = map(int, header.groups())
    rgb = memoryview(ppm)[header.end() :]
    if not (0 < width <= SIZE and 0 < height <= SIZE and len(rgb) == 3 * width * height):
        raise ValueError("Poppler original page dimensions or raster length are invalid")
    stride = width * 3
    scanlines = b"".join(b"\0" + rgb[k : k + stride] for k in range(0, len(rgb), stride))

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines, 1))
        + chunk(b"IEND", b"")
    )
    return png, digest(rgb)


def render_pages(temp, pdf, start, end):
    if not 1 <= start <= end < start + LIMIT:
        raise ValueError("Invalid bounded original page batch")
    confined(
        [
            "pdftoppm",
            "-q",
            "-f",
            str(start),
            "-l",
            str(end),
            "-scale-to",
            str(SIZE),
            "-",
            str(temp / "p"),
        ],
        temp,
        stdin=pdf,
    )
    files = {int(f.stem.split("-")[-1]): f for f in temp.glob("p-*.ppm")}
    if set(files) != set(range(start, end + 1)):
        raise ValueError("Poppler returned a different set of original pages")
    results = []
    for page in range(start, end + 1):
        png, rgb = lossless_png(files[page].read_bytes())
        results.append((png, rgb))
    return results


def recognize_pages(temp, binary, images):
    paths = []
    for i, (png, _) in enumerate(images):
        path = temp / f"word-input-{i}.png"
        path.write_bytes(png)
        paths.append(str(path))
    raw = confined([str(binary), *paths], temp, stdin=b"").stdout
    rows = [encoded(json.loads(line)) for line in raw.splitlines()]
    if len(rows) != len(images):
        raise ValueError("Optical reader returned an incomplete original page batch")
    return rows


def _paths(root, inputs):
    base = page_inputs(inputs["pdf_sha256"], inputs["page"], SIZE, inputs)
    old_key = digest(encoded(base))
    old = root / "rendered-page-cache/v1" / old_key[:2] / (old_key + ".json")
    key = digest(encoded({**base, "render_profile": profile()}))
    return old, root / "rendered-page-cache/legacy-batch-v1" / key[:2] / (key + ".json"), base


def read_pages(root, old_program, binary, pdf, sha, count, tools):
    """Yield (page, PNG, words, renderer, exit status) and reuse any earlier verified read."""
    if digest(pdf) != sha:
        raise ValueError("Source PDF differs from legacy page batch")
    lockdir = root / "rendered-page-cache/legacy-batch-locks"
    lockdir.mkdir(parents=True, exist_ok=True)
    obj = root / "rendered-page-objects"
    with (lockdir / (sha + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        paths = {
            n: _paths(root, {**tools, "pdf_sha256": sha, "page": n}) for n in range(1, count + 1)
        }
        page = 1
        while page <= count:
            old = paths[page][0]
            if old.is_file():
                try:
                    png, boxes = raster(
                        root, old_program, pdf, sha, page, SIZE, tools, retain_png=False
                    )
                    yield page, png, boxes, {"method": "original-poppler-png"}, None
                except subprocess.CalledProcessError as exc:
                    yield page, None, None, None, exc.returncode
                page += 1
                continue
            end = page
            # Old page receipts always retain their original tool and compressed PNG identity.
            while end < count and end - page + 1 < LIMIT and not paths[end + 1][0].is_file():
                end += 1
            try:
                with tempfile.TemporaryDirectory(dir=root.parent, prefix="optical-rgb-") as tmp:
                    folder = Path(tmp)
                    images = render_pages(folder, pdf, page, end)
                    missing = []
                    known = {}
                    for n, (png, rgb) in zip(range(page, end + 1), images, strict=True):
                        _, meta, base = paths[n]
                        if not meta.is_file():
                            missing.append(n)
                            continue
                        saved = json.loads(meta.read_bytes())
                        boxes = gzip.decompress(
                            (
                                obj / saved["word_boxes"][:2] / (saved["word_boxes"] + ".json.gz")
                            ).read_bytes()
                        )
                        if (
                            saved["inputs"] != {**base, "render_profile": profile()}
                            or digest(png) != saved["png"]
                            or saved["rgb_sha256"] != rgb
                            or digest(boxes) != saved["word_boxes"]
                        ):
                            raise ValueError("Changed source pixels or boxes for a legacy page")
                        known[n] = boxes
                    if missing:
                        boxes = recognize_pages(folder, binary, [images[n - page] for n in missing])
                        for n, row in zip(missing, boxes, strict=True):
                            png, rgb = images[n - page]
                            _, meta, base = paths[n]
                            bsha = digest(row)
                            write_once(
                                obj / bsha[:2] / (bsha + ".json.gz"), gzip.compress(row, mtime=0)
                            )
                            write_once(
                                meta,
                                encoded(
                                    {
                                        "inputs": {**base, "render_profile": profile()},
                                        "png": digest(png),
                                        "rgb_sha256": rgb,
                                        "word_boxes": bsha,
                                    }
                                ),
                            )
                            known[n] = row
                for n, (png, rgb) in zip(range(page, end + 1), images, strict=True):
                    yield n, png, known[n], {**profile(), "rgb_sha256": rgb}, None
            except subprocess.CalledProcessError:
                # A corrupt page must not suppress other pages in the same attachment.
                for n in range(page, end + 1):
                    try:
                        png, boxes = raster(
                            root, old_program, pdf, sha, n, SIZE, tools, retain_png=False
                        )
                        yield n, png, boxes, {"method": "original-poppler-png-batch-fallback"}, None
                    except subprocess.CalledProcessError as exc:
                        yield n, None, None, None, exc.returncode
            page = end + 1
