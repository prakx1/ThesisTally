import json
import struct
import subprocess
import zlib

import pytest

from thesistally import fundamental_legacy_batch as batch
from thesistally.discovery_registry import digest, encoded


def test_fast_png_keeps_exact_input_rgb_including_first_whitespace_byte():
    rgb = bytes([10, 1, 220, 5, 90, 128, 0, 255, 65, 1, 0, 127])
    png, sha = batch.lossless_png(b"P6\n2 2\n255\n" + rgb)
    assert sha == digest(rgb)
    chunks = {}
    pos = 8
    while pos < len(png):
        n, kind = struct.unpack(">I4s", png[pos : pos + 8])
        data = png[pos + 8 : pos + 8 + n]
        assert int.from_bytes(png[pos + 8 + n : pos + 12 + n], "big") == zlib.crc32(kind + data)
        chunks[kind] = data
        pos += 12 + n
    assert chunks[b"IHDR"] == struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    assert zlib.decompress(chunks[b"IDAT"]) == b"\0" + rgb[:6] + b"\0" + rgb[6:]
    with pytest.raises(ValueError, match="dimensions"):
        batch.lossless_png(b"P6\n2 2\n255\n" + rgb[:-1])


def test_bounded_batches_reuse_prior_cache_and_keep_other_pages_on_failure(tmp_path, monkeypatch):
    root = tmp_path / "nse"
    root.mkdir()
    pdf = b"archived"
    sha = digest(pdf)
    tools = {"source_sha256": "old", "platform": "test", "poppler": "renderer", "macos_build": "os"}

    def images(directory, raw, start, end):
        return [(f"png{n}".encode(), f"rgb{n}") for n in range(start, end + 1)]

    seen = []

    def recognize(directory, binary, imgs):
        seen.append([png for png, _ in imgs])
        return [encoded([png.decode()]) for png, _ in imgs]

    monkeypatch.setattr(batch, "render_pages", images)
    monkeypatch.setattr(batch, "recognize_pages", recognize)

    def read():
        return list(batch.read_pages(root, "old", "new", pdf, sha, 6, tools))

    expected = read()
    assert list(map(len, seen)) == [4, 2] and [x[0] for x in expected] == list(range(1, 7))
    assert read() == expected and len(seen) == 2
    old, _, _ = batch._paths(root, {**tools, "pdf_sha256": sha, "page": 2})
    old.parent.mkdir(parents=True)
    old.write_text("old cache present")
    old_pages = []

    def legacy(root, program, pdf, sha, n, size, tools, **kwargs):
        old_pages.append(n)
        if n == 4:
            raise subprocess.CalledProcessError(7, ["old"])
        return b"old", b"[]"

    monkeypatch.setattr(batch, "raster", legacy)
    assert read()[1][3] == {"method": "original-poppler-png"} and old_pages == [2]

    def fails(*args):
        raise subprocess.CalledProcessError(3, ["new"])

    monkeypatch.setattr(batch, "render_pages", fails)
    fallbacks = read()
    assert [r[0] for r in fallbacks] == list(range(1, 7))
    assert [r[4] for r in fallbacks] == [None, None, None, 7, None, None]
    assert fallbacks[0][3]["method"] == "original-poppler-png-batch-fallback"


def test_new_cache_preserves_and_checks_encoded_observations(tmp_path, monkeypatch):
    root = tmp_path / "nse"
    root.mkdir()
    pdf = b"source"
    sha = digest(pdf)
    tools = {"source_sha256": "x", "macos_build": "m", "poppler": "p", "platform": "z"}
    monkeypatch.setattr(batch, "render_pages", lambda *args: [(b"exact", "rgb")])
    monkeypatch.setattr(batch, "recognize_pages", lambda *args: [b"[]"])
    args = root, "original", "batch", pdf, sha, 1, tools
    [result] = list(batch.read_pages(*args))
    assert result[1] == b"exact"
    _, meta, _ = batch._paths(root, {**tools, "pdf_sha256": sha, "page": 1})
    row = json.loads(meta.read_bytes())
    row["rgb_sha256"] = "another source"
    meta.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="Changed source pixels"):
        list(batch.read_pages(*args))
    with pytest.raises(ValueError, match="Source PDF differs"):
        list(batch.read_pages(root, "original", "batch", b"another", sha, 1, tools))


def test_original_png_recovers_a_rounded_page_without_recounting_or_hiding_mutations(monkeypatch):
    from thesistally import fundamental_legacy_optics_build as build

    def rounded(*args):
        yield 1, b"page one", b"[]", {}, None
        batch.lossless_png(b"P6\n2401 1\n255\n" + b"\x00" * (2401 * 3))

    def single(root, program, pdf, sha, n, size, tools, **kwargs):
        if n == 3:
            raise subprocess.CalledProcessError(1, "pdftoppm")
        return str(n).encode(), b"[]"

    monkeypatch.setattr(build.batch, "read_pages", rounded)
    monkeypatch.setattr(build, "raster", single)
    args = None, "original", b"original", "sha", 3, {}, "batch"
    rows = list(build.original_pages(*args))
    assert [(r[0], r[1], r[4]) for r in rows] == [
        (1, b"page one", None),
        (2, b"2", None),
        (3, None, 1),
    ]
    assert rows[1][3] == {"method": "original-poppler-png"}

    def corrupted(*args):
        raise ValueError("Changed source pixels or boxes for a legacy page")

    monkeypatch.setattr(build.batch, "read_pages", corrupted)
    with pytest.raises(ValueError, match="Changed source pixels"):
        list(build.original_pages(*args))
