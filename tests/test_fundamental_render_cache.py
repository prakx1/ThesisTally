import pytest

from thesistally import fundamental_render_cache as cache
from thesistally.discovery_registry import digest


def test_pages_reuse_ocr_across_originals_and_detect_changed_source_bytes(tmp_path, monkeypatch):
    tools = {
        "source_sha256": "worker",
        "macos_build": "build",
        "platform": "machine",
        "poppler": "rasterizer",
    }
    pdf = b"nonempty official page"
    args = (tmp_path, None, pdf, digest(pdf), 1, 2400, tools)
    calls = []

    def recognize(*args):
        calls.append("recognize")
        return b"pixels", b"[]"

    monkeypatch.setattr(cache, "page_image", recognize)
    monkeypatch.setattr(cache, "render_png", lambda *_: b"pixels")
    assert cache.raster(*args, retain_png=False) == (b"pixels", b"[]")
    assert cache.raster(*args) == (b"pixels", b"[]") and calls == ["recognize"]
    monkeypatch.setattr(cache, "render_png", lambda *_: b"changed")
    with pytest.raises(ValueError, match="changed"):
        cache.raster(*args)
    with pytest.raises(ValueError, match="differs"):
        cache.raster(tmp_path, None, b"another PDF", digest(pdf), 1, 2400, tools)
