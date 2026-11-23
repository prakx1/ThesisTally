"""Read nominated original pages with the same renderer and OCR used in the complete scan."""

import fcntl
import gzip
import json
import subprocess
import tempfile
from pathlib import Path

from . import fundamental_legacy_batch as batch
from .discovery_registry import digest, encoded, write_once
from .fundamental_render_cache import raster


def selection(members, selected, version):
    if selected is None:
        if version is not None:
            raise ValueError("A page-selector version requires an explicit nomination")
        return {"mode": "all_pages"}
    if not version or not isinstance(selected, dict) or set(selected) - set(members):
        raise ValueError("Name the selector and nominate only original PDF members")
    normalized = {}
    for key in sorted(members):
        ids = selected.get(key, [])
        if any(type(n) is not int or n < 1 or n > members[key] for n in ids) or list(ids) != sorted(
            set(ids)
        ):
            raise ValueError("Nominated page numbers must be valid, unique and sorted")
        normalized[key] = list(ids)
    return {
        "mode": "nominated",
        "selection_version": version,
        "nomination_sha256": digest(encoded(normalized)),
        "pages": normalized,
    }


def read(root, old, binary, pdf, sha, pages, tools):
    if digest(pdf) != sha:
        raise ValueError("Nominated source PDF changed")
    folder = root / "rendered-page-cache/legacy-batch-locks"
    folder.mkdir(parents=True, exist_ok=True)
    objects = root / "rendered-page-objects"
    with (folder / (sha + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for page in pages:
            prev, meta, inputs = batch._paths(root, {**tools, "pdf_sha256": sha, "page": page})
            completed = False
            if not prev.is_file():
                try:
                    with tempfile.TemporaryDirectory(
                        dir=root.parent, prefix="nominated-xbrl-"
                    ) as tmp:
                        work = Path(tmp)
                        images = batch.render_pages(work, pdf, page, page)
                        png, rgb = images[0]
                        expected = {**inputs, "render_profile": batch.profile()}
                        if meta.is_file():
                            check = json.loads(meta.read_bytes())
                            b = check["word_boxes"]
                            words = gzip.decompress(
                                (objects / b[:2] / (b + ".json.gz")).read_bytes()
                            )
                            if (
                                check["inputs"] != expected
                                or check["png"] != digest(png)
                                or check["rgb_sha256"] != rgb
                                or digest(words) != b
                            ):
                                raise ValueError("Original nominated image or observations changed")
                        else:
                            [words] = batch.recognize_pages(work, binary, images)
                            b = digest(words)
                            write_once(
                                objects / b[:2] / (b + ".json.gz"), gzip.compress(words, mtime=0)
                            )
                            write_once(
                                meta,
                                encoded(
                                    {
                                        "inputs": expected,
                                        "png": digest(png),
                                        "rgb_sha256": rgb,
                                        "word_boxes": b,
                                    }
                                ),
                            )
                        completed = True
                except subprocess.CalledProcessError:
                    pass
            if completed:
                yield page, png, words, {**batch.profile(), "rgb_sha256": rgb}, None
            else:
                try:
                    png, words = raster(
                        root, old, pdf, sha, page, batch.SIZE, tools, retain_png=False
                    )
                    yield page, png, words, {"method": "original-poppler-png"}, None
                except subprocess.CalledProcessError as exc:
                    yield page, None, None, None, exc.returncode
