"""Reusable offline OCR receipts for the exact pixels of an archived PDF page."""

import fcntl
import gzip
import json

from .discovery_registry import digest, encoded, write_once
from .fundamental_rendered_build import page_image, render_png


def page_inputs(pdf_sha, page, size, tools):
    return {
        "pdf_sha256": pdf_sha,
        "page": int(page),
        "size": int(size),
        "recognizer_sha256": tools["source_sha256"],
        "macos_build": tools["macos_build"],
        "poppler": tools["poppler"],
        "platform": tools["platform"],
    }


def raster(root, program, pdf, pdf_sha, page, size, tools, *, retain_png=True):
    if digest(pdf) != pdf_sha:
        raise ValueError("PDF member differs from original raster input")
    inputs = page_inputs(pdf_sha, page, size, tools)
    key = digest(encoded(inputs))
    folder = root / "rendered-page-cache/v1" / key[:2]
    folder.mkdir(parents=True, exist_ok=True)
    metadata = folder / (key + ".json")
    objects = root / "rendered-page-objects"
    with (folder / (key + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if metadata.is_file():
            cached = json.loads(metadata.read_bytes())
            if cached["inputs"] != inputs:
                raise ValueError("Cached raster belongs to another PDF page")
            psha, bsha = cached["png"], cached["word_boxes"]
            png_file = objects / psha[:2] / (psha + ".png")
            png = (
                png_file.read_bytes()
                if png_file.is_file()
                else render_png(root, pdf, str(page), str(size))
            )
            boxes = gzip.decompress((objects / bsha[:2] / (bsha + ".json.gz")).read_bytes())
            if digest(png) != psha or digest(boxes) != bsha:
                raise ValueError("Cached raster or optical words changed")
            return png, boxes
        png, boxes = page_image(root, program, pdf, str(page), str(size))
        psha, bsha = digest(png), digest(boxes)
        if retain_png:
            write_once(objects / psha[:2] / (psha + ".png"), png)
        write_once(objects / bsha[:2] / (bsha + ".json.gz"), gzip.compress(boxes, mtime=0))
        write_once(metadata, encoded({"inputs": inputs, "png": psha, "word_boxes": bsha}))
        return png, boxes
