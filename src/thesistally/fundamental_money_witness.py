"""Pin the printed rupee fields and preserve genuine differences from a later structured filing."""

import gzip
import json
from decimal import Decimal

from .discovery_registry import digest, encoded, write_once
from .fundamental_rendered import identity
from .fundamental_rendered_money import observe

SIZES = (3200, 2400)


def location(root, info):
    key = digest(encoded(info))
    return root / "notice-rendered-money/v2" / key[:2] / (key + ".json")


def deciding_witness(observations, native, structured):
    values = {Decimal(obs["value_inr"]) for obs in observations if obs}
    if len(values) != 1 or any(obs and not obs["currency_explicit"] for obs in observations):
        return None
    value = values.pop()
    if structured is not None and value == Decimal(str(structured)):
        return "agrees_with_structured_filing"
    if (
        native is not None
        and value == Decimal(str(native))
        and len(observations) == len(SIZES)
        and all(obs and obs["currency_explicit"] for obs in observations)
        and len({obs["unit_inr"] for obs in observations}) == 1
    ):
        return "agrees_with_original_text_at_two_raster_sizes"
    return None


def original_rendered_money(root, row, release, package, text_proof, field, *, available=None):
    info = {
        **identity(row, release, package, text_proof, field),
        "native_value": text_proof["values"][field],
    }
    path = location(root, info)
    if (available is not None and path.stem not in available) or not path.is_file():
        return None
    raw = path.read_bytes()
    evidence = json.loads(raw)
    if evidence["identity"] != info or tuple(s["size"] for s in evidence["samples"]) != SIZES:
        raise ValueError("Rendered money certificate belongs to different source inputs")
    observations = []
    for sample in evidence["samples"]:
        objects = root / "rendered-page-objects"
        sha = sample["rendered_page_sha256"]
        if digest((objects / sha[:2] / (sha + ".png")).read_bytes()) != sha:
            raise ValueError("Changed original rendered monetary page")
        sha = sample["boxes_sha256"]
        body = gzip.decompress((objects / sha[:2] / (sha + ".json.gz")).read_bytes())
        if digest(body) != sha:
            raise ValueError("Changed monetary optical word boxes")
        obs = observe(row, json.loads(body), field)
        if obs != sample["observation"]:
            raise ValueError("Monetary field no longer maps to the same original image cell")
        observations.append(obs)
    method = deciding_witness(observations, info["native_value"], info["structured_value"])
    if not method or method != evidence["decision"]:
        raise ValueError("Monetary certificate has insufficient corroboration")
    value = next(o["value_inr"] for o in observations if o)
    return {
        **evidence,
        "value": float(value),
        "pdf_sha256": info["pdf_sha256"],
        "page": info["page"],
        "method": method,
        "evidence_sha256": digest(raw),
    }


def archive_observation(root, info, views, decision, tools):
    samples = []
    for size, png, boxes, observation in views:
        psha, bsha = digest(png), digest(boxes)
        objects = root / "rendered-page-objects"
        write_once(objects / psha[:2] / (psha + ".png"), png)
        write_once(objects / bsha[:2] / (bsha + ".json.gz"), gzip.compress(boxes, mtime=0))
        samples.append(
            {
                "size": size,
                "rendered_page_sha256": psha,
                "boxes_sha256": bsha,
                "observation": observation,
            }
        )
    write_once(
        location(root, info),
        encoded({"identity": info, "decision": decision, "tools": tools, "samples": samples}),
    )
