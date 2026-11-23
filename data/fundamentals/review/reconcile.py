"""Independent Strategy D checks using only public study products and archived exchange bytes."""

import argparse
import bisect
import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
F = ROOT / "data/fundamentals"
ARC = ROOT.parent / "ThesisTally/data"
ARCHIVE = ARC / "stage3/close-verification/views/a79daa45cf9271df5dbf/stage3"
METRICS = ("eps", "net_profit", "operating_profit", "revenue")
TZ = ZoneInfo("Asia/Kolkata")

# Visual page observations used only to catch the withdrawn edition's misreads.
# Nothing in the acquisition, scoring or replay code imports this review file.
RENDERED_SPOT_CHECKS = {
    ("INE246F01010", "2020-12-31", "consolidated"): {"eps": 8.34},
    ("INE918I01026", "2025-09-30", "standalone"): {"eps": 6.8},
    ("INE918I01026", "2025-09-30", "consolidated"): {"net_profit": 47_464_700_000},
    ("INE013A01015", "2013-06-30", "consolidated"): {
        "revenue": 19_300_000_000,
        "operating_profit": 7_610_000_000,
    },
    ("INE647A01010", "2023-03-31", "consolidated"): {
        "revenue": 37_780_900_000,
        "net_profit": 5_624_500_000,
    },
    ("INE769A01020", "2021-09-30", "consolidated"): {
        "revenue": 15_515_800_000,
        "operating_profit": 2_387_100_000,
    },
    ("INE628A01036", "2022-03-31", "consolidated"): {
        "eps": 18.04,
        "revenue": 158_610_000_000,
        "operating_profit": 27_380_000_000,
        "net_profit": 17_350_000_000,
    },
    ("INE849A01020", "2025-09-30", "consolidated"): {
        "revenue": 48_176_800_000,
        "operating_profit": 4_921_500_000,
        "net_profit": 3_734_200_000,
    },
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def obj(path):
    return json.loads(path.read_bytes())


def same(a, b):
    assert math.isclose(a, b, abs_tol=1e-8, rel_tol=1e-9), (a, b)


def ledger_records(source, expected_digest):
    raw = (F / source / "filings.jsonl").read_bytes()
    if sha(raw) != expected_digest:
        raw = gzip.decompress(
            (F / source / "vintages" / (expected_digest + ".jsonl.gz")).read_bytes()
        )
    assert sha(raw) == expected_digest
    return [json.loads(line) for line in raw.splitlines()]


def pdf_member(raw, fingerprint, depth=0):
    if raw.startswith(b"%PDF") and sha(raw) == fingerprint:
        return raw
    if depth < 2 and zipfile.is_zipfile(io.BytesIO(raw)):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for member in archive.infolist():
                if member.filename.lower().endswith((".pdf", ".zip")):
                    found = pdf_member(archive.read(member), fingerprint, depth + 1)
                    if found is not None:
                        return found
    return None


def original_document(source_hash, pdf_sha):
    source = F / "nse/notice-objects" / source_hash[:2] / (source_hash + ".gz")
    raw = gzip.decompress(source.read_bytes())
    assert sha(raw) == source_hash
    pdf = pdf_member(raw, pdf_sha)
    assert pdf is not None, (source_hash, pdf_sha)
    return pdf


@lru_cache(maxsize=1024)
def native_page(source_hash, pdf_sha, page):
    text = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", "-", "-"],
        input=original_document(source_hash, pdf_sha),
        capture_output=True,
        check=True,
        timeout=90,
    ).stdout.decode(errors="replace")
    return text.split("\f")[0]


@lru_cache(maxsize=1)
def canonical_optical_cells():
    from check_canonical_optical_links import run as original_study

    return original_study()["canonical_field_to_receipt_sha256"]


