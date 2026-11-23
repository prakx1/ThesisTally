"""Independently reproduce the D2 XBRL cohorts and their selected source values."""

import calendar
import csv
import gzip
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as XML

REPO = Path(__file__).resolve().parents[3]
F = REPO / "data/fundamentals"
N = F / "nse"
FIELDS = ("revenue", "operating_profit", "net_profit", "eps")
EDITION = "cb381118ee9ad0593180b7d98ac5e7d976487621ce56e7b9b1136ecdf802b1f4"
BASE = "0e93eb357fb6e0905f52fd9a518a571f1738030a5b38ec4a97b0dca9ea7bb6ce"
SYNTH = "NSE quarterly duration plus current segment contexts"


def signed(path, expected, compressed=False):
    raw = path.read_bytes()
    if compressed:
        raw = gzip.decompress(raw)
    assert hashlib.sha256(raw).hexdigest() == expected, path
    return raw


@lru_cache(maxsize=128)
def xml_source(h):
    raw = signed(N / "objects" / h[:2] / (h + ".gz"), h, True)
    doc = XML.fromstring(raw)
    assert doc.tag == "{http://www.xbrl.org/2003/instance}xbrl"
    return doc


def columns(row):
    h = row["structured_document_sha256"]
    assert h == row["structured_sha256"]
    doc = xml_source(h)
    values = {n.tag.rsplit("}", 1)[-1]: n for n in doc if n.get("contextRef") == "OneD" and n.text}
    ctxs = {n.get("id"): n for n in doc if n.tag.endswith("}context")}
    if row["structured_tags"]["quarter_context"] == SYNTH:
        contexts = [c for i, c in ctxs.items() if i.startswith("One") and i.endswith("D")]
    else:
        contexts = [ctxs["OneD"]]
    assert contexts
    for ctx in contexts:
        end = ctx.findtext(".//{*}endDate")
        begin = ctx.findtext(".//{*}startDate")
        assert (
            end == row["period_end"]
            and 75 <= (date.fromisoformat(end) - date.fromisoformat(begin)).days <= 100
        )
    units = {
        n.get("id"): [e.text.split(":")[-1] for e in n.findall(".//{*}measure")]
        for n in doc
        if n.tag.endswith("}unit")
    }

    def read(tag, k):
        item = values[tag]
        assert units[item.get("unitRef")] == (["INR", "shares"] if k == "eps" else ["INR"])
        return float(
            item.text.strip().replace(",", "").replace("−", "-").replace("(", "-").replace(")", "")
        )

    for field in FIELDS:
        term = row["structured_tags"][field]
        if "-" in term:
            a, b, c = re.split("[-+]", term)
            actual = read(a, field) - read(b, field) + read(c, field)
        else:
            actual = read(term, field)
        assert math.isclose(actual, row["structured_values"][field], rel_tol=1e-13, abs_tol=1e-6), (
            row["id"],
            field,
        )


def run():
    ref = json.loads(signed(F / "coverage-history" / (BASE + ".json"), BASE))
    cert = json.loads(signed(REPO / "data/baseline/preflight.json", ref["preflight_sha256"]))
    members = list(
        csv.DictReader(
            signed(REPO / "data/universes/nifty200_membership.csv", ref["membership_sha256"])
            .decode()
            .splitlines()
        )
    )
    days = cert["sessions"]
    cohorts = {}
    for year in range(2014, 2027):
        for month in (3, 6, 9, 12):
            q = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
            if q > days[-1]:
                continue
            session = max(d for d in days if d <= q)
            for member in members:
                if member["from_date"] <= session <= member["to_date"]:
                    key = member["isin"], q
                    assert key not in cohorts
                    cohorts[key] = str(year + (month == 12))
    assert len(cohorts) == 10221
    cases = list(csv.DictReader((F / "d2-xbrl/stock-quarters.csv").open()))
    actual = {(r["isin"], r["period_end"]): r for r in cases}
    assert len(actual) == len(cases) and set(actual) == set(cohorts)
    groups = defaultdict(lambda: defaultdict(list))
    limits = {
        s: max(d for d in days if d <= s + "-12-31") + "T15:30:00+05:30"
        for s in set(cohorts.values())
    }
    for line in signed(N / "vintages" / (EDITION + ".jsonl.gz"), EDITION, True).splitlines():
        r = json.loads(line)
        key = r["isin"], r["period_end"]
        if (
            key not in cohorts
            or r.get("original_record")
            or not urlsplit(r.get("structured_url") or "").path.lower().endswith(".xml")
        ):
            continue
        assert r["filed_at"].endswith("+05:30")
        if datetime.fromisoformat(r["filed_at"]) <= datetime.fromisoformat(limits[cohorts[key]]):
            groups[key][r["basis"]].append(r)
    series = defaultdict(Counter)
    hashes = set()
    for k, year in sorted(cohorts.items()):
        count = series[year]
        count["eligible"] += 1
        bases = groups[k]
        if any(
            r.get("structured_sha256")
            and all(r["structured_values"].get(f) is not None for f in FIELDS)
            for g in bases.values()
            for r in g
        ):
            count["any_timed_xml_with_four"] += 1
        selected = None
        for basis in ("consolidated", "standalone"):
            g = bases.get(basis, ())
            if not g:
                continue
            first = min(r["filed_at"] for r in g)
            originals = [
                r
                for r in g
                if r["filed_at"] == first
                and r.get("structured_sha256")
                and r["filing_status"] != "revision"
                and r.get("exchange_filing_type") != "Revision"
                and not re.search(
                    "revis(ed|ion)|corrigendum|correct(ed|ion)|re-?submi|restat",
                    r.get("exchange_revision_remark") or "",
                    re.IGNORECASE,
                )
            ]
            if any(
                len(
                    {
                        r["structured_values"][f]
                        for r in originals
                        if r["structured_values"].get(f) is not None
                    }
                )
                > 1
                for f in FIELDS
            ):
                continue
            full = [
                r
                for r in originals
                if all(r["structured_values"].get(f) is not None for f in FIELDS)
            ]
            if full:
                selected = min(full, key=lambda r: r["id"])
                break
        out = actual[k]
        assert (out["season"], out["covered"]) == (year, str(bool(selected)))
        if selected:
            assert (out["structured_sha256"], out["accession"], out["filed_at"]) == (
                selected["structured_sha256"],
                selected["id"],
                selected["filed_at"],
            )
            columns(selected)
            hashes.add(out["structured_sha256"])
            count["covered"] += 1
    reported = list(csv.DictReader((F / "d2-xbrl/windows.csv").open()))
    for w in reported:
        stats = series[w["season"]]
        assert (stats["covered"], stats["eligible"], stats["any_timed_xml_with_four"]) == tuple(
            int(w[f]) for f in ("covered", "eligible", "complete_any_xml_edition")
        )
    return {
        "source_edition": EDITION,
        "quarter_end_universe": len(cohorts),
        "quarter_coverage_sha256": hashlib.sha256(
            (F / "d2-xbrl/stock-quarters.csv").read_bytes()
        ).hexdigest(),
        "distinct_selected_original_xmls_rehashed": len(hashes),
        "selected_quarters_with_all_four_raw_values_reproduced": sum(
            c["covered"] for c in series.values()
        ),
        "windows": {w["season"]: dict(series[w["season"]]) for w in reported},
        "qualifying_years": [
            w["season"]
            for w in reported
            if 5 * series[w["season"]]["covered"] >= 4 * series[w["season"]]["eligible"]
        ],
    }


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True, indent=2))
