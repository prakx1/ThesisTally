"""Recreate the BPCL original standalone currency evidence with local, unrestricted OCR.

Run with tesserocr 2.13.0 and the checksum-pinned tessdata_fast/4.1.0 Hindi and
English models. The script reads the archived official PDF and writes only to
the results worktree. The single fixed crop contains the entire source legend.
"""

import argparse
import gzip
import json
import struct
import subprocess
from pathlib import Path

from thesistally.discovery_guard import REPO, output_root
from thesistally.discovery_registry import digest, encoded, write_once
from thesistally.fundamental_media_recovery import package_path
from thesistally.fundamental_rendered_build import original_pdf, render_png

PDF = "34bef44abddf163f1dddbc7eddb0b00ae634e9e5fa2d910cf93d440a7f477853"
URL = "https://nsearchives.nseindia.in/corporate/resultsdec211_31012022172039.pdf"
LANG = {
    "hin": "4c73ffc59d497c186b19d1e90f5d721d678ea6b2e277b719bee4e2af12271825",
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}


def capture(models, wheel):
    import tesserocr as t

    if t.__version__ != "2.13.0" or not t.tesseract_version().startswith("tesseract 5.5.4"):
        raise ValueError("Unexpected optical engine")
    wheel_sha = digest(wheel.read_bytes())
    if wheel_sha != "9a172c4ec8d2eca0236bac569c8355a11b7210e34b3bc41c0d9874b9b3b19b8f":
        raise ValueError("Unpinned optical engine wheel")
    for lang, expected in LANG.items():
        if digest((models / (lang + ".traineddata")).read_bytes()) != expected:
            raise ValueError("Changed recognizer language data")
    root = output_root(workstream="fundamentals") / "nse"
    out = root / "targeted-currency"
    out.mkdir(exist_ok=True)
    package = json.loads(gzip.decompress(package_path(root, URL).read_bytes()))
    if package["sha256"] != PDF:
        raise ValueError("Changed original earnings packet")
    doc = next(d for d in package["pdfs"] if d["pdf_sha256"] == PDF)
    native = doc["pages"][3].encode()
    write_once(out / "page4-native.txt.gz", gzip.compress(native, mtime=0))
    pdf = original_pdf(root, PDF, doc)
    views = []
    for size in [2400, 3200, 4200]:
        page = render_png(root, pdf, "4", str(size))
        w, h = struct.unpack(">II", page[16:24])
        x, y, cw, ch = int(0.8693 * w), int(0.1494 * h), int(0.0652 * w), int(0.0085 * h)
        flags = f"crop={cw}:{ch}:{x}:{y},scale={cw * 3}:{ch * 3}:flags=lanczos,pad=iw+80:ih+80:40:40:color=white"
        args = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-vf",
            flags,
            "-threads",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "pipe:1",
        ]
        crop = subprocess.run(args, input=page, capture_output=True, check=True).stdout
        path = out / f"legend-{size}.png"
        write_once(path, crop)
        readings = {}
        for lang in LANG:
            with t.PyTessBaseAPI(path=str(models), lang=lang, psm=t.PSM.SINGLE_LINE) as api:
                api.SetImageFile(str(path))
                api.Recognize()
                lvl = t.RIL.SYMBOL
                readings[lang] = [
                    {
                        "character": r.GetUTF8Text(lvl),
                        "bounds": r.BoundingBox(lvl),
                        "confidence": round(r.Confidence(lvl), 2),
                    }
                    for r in t.iterate_level(api.GetIterator(), lvl)
                ]
        views.append(
            {
                "size": size,
                "rendered_page_sha256": digest(page),
                "roi_xywh": [x, y, cw, ch],
                "crop_sha256": digest(crop),
                "symbols": readings,
            }
        )
    receipt = {
        "source_url": URL,
        "source_sha256": PDF,
        "pdf_sha256": PDF,
        "page": 4,
        "page_text_sha256": digest(native),
        "model_sha256": LANG,
        "tesserocr_wheel_sha256": wheel_sha,
        "tesseract": t.tesseract_version(),
        "poppler": subprocess.run(
            ["pdftoppm", "-v"], capture_output=True, text=True, check=True
        ).stderr.splitlines()[0],
        "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0],
        "capture_program_sha256": digest(Path(__file__).read_bytes()),
        "page_parser_sha256": digest(
            (REPO / "src/thesistally/fundamental_native_rows.py").read_bytes()
        ),
        "views": views,
    }
    write_once(out / "evidence.json", encoded(receipt))


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("models", type=Path)
    args.add_argument("wheel", type=Path)
    opts = args.parse_args()
    capture(opts.models, opts.wheel)