def native_rows_control(record, proof):
    values = {}
    for field, evidence in proof["field_page_evidence"].items():
        if "page_text_sha256" not in evidence:
            # A merged accession may contain both native text and separately source-verified optical cells.
            assert evidence["field"] == field and evidence["method"] in {
                "optical_quarter_columns",
                "optical_currency_and_cells",
                "optical_grouped_statutory_columns",
                "original_indexed_expenses_operating_profit",
            }
            key = "/".join((record["isin"], record["period_end"], record["basis"], field))
            assert canonical_optical_cells()[key] == evidence["receipt_sha256"]
            assert Decimal(evidence["value"]) == Decimal(str(record[field]))
            continue
        document = native_page(proof["source_sha256"], evidence["pdf_sha256"], evidence["page"])
        assert sha(document.encode()) == evidence["page_text_sha256"]
        assert evidence["scope"] == record["basis"]
        assert evidence["quarter"]["dates"][0] == record["period_end"]
        assert evidence["statement_heading"] in document
        assert " ".join(evidence["issuer_heading"].split()) in " ".join(document.split())
        assert evidence["unit"] in document
        lines = document.splitlines()

        def amount(cell, lines=lines):
            line = lines[cell["line"] - 1]
            left, right = cell["columns"]
            assert line[left:right] == cell["raw_cell"]
            assert line[:left].strip() == cell["label"]
            assert line[right:].lstrip().split(None, 1)[0] == cell["comparison"]
            return read_amount({"text": cell["raw_cell"]})

        cell = evidence["cell"]
        if "inputs" in cell:
            a, b, c = map(amount, cell["inputs"])
            printed = a - b + c
        else:
            printed = amount(cell)
        if field != "eps":
            unit = evidence["unit"].lower()
            match = re.search(r"(?:\b|(?<=rs))(crores?|lakhs?|lacs?|millions?|thousands?)\b", unit)
            assert match, unit
            word = match[1].rstrip("s")
            printed *= {
                "crore": 10**7,
                "lakh": 10**5,
                "lac": 10**5,
                "million": 10**6,
                "thousand": 10**3,
            }[word]
        assert printed == Decimal(str(proof["values"][field]))
        assert printed == Decimal(str(record[field]))
        values[field] = str(printed)
    return values


@lru_cache(maxsize=1)
def renderer_identity():
    binary = Path(shutil.which("pdftoppm")).read_bytes()
    about = subprocess.run(["pdftoppm", "-v"], capture_output=True, check=True)
    return {"binary_sha256": sha(binary), "version": (about.stderr + about.stdout).decode().strip()}


@lru_cache(maxsize=1024)
def rerender(source_hash, pdf_sha, page, pixels):
    pdf = original_document(source_hash, pdf_sha)
    identity = {
        "tool": renderer_identity(),
        "source_sha256": source_hash,
        "pdf_sha256": pdf_sha,
        "page": page,
        "long_edge": pixels,
        "output": "pdftoppm singlefile PNG",
    }
    cache = F / "review" / "independent-renders"
    cache.mkdir(exist_ok=True)
    output = cache / (sha(json.dumps(identity, sort_keys=True).encode()) + ".json")
    if output.exists():
        memo = obj(output)
        assert memo["identity"] == identity and len(memo["sha256"]) == 64
        return memo["sha256"]
    page = str(page)
    image = subprocess.run(
        [
            "pdftoppm",
            "-q",
            "-f",
            page,
            "-l",
            page,
            "-singlefile",
            "-scale-to",
            str(pixels),
            "-png",
            "-",
        ],
        input=pdf,
        capture_output=True,
        check=True,
        timeout=480,
    ).stdout
    result = sha(image)
    pending = output.with_suffix(f".{os.getpid()}.tmp")
    pending.write_text(json.dumps({"identity": identity, "sha256": result}, sort_keys=True))
    os.replace(pending, output)
    return result


def boxes_for(identity, sample, size):
    assert (
        rerender(identity["source_sha256"], identity["pdf_sha256"], identity["page"], size)
        == sample["rendered_page_sha256"]
    )
    objects = F / "nse/rendered-page-objects"
    box_sha = sample["boxes_sha256"]
    boxes = gzip.decompress((objects / box_sha[:2] / (box_sha + ".json.gz")).read_bytes())
    assert sha(boxes) == box_sha
    return json.loads(boxes)


def read_amount(cell):
    token = cell["text"].replace("−", "-").replace(",", "")
    return Decimal("-" + token[1:-1] if token.startswith("(") and token.endswith(")") else token)


def rendered_control(proof):
    identity = proof["identity"]
    if "samples" in proof:
        verified = []
        for sample in proof["samples"]:
            boxes = boxes_for(identity, sample, sample["size"])
            o = sample["observation"]
            if not o:
                continue
            if "basic_eps_observation" in o:
                cell = o["basic_eps_observation"]["cell"]
                assert cell in boxes and o["unit_inr"] == 1 and o["currency_explicit"]
                subtotal = read_amount(cell)
            else:
                cells = o["cells"]
                assert all(c["cell"] in boxes and c["label"] in boxes for c in cells)
                values = [read_amount(c["cell"]) for c in cells]
                subtotal = values[0] if len(values) == 1 else values[0] - values[1] + values[2]
            total = subtotal * Decimal(o["unit_inr"])
            assert total == Decimal(o["value_inr"])
            verified.append(total)
        assert verified and len(set(verified)) == 1
        if proof["decision"] == "agrees_with_structured_filing":
            assert verified[0] == Decimal(str(identity["structured_value"]))
        else:
            assert proof["decision"] in {
                "agrees_with_original_text_and_both_independent_rasters",
                "agrees_with_original_text_at_two_raster_sizes",
            }
            assert len(verified) == 2 and verified[0] == Decimal(str(identity["native_value"]))
            assert all(s["observation"]["currency_explicit"] for s in proof["samples"])
        return str(verified[0])
    boxes = boxes_for(identity, proof, proof["image_long_edge"])
    cell = proof["observation"]["cell"]
    assert cell in boxes
    value = read_amount(cell)
    assert value == Decimal(str(identity["structured_value"]))
    return str(value)


def cross_basis_timing(calendar, active, inputs, probes):
    def session(stamp):
        if not stamp:
            return None
        t = datetime.fromisoformat(stamp).astimezone(TZ)
        day = t.date().isoformat()
        i = (bisect.bisect_right if t.time() > time(15, 30) else bisect.bisect_left)(calendar, day)
        return calendar[i] if i < len(calendar) and day >= calendar[0] else None

    earlier_by_issuer = defaultdict(list)
    for entry in list(inputs) + list(probes):
        # Even a formal later revision can establish that some earlier EPS may have
        # been public. An unreconciled basis cannot date a competing reaction.
        if (entry.get("isin"), entry.get("period_end"), entry.get("basis")) in active:
            continue
        stamp = (entry.get("first_notice") or {}).get("announced_at") or entry.get(
            "first_notice_at"
        )
        stamp = stamp or entry.get("announced_at")
        actual = session(stamp)
        if actual:
            earlier_by_issuer[entry["isin"], entry["period_end"]].append((actual, entry))
    identified = {}
    for (isin, period, basis), record in active.items():
        if record["eps"] is None or not record["usable_session"]:
            continue
        alternate = "standalone" if basis == "consolidated" else "consolidated"
        other = active.get((isin, period, alternate))
        earlier_unknown = sorted(
            {
                row["id"]
                for day, row in earlier_by_issuer[isin, period]
                if row["basis"] == alternate and day < record["usable_session"]
            }
        )
        partial = bool(
            other
            and other["eps"] is None
            and other["usable_session"]
            and other["usable_session"] < record["usable_session"]
        )
        if partial or earlier_unknown:
            identified[record["id"]] = {
                "earlier_no_eps_original": other["id"] if partial else None,
                "earlier_unknown_candidates": earlier_unknown,
            }
    return identified


def bound_known_quarter_clocks(active):
    directory = F / "review"
    sources = [
        directory / "formal-original-clock-document-decisions.json",
        directory / "additional-earlier-clock-confirmations.json",
    ]
    earlier = {}
    for source in sources:
        for proof in obj(source)["items"]:
            if proof["timing_status"] != "proven_original":
                continue
            # These enumerated source documents explicitly enclose both accounting bases.
            for basis in ("consolidated", "standalone"):
                key = (proof["isin"], proof["period_end"], basis)
                if key not in earlier or proof["announced_at"] < earlier[key]["announced_at"]:
                    earlier[key] = proof
    checks = {}
    for key, document in sorted(earlier.items()):
        release = active.get(key)
        stamp = document["announced_at"]
        if release is None:
            state = "unverified"
        elif release["initial_disclosure_at"] <= stamp:
            state = "retained_original_clock"
        else:
            state = "later_than_observed_original"
        checks["/".join(key)] = {
            "symbol": document["symbol"],
            "observed_original_by": stamp,
            "source_sequence": document["sequence"],
            "canonical_earliest": release["initial_disclosure_at"] if release else None,
            "eps_available_at": (release or {}).get("fact_announced_at", {}).get("eps"),
            "state": state,
        }
    return checks


def run(*, require_final_register=False):
    cert = obj(ARCHIVE / "preflight.json")["session_manifests"]
    calendar = sorted(cert)
    release = [json.loads(r) for r in (F / "first-releases.jsonl").read_text().splitlines()]
    initial_coverage = (F / "coverage.json").read_bytes()
    receipts = json.loads(initial_coverage)
    baseline_raw = (ARC / "baseline/preflight.json").read_bytes()
    assert sha(baseline_raw) == receipts["preflight_sha256"]
    assert json.loads(baseline_raw)["sessions"] == calendar
    assert (
        sha((ROOT / "data/universes/nifty200_membership.csv").read_bytes())
        == receipts["membership_sha256"]
    )
    for filename, expected_hash in receipts["products"].items():
        assert sha((F / filename).read_bytes()) == expected_hash
    inputs = [
        record
        for src in ("nse", "bse")
        for record in ledger_records(src, receipts["filing_sources"][src]["sha256"])
    ]
    data_by_unit = defaultdict(list)
    for row in inputs:
        data_by_unit[row["isin"], row["period_end"], row["basis"]].append(row)
    active = {}
    full_cases = []
    source_verified = set()
    early_notice_verified = set()
    optical = {}
    native = {}
    for r in release:
        assert r["first_reported"] is True
        stamp = datetime.fromisoformat(r["announced_at"]).astimezone(TZ)
        day = stamp.date().isoformat()
        i = (bisect.bisect_right if stamp.time() > time(15, 30) else bisect.bisect_left)(
            calendar, day
        )
        assert r["usable_session"] == (
            calendar[i] if i < len(calendar) and stamp.date().isoformat() >= calendar[0] else None
        )
        key = (r["isin"], r["period_end"], r["basis"])
        assert key not in active
        active[key] = r
        rows = data_by_unit[key]
        assert any(
            q["first_reported"]
            and q["sha256"] == r["sha256"]
            and q["announced_at"] == r["announced_at"]
            and all(q[k] == r[k] for k in METRICS)
            for q in rows
        )
        assert not any(q["first_reported"] and q["announced_at"] < r["announced_at"] for q in rows)
        if r["exchange"] == "NSE":
            locations = [(r["source_url"], r["sha256"])]
            for prior_event in r.get("eps_absence_evidence", []):
                locations.append((prior_event["source_url"], prior_event["sha256"]))
            if r.get("notice_proof"):
                pro = r["notice_proof"]
                locations.append((pro["notice_url"], pro["notice_sha256"]))
                early_notice_verified.add(pro["notice_sha256"])
                table = pro.get("additional_native_cells")
                if (
                    not table
                    and pro.get("method")
                    == "explicit_inr_original_native_quarter_cells_cross_checked_with_xbrl"
                ):
                    table = pro
                if table:
                    native[r["id"]] = native_rows_control(r, table)
                for field, optical_proof in pro.get("rendered_field_evidence", {}).items():
                    assert field in METRICS and optical_proof["identity"]["isin"] == r["isin"]
                    value = rendered_control(optical_proof)
                    assert Decimal(value) == Decimal(str(r[field]))
                    optical[r["id"] + "/" + field] = value
            for data in (r.get("fact_sources") or {}).values():
                locations.append((data["source_url"], data["sha256"]))
            for url, digest in locations:
                if digest in source_verified:
                    continue
                locations_on_disk = [
                    F / "nse" / folder / digest[:2] / (digest + ".gz")
                    for folder in ("objects", "notice-objects")
                ] + [
                    F / "nse/notice-language-sources" / (digest + ".json.gz"),
                    F / "nse/catalogues" / (sha(url.encode()) + ".json.gz"),
                ]
                path = next((p for p in locations_on_disk if p.is_file()), None)
                assert path, (digest, url)
                assert sha(gzip.decompress(path.read_bytes())) == digest
                source_verified.add(digest)
        for metric, t in r.get("fact_announced_at", {}).items():
            assert r[metric] is not None and t >= r["initial_disclosure_at"]
            stamp = datetime.fromisoformat(t).astimezone(TZ)
            i = (bisect.bisect_right if stamp.time() > time(15, 30) else bisect.bisect_left)(
                calendar, stamp.date().isoformat()
            )
            assert r["fact_usable_sessions"][metric] == (
                calendar[i]
                if i < len(calendar) and stamp.date().isoformat() >= calendar[0]
                else None
            )
        if all(r.get(k) is not None and math.isfinite(r[k]) for k in METRICS):
            if "fact_announced_at" in r:
                assert r["complete_available_at"] == max(r["fact_announced_at"].values())
            if r.get("complete_usable_session", r["usable_session"]):
                full_cases.append(r)
    reviewed = {}
    for key, fields in RENDERED_SPOT_CHECKS.items():
        original = active.get(key)
        results = {}
        for metric, visible in fields.items():
            actual = original.get(metric) if original else None
            if actual is not None:
                same(actual, visible)
            results[metric] = actual
        reviewed["/".join(key)] = results
    bounded = bound_known_quarter_clocks(active)
    if receipts["filing_sources"]["nse"].get("source_acquisition", {}).get("complete"):
        assert not any(
            row["state"] == "later_than_observed_original" for row in bounded.values()
        ), bounded
    probes_digest = receipts.get("notice_probe_gaps_sha256")
    probes = []
    if probes_digest:
        raw = gzip.decompress(
            (F / "coverage-history/objects" / (probes_digest + ".gz")).read_bytes()
        )
        assert sha(raw) == probes_digest
        probes = json.loads(raw)
    candidate_clocks = cross_basis_timing(calendar, active, inputs, probes)
    intervals = list(csv.DictReader((ROOT / "data/universes/nifty200_membership.csv").open()))
    eligible = {}
    members_cache = {}

    def members(d):
        if d not in members_cache:
            folder = ARCHIVE / "market/sessions" / d
            m = (folder / "manifest.json").read_bytes()
            assert sha(m) == cert[d]
            data = (folder / "members.csv").read_bytes()
            assert sha(data) == json.loads(m)["sha256"]["members.csv"]
            members_cache[d] = {
                r["ISIN Code"]: r["Symbol"]
                for r in csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
            }
        return members_cache[d]

    differences = []
    for quarter in pd.date_range("2014-03-31", "2026-09-30", freq="QE"):
        end = quarter.date().isoformat()
        close = calendar[bisect.bisect_right(calendar, end) - 1]
        ids = {r["isin"] for r in intervals if r["from_date"] <= close <= r["to_date"]}
        daily = set(members(close))
        for isin in sorted(ids ^ daily):
            differences.append((close, isin, isin in daily))
        for isin in ids:
            eligible[isin, end] = close
    used = {(r["isin"], r["period_end"]) for r in full_cases} & set(eligible)
    by_stock = Counter(isin for isin, _ in used)
    denom_stock = Counter(isin for isin, _ in eligible)
    annual = Counter(p[:4] for _, p in used)
    annual_den = Counter(p[:4] for _, p in eligible)
    for y, saved in receipts["years"].items():
        assert (saved["covered"], saved["eligible"]) == (annual[y], annual_den[y])
    stock_rows = list(csv.DictReader((F / "stock-coverage.csv").open()))
    all_members = {r["isin"] for r in intervals}
    assert len(stock_rows) == len(all_members)
    assert {r["isin"] for r in stock_rows} == all_members
    for r in stock_rows:
        assert (int(r["covered"]), int(r["eligible"])) == (
            by_stock[r["isin"]],
            denom_stock[r["isin"]],
        )
    by_stock_year = Counter((isin, end[:4]) for isin, end in used)
    expected_stock_years = Counter((isin, end[:4]) for isin, end in eligible)
    rows_by_stock_year = list(csv.DictReader((F / "stock-year-coverage.csv").open()))
    assert {(r["isin"], r["year"]) for r in rows_by_stock_year} == set(expected_stock_years)
    for r in rows_by_stock_year:
        key = r["isin"], r["year"]
        assert (int(r["covered"]), int(r["eligible"])) == (
            by_stock_year[key],
            expected_stock_years[key],
        )
    by_field = {
        k: {
            (r["isin"], r["period_end"])
            for r in release
            if r.get("fact_usable_sessions", {}).get(k, r["usable_session"])
            and r.get(k) is not None
        }
        & set(eligible)
        for k in METRICS
    }
    assert receipts["known_fields"] == {k: len(pairs) for k, pairs in by_field.items()}
    published_cells = {
        (r["isin"], r["period_end"]): r for r in csv.DictReader((F / "quarter-coverage.csv").open())
    }
    assert set(published_cells) == set(eligible)
    for key, r in published_cells.items():
        assert (r["covered"] == "True") == (key in used) and r["member_session"] == eligible[key]
        for metric in METRICS:
            assert (r["known_" + metric] == "True") == (key in by_field[metric])
    complete_years = []
    for y, cell in receipts["windows"].items():
        cutoff = calendar[bisect.bisect_right(calendar, y + "-12-31") - 1]
        yrs = {k for k in eligible if int(k[1][:4]) + (k[1][5:7] == "12") == int(y)}
        obs = {
            (r["isin"], r["period_end"])
            for r in full_cases
            if r.get("complete_usable_session", r["usable_session"]) <= cutoff
        }
        n = len(obs & yrs)
        assert (
            cell["eligible"] == len(yrs)
            and cell["covered"] == n
            and cell["coverage_eligible"] == (n * 5 >= len(yrs) * 4)
        )
        if cell["coverage_eligible"]:
            complete_years.append(y)
    values = {}
    for (isin, end, basis), r in active.items():
        m = datetime.fromisoformat(end)
        values[isin, m.year * 12 + m.month, basis] = r
    keys = defaultdict(list)
    for r in release:
        if r["usable_session"] and r["announced_at"][:10] >= calendar[0]:
            keys[r["isin"], r["period_end"]].append(r)
    scored = []
    for (isin, end), rows in keys.items():
        observed = [r for r in rows if r["eps"] is not None]
        if not observed:
            continue
        u = min(r["usable_session"] for r in observed)
        day_releases = [r for r in observed if r["usable_session"] == u]
        start = min(r["announced_at"] for r in day_releases)
        if any(
            not (
                r.get("eps_absence_evidence")
                and r["eps_absence_from"] <= r["announced_at"] <= start < r["eps_absence_before"]
            )
            for r in rows
            if r["usable_session"] < u
        ):
            continue
        current = min(day_releases, key=lambda r: (r["basis"] != "consolidated", r["announced_at"]))
        if candidate_clocks.get(current["id"], {}).get("earlier_unknown_candidates"):
            continue
        if isin not in members(u):
            continue
        mon = datetime.fromisoformat(end)
        month = mon.year * 12 + mon.month
        line = [values.get((isin, month - 3 * j, current["basis"])) for j in range(13)]
        limit = u + "T15:30:00+05:30"
        if any(not x or x["eps"] is None or x["announced_at"] > limit for x in line):
            continue
        values_now = [Decimal(str(x["eps"])) for x in line]
        delta = [a - b for a, b in zip(values_now[:9], values_now[4:], strict=True)]
        if len(set(delta[1:])) == 1:
            continue
        score = float(delta[0]) / np.std([float(v) for v in delta[1:]], ddof=1)
        scored.append((u, isin, end, current["announced_at"], score))
    eligible_scores = {}
    for u, isin, end, stamp, score in scored:
        cutoff = datetime.fromisoformat(u + "T15:30:00+05:30")
        peers = [
            r[4]
            for r in scored
            if r[0] <= u and cutoff - timedelta(days=90) < datetime.fromisoformat(r[3]) <= cutoff
        ]
        q = np.quantile(peers, 0.8)
        if score >= q - 1e-12:
            eligible_scores[isin, end, u] = (score, q, len(peers))
    signed = obj(F / "signals-receipt.json")
    plans = pd.read_parquet(F / "signals.parquet")
    assert signed["funnel"].get("sue_scored", 0) == len(scored)
    assert len(plans) == signed["plans"]
    # Independently reconstruct also the ranked announcements that did not become plans.
    expected = {}
    for (isin, end, u), (sue, percentile, peers) in sorted(
        eligible_scores.items(), key=lambda kv: (-kv[1][0], kv[0])
    ):
        i = calendar.index(u)
        if i < 2 or i + 2 >= len(calendar):
            continue
        day = calendar[i + 1]
        by_isin = members(day)
        if isin not in by_isin or (day, isin) in expected:
            continue
        symbol = by_isin[isin]
        path = ARCHIVE / "market/sessions" / day / "bars.parquet"
        raw_quotes = pd.read_parquet(
            path,
            columns=["symbol", "day", "close", "low", "high", "volume"],
            filters=[("symbol", "in", [symbol, "NIFTY 200"])],
        )
        both = raw_quotes.pivot(index="day", columns="symbol", values="close")
        both.index = both.index.astype(str)
        four = both.reindex(calendar[i - 2 : i + 2])
        if four.isna().any().any() or (four <= 0).any().any():
            continue
        rets = four.iloc[-1] / four.iloc[0] - 1
        reaction = rets[symbol] - rets["NIFTY 200"]
        if reaction <= 0:
            continue
        px = raw_quotes.loc[raw_quotes.symbol.eq(symbol)].set_index("day").sort_index()
        px.index = px.index.astype(str)
        px = px.reindex(both.index)
        turn = (px.close * px.volume).tail(20)
        if len(turn) != 20 or turn.isna().any() or turn.median() < 50_000_000:
            continue
        prev = px.close.ffill().shift()
        tr = pd.concat(
            [px.high - px.low, (px.high - prev).abs(), (px.low - prev).abs()], axis=1
        ).max(axis=1)
        atr = tr.ewm(alpha=1 / 14, adjust=False, ignore_na=True, min_periods=14).mean().iloc[-1]
        if not np.isfinite(atr) or atr <= 0:
            continue
        expected[day, isin] = {
            "period_end": end,
            "usable_session": u,
            "entry_session": calendar[i + 2],
            "sue": sue,
            "sue_threshold": percentile,
            "peers": peers,
            "reaction": reaction,
            "close": px.close.iloc[-1],
            "turnover": turn.median(),
            "atr14": atr,
        }
    assert set(expected) == set(zip(plans.day, plans["isin"], strict=True))
    for row in plans.itertuples(index=False):
        for metric, value in expected[row.day, row.isin].items():
            if isinstance(value, str):
                assert getattr(row, metric) == value
            else:
                same(getattr(row, metric), value)
    reports = [obj(p) for p in (F / "replays").glob("*/report.json")]
    curr = {
        "coverage": sha(initial_coverage),
        "signals": sha((F / "signals.parquet").read_bytes()),
        "signal_receipt": sha((F / "signals-receipt.json").read_bytes()),
    }
    matching = [r for r in reports if all(r["proof"][k] == v for k, v in curr.items())]
    if len(matching) > 1:
        code = ROOT / "src/thesistally"
        current_files = {
            p.name: sha(p.read_bytes())
            for p in code.rglob("*")
            if p.is_file() and p.name in matching[0]["proof"]["source"]
        }
        matching = [r for r in matching if current_files == r["proof"]["source"]]
    assert len(matching) == 1, "Exactly one replay must match the current data, signals and method"
    if matching:
        report = matching[0]
        proof = report["proof"]
        for location, expected in proof["inputs"].items():
            assert sha((ARC / location).read_bytes()) == expected
        assert sha(json.dumps(proof, sort_keys=True).encode()) == report["identity"]
        git = ["git", "-C", str(ROOT), "show"]
        prereg = subprocess.check_output(
            git + [proof["preregistration_commit"] + ":docs/PREREGISTRATION.md"]
        )
        assert sha(prereg) == proof["preregistration_sha256"]
        for source, expected in proof["source"].items():
            paths = [p for p in (ROOT / "src/thesistally").rglob(source) if p.is_file()]
            assert len(paths) == 1, source
            relative = paths[0].relative_to(ROOT).as_posix()
            committed = subprocess.check_output(git + [report["code_commit"] + ":" + relative])
            assert sha(committed) == expected, (relative, report["code_commit"])
        assert sorted(report["eligible_years"]) == sorted(complete_years)
        for y, window in report["by_year_and_window"].items():
            if y in complete_years:
                continue
            assert window["status"] == "incomplete_filing_coverage"
            assert len(window["books"]) == 4
            for book in window["books"].values():
                assert not book["replayed"] and not book["complete"]
                for field in (
                    "trades",
                    "mean_gross_r",
                    "mean_net_r",
                    "mean_net_r_ci90",
                    "benchmark_return_pct",
                    "strategy_return_pct",
                    "after_tax",
                    "loss_diagnosis",
                    "max_drawdown_pct",
                ):
                    assert book[field] is None
        if not complete_years:
            assert all(
                q["verdict"] == "not enough data" and not q["qualified"]
                for q in report["qualification"].values()
            )
    register = {}
    for horizon in (40, 20):
        ident = f"results-d{horizon}"
        directory = ROOT / "data/discovery/register" / ident
        proposal = obj(directory / "01-proposed.json")
        assert proposal["id"] == ident and proposal["hypothesis"]["holding_sessions"] == horizon
        assert sha((ROOT / proposal["evidence"]).read_bytes()) == proposal["registration_sha256"]
        assert proposal["registration_commit"].startswith(report["proof"]["preregistration_commit"])
        matched = []
        for event in sorted(directory.glob("*-study.json")):
            entry = obj(event)
            data = (ROOT / entry["evidence"]).read_bytes()
            assert sha(data) == entry["evidence_sha256"] and entry["id"] == ident
            if json.loads(data)["identity"] != report["identity"]:
                continue
            for book in ("funded", "equal_risk"):
                decision = report["qualification"][f"D{horizon}-{book}"]
                assert entry["books"][book] == {k: decision[k] for k in ("qualified", "verdict")}
            assert entry["filing_coverage_eligible_years"] == report["eligible_years"]
            if require_final_register:
                source = entry["source_processing"]
                proof_file = ROOT / source["path"]
                processing_bytes = proof_file.read_bytes()
                assert sha(processing_bytes) == source["sha256"]
                processing = json.loads(processing_bytes)
                assert processing["coverage_sha256"] == curr["coverage"]
                nse = receipts["filing_sources"]["nse"]
                assert processing["filings_sha256"] == nse["sha256"]
                assert processing["source_census_sha256"] == nse["source_census_sha256"]
                assert processing["legacy"]["ledger"] == nse["sha256"]
                assert {
                    "original_fields",
                    "scanned_pages",
                    "bse_fallback",
                    "modern_originals",
                } <= processing.keys()
                scope_file = F / "nse/terminal-xbrl-scope.json"
                scope = obj(scope_file)
                modern = processing["modern_originals"]
                assert modern["scope_sha256"] == sha(scope_file.read_bytes())
                assert modern["reference_filing_edition"] == scope["source_edition"]
                assert (
                    set(modern["cohorts"])
                    == set(scope["cohorts"])
                    == {"modern-statement-pages", "independent-earlier-release"}
                )
                for cohort, result in modern["cohorts"].items():
                    assert result["plan"] == scope["cohorts"][cohort]
                    marker = (
                        F / "nse/xbrl-optical-completions" / cohort / (result["edition"] + ".json")
                    )
                    assert sha(marker.read_bytes()) == result["marker_sha256"]
                    data = obj(marker)
                    assert (data["plan"], data["edition_sha256"], data["evaluator"]) == (
                        result["plan"],
                        result["edition"],
                        scope["evaluator"],
                    )
                if not complete_years:
                    assert (
                        entry["status"] == "proposed" and entry["evaluation"] == "insufficient data"
                    )
            matched.append(
                {"event": event.name, "status": entry["status"], "evaluation": entry["evaluation"]}
            )
        if require_final_register:
            assert len(matched) == 1, (ident, matched)
        register[ident] = matched
    assert initial_coverage == (F / "coverage.json").read_bytes()
    return {
        "quarter_ends": 51,
        "calendar_sessions_verified": len(calendar),
        "stock_quarters": len(eligible),
        "distinct_quarter_end_isins": len(denom_stock),
        "all_members_in_stock_coverage": len(all_members),
        "daily_panel_differences": differences,
        "covered": len(used),
        "known_fields": {k: len(v) for k, v in by_field.items()},
        "spot_checks": reviewed,
        "reproduced_raster_fields": optical,
        "reproduced_native_fields": dict(Counter(k for fields in native.values() for k in fields)),
        "native_source_amounts_sha256": sha(json.dumps(native, sort_keys=True).encode()),
        "cross_basis_timing_cases": candidate_clocks,
        "demonstrated_early_originals": bounded,
        "annual_window_counts": {
            y: [cell["covered"], cell["eligible"]] for y, cell in receipts["windows"].items()
        },
        "complete_years": complete_years,
        "original_attachment_hashes_verified": len(source_verified),
        "earlier_notice_hashes_verified": len(early_notice_verified),
        "first_basis_records": len(release),
        "scores_reproduced": len(scored),
        "plans_reproduced": len(plans),
        "filing_sources": receipts["filing_sources"],
        "report_id": report["identity"],
        "registered_studies": register,
        "coverage_sha256": sha((F / "coverage.json").read_bytes()),
        "signals_sha256": sha((F / "signals.parquet").read_bytes()),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-final-register", action="store_true")
    print(
        json.dumps(run(require_final_register=parser.parse_args().require_final_register), indent=2)
    )
