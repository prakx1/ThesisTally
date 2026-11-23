"""Independently audit the combined XBRL coverage from raw BSE catalogues and instances."""

import argparse
import base64
import bisect
import calendar
import csv
import gzip
import hashlib
import io
import json
import re
import sqlite3
import tarfile
from collections import Counter, defaultdict
from datetime import date, datetime, time
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from lxml import etree

ROOT = Path(__file__).resolve().parents[3]
ARCH = ROOT / "data/fundamentals"
IST = ZoneInfo("Asia/Kolkata")
XR = "{http://www.xbrl.org/2003/instance}"
INLINE = {"http://www.xbrl.org/2013/inlineXBRL", "http://www.xbrl.org/2008/inlineXBRL"}
BSE = "https://www.bseindia.com"
FIELDS = ("revenue", "operating_profit", "net_profit", "eps")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def timestamp(raw):
    if not raw or "T" not in raw:
        return None
    value = datetime.fromisoformat(raw)
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


def first_session(at, days):
    i = bisect.bisect_left(days, at.date().isoformat())
    if i < len(days) and days[i] == at.date().isoformat() and at.time() > time(15, 30):
        i += 1
    return days[i] if i < len(days) else None


def source(db, fingerprint):
    if isinstance(db, dict):
        return db[fingerprint]
    blob = db.execute("SELECT body FROM objects WHERE sha=?", (fingerprint,)).fetchone()
    assert blob
    raw = gzip.decompress(blob[0])
    assert sha(raw) == fingerprint
    return raw


def independent_catalogue(raw, code):
    for row in json.loads(raw)["Table"]:
        assert str(row["scripcode"]) == code
        match = re.fullmatch(r"(Consolidated|Standalone)-([A-Za-z]{3})-(\d{2});.*", row["Quarter"])
        if not match:
            continue
        basis, month, year = match.groups()
        month, year = list(calendar.month_abbr).index(month), 2000 + int(year)
        key = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
        relative = row["conso_xbrl_link" if basis == "Consolidated" else "stand_xbrl_link"] or ""
        url = urljoin(BSE, relative)
        if not url.lower().startswith(BSE + "/xbrlfiles/") or not url.lower().endswith(
            (".xml", ".htm", ".html", ".xhtml")
        ):
            continue
        new = (
            row["Status"] == "New"
            and not row["Revised_Date_Time"]
            and not (row["Revision_Reason"] or "").strip()
        )
        at = timestamp(
            row["Filing_Date_Time"] if new else row["Revised_Date_Time"] or row["Filing_Date_Time"]
        )
        yield key, basis.lower(), url, at, new, row["Fld_QuarterName"]


def independent_integrated(raw, code):
    for row in json.loads(raw)["Table"]:
        if row.get("seourl"):
            assert urlsplit(row["seourl"]).path.rstrip("/").split("/")[-1] == code
        m = re.fullmatch(
            r"(Consolidated|Standalone)-([A-Za-z]{3})-(Qtr|Hly|Yearly|NineMths)-(\d{4})",
            row.get("Quarter_Name") or "",
            re.IGNORECASE,
        )
        if not m:
            continue
        scope, month, _, year = m.groups()
        month, year = list(calendar.month_abbr).index(month.title()), int(year)
        period = date(year, month, calendar.monthrange(year, month)[1])
        new = (row.get("status") or "").lower().strip() == "new" and not (
            row.get("revised_date_time") or (row.get("revised_reson") or "").strip()
        )
        at = timestamp(
            row.get("filing_date_time")
            if new
            else row.get("revised_date_time") or row.get("filing_date_time")
        )
        if at and at.date() <= period:
            at = None
        for link in {row.get("XbrlFile"), row.get("xbrlurl")} - {None, ""}:
            url = urljoin(BSE, link)
            if url.lower().startswith(BSE + "/xbrlfiles/") and urlsplit(url).path.lower().endswith(
                (".xml", ".html", ".htm", ".xhtml")
            ):
                yield period.isoformat(), scope.lower(), url, at, new, "Integrated"


def bare(tag):
    return tag.split("}")[-1]


def raw_values(payload, selected):
    # Resolve each reported tag with its in-scope namespace; no production parser is used.
    doc = etree.fromstring(payload, etree.XMLParser(resolve_entities=False, no_network=True))
    contexts = doc.findall(f".//{XR}context[@id='OneD']")
    assert len(contexts) == 1
    ctx = contexts[0]
    assert [n.text for n in ctx.iter(XR + "identifier")] == [selected["scrip_code"]]
    assert not any(bare(n.tag) in {"typedMember", "explicitMember"} for n in ctx.iter())
    start = date.fromisoformat(ctx.findtext(f"{XR}period/{XR}startDate"))
    end = date.fromisoformat(ctx.findtext(f"{XR}period/{XR}endDate"))
    assert str(end) == selected["period_end"] and 75 <= (end - start).days <= 100
    units = {}
    for unit in doc.iter(XR + "unit"):
        assert unit.get("id") not in units
        parts = []
        for m in unit.iter(XR + "measure"):
            prefix, label = m.text.strip().split(":")
            parts.append((m.nsmap[prefix], label))
        units[unit.get("id")] = (parts, unit.find(XR + "divide") is not None)
    scalars = defaultdict(set)
    numeric = defaultdict(set)
    for n in doc.xpath("//*[@contextRef='OneD']"):
        q = etree.QName(n)
        name, ns = q.localname, q.namespace
        if n.get("{http://www.w3.org/2001/XMLSchema-instance}nil") in {"true", "1"}:
            continue
        if ns in INLINE:
            if name not in {"nonNumeric", "nonFraction"} or n.get("continuedAt"):
                continue
            prefix, name = n.attrib["name"].split(":")
            ns = n.nsmap[prefix]
        if not ns or not re.match(
            r"https?://(www\.)?(bseindia\.com|sebi\.gov\.in)/xbrl/", ns, re.IGNORECASE
        ):
            continue
        text = "".join(n.itertext()).strip()
        if not text:
            continue
        if not n.get("unitRef"):
            scalars[name].add(text)
            continue
        if n.get("unitRef") not in units:
            continue
        try:
            amount = Decimal(text.replace(",", "").replace("−", "-"))
        except ArithmeticError:
            continue
        if not amount.is_finite():
            continue
        amount = amount.scaleb(int(n.get("scale") or 0)) * (-1 if n.get("sign") == "-" else 1)
        numeric[name].add((amount, n.get("unitRef")))
    assert scalars["ISIN"] == {selected["isin"]} and scalars["ScripCode"] == {
        selected["scrip_code"]
    }
    assert {b.lower() for b in scalars["NatureOfReportStandaloneConsolidated"]} == {
        selected["basis"]
    }
    tags = json.loads(selected["tags"])
    assert tags["quarter_context"] == "OneD"
    for field in FIELDS:
        expr = tags[field]
        values = []
        for term in re.split(r"[-+]", expr):
            assert len(numeric[term]) == 1, (selected["url"], term, numeric[term])
            amount, unit = next(iter(numeric[term]))
            parts, divided = units[unit]
            expected = [("http://www.xbrl.org/2003/iso4217", "INR")]
            if field == "eps":
                expected.append((XR[1:-1], "shares"))
            assert parts == expected and divided == (field == "eps")
            values.append(amount)
        if len(values) == 3:
            assert field == "operating_profit" and "-" in expr and "+" in expr
            actual = values[0] - values[1] + values[2]
        else:
            assert len(values) == 1
            actual = values[0]
        published = Decimal(str(selected[field]))
        assert abs(actual - published) <= max(
            Decimal("0.000001"), abs(actual) * Decimal("1E-13")
        ), (
            selected["url"],
            field,
            actual,
            published,
        )


def nse_clocks(active):
    ledger_hash = "cb381118ee9ad0593180b7d98ac5e7d976487621ce56e7b9b1136ecdf802b1f4"
    raw = gzip.decompress((ARCH / "nse/vintages" / (ledger_hash + ".jsonl.gz")).read_bytes())
    assert sha(raw) == ledger_hash
    first = {}
    for line in raw.splitlines():
        row = json.loads(line)
        key = row["isin"], row["period_end"]
        if key not in active or row.get("original_record"):
            continue
        if not urlsplit(row.get("structured_url") or "").path.lower().endswith(".xml"):
            continue
        at = timestamp(row["filed_at"])
        target = (*key, row["basis"])
        if target not in first or first[target][0] > at:
            first[target] = at, [row]
        elif first[target][0] == at:
            first[target][1].append(row)
    return first


def corroborate_nse(moment, rows, selected):
    if not rows:
        return
    earliest, tied = rows
    assert moment <= earliest, (selected["isin"], selected["basis"], moment, earliest)
    if moment != earliest:
        return
    originals = [
        r
        for r in tied
        if r["filing_status"] != "revision"
        and r.get("exchange_filing_type") != "Revision"
        and not re.search(
            r"revis(ed|ion)|corrigendum|correct(ed|ion)|re-?submi|restat",
            r.get("exchange_revision_remark") or "",
            re.IGNORECASE,
        )
    ]
    assert originals
    for source_row in originals:
        if source_row.get("structured_sha256"):
            for field in FIELDS:
                known = source_row["structured_values"].get(field)
                if known is not None:
                    assert Decimal(str(known)) == Decimal(str(selected[field]))


def inputs(report):
    study = report.resolve().parent.parent if report is not None else ARCH / "d2-bse"
    archive = report / "sources.tar.gz" if report else None
    if archive and archive.is_file():
        manifest = json.loads((study / "sealed.json").read_bytes())
        with archive.open("rb") as stream:
            assert (
                hashlib.file_digest(stream, "sha256").hexdigest()
                == manifest["source_bundle_sha256"]
            )
        assert manifest["coverage_sha256"] == sha((report / "coverage.json").read_bytes())
        hashes, wanted, payloads, leads, rows = set(), set(), {}, [], []
        with tarfile.open(archive, "r|gz") as data:
            for member in data:
                assert member.isfile()
                raw = data.extractfile(member).read()
                if member.name.startswith("objects/"):
                    h = sha(raw)
                    assert member.name == "objects/" + h and h not in hashes
                    hashes.add(h)
                    if h in wanted:
                        payloads[h] = raw
                elif member.name == "controls.jsonl":
                    for line in raw.splitlines():
                        rec = json.loads(line)
                        if rec["body_base64"] is None:
                            assert rec["sha"] is None
                        else:
                            body = base64.b64decode(rec["body_base64"], validate=True)
                            assert sha(body) == rec["sha"]
                elif member.name == "observations.jsonl":
                    assert not rows
                    for line in raw.splitlines():
                        r = json.loads(line)
                        rows.append(tuple(r[k] for k in ("url", "kind", "sha", "headers")))
                        if r["kind"] in {"catalogue", "inline_catalogue", "xbrl"}:
                            wanted.add(r["sha"])
                else:
                    assert re.fullmatch(r"issuer-leads[\w-]*\.json", member.name)
                    leads.extend(json.loads(raw)["leads"])
        assert hashes == {r[2] for r in rows if r[2]} and wanted == set(payloads)
        return payloads, rows, leads, manifest["source_bundle_sha256"]
    db = sqlite3.connect((study / "source-archive.sqlite3").as_uri() + "?mode=ro", uri=True)
    db.execute("BEGIN")
    rows = list(db.execute("SELECT url,kind,sha,headers FROM observations ORDER BY id"))
    leads = [
        r for p in study.glob("issuer-leads*.json") for r in json.loads(p.read_bytes())["leads"]
    ]
    return db, rows, leads, None


def audit(report):
    signed = (ARCH / "d2-xbrl/stock-quarters.csv").read_bytes()
    assert sha(signed) == "04423314a259e75ac30523dfc09f31df07ec0cdb8fb9f9201b9ba047e367a63e"
    quarters = {
        (r["isin"], r["period_end"]): r for r in csv.DictReader(io.StringIO(signed.decode()))
    }
    days = json.loads((ROOT / "data/baseline/preflight.json").read_bytes())["sessions"]
    db, observations, routing, bundle = inputs(report)
    leads = defaultdict(set)
    for r in routing:
        leads[r["isin"]].add(str(r["scrip_code"]))
    active = {k: q for k, q in quarters.items() if "2017" <= q["season"] <= "2026"}
    objects = defaultdict(set)
    metadata = defaultdict(list)
    clocks = defaultdict(list)
    for url, kind, fingerprint, headers in observations:
        if kind not in {"catalogue", "inline_catalogue", "xbrl"}:
            continue
        if kind == "catalogue" and fingerprint not in objects[url]:
            code = url.split("=")[-1]
            for q, scope, doc, at, new, period_type in independent_catalogue(
                source(db, fingerprint), code
            ):
                clocks[(code, q, scope)].append((at, doc, new, fingerprint, period_type))
        if kind == "inline_catalogue" and fingerprint not in objects[url]:
            code = url.split("=")[-1]
            for q, scope, doc, at, new, period_type in independent_integrated(
                source(db, fingerprint), code
            ):
                clocks[code, q, scope].append((at, doc, new, fingerprint, period_type))
        objects[url].add(fingerprint)
        if kind == "xbrl":
            metadata[url, fingerprint].append(json.loads(headers))
    for url, fingerprint in metadata:
        assert source(db, fingerprint)
    stats = defaultdict(Counter)
    first_nse = nse_clocks(active)
    full_report = bool(report and (report / "previously-covered.csv").exists())
    integrated_url = "https://api.bseindia.com/BseIndiaAPI/api/Integratedfinancedata/w?scripcode="
    checks_nse = {}
    for (isin, q), r in active.items():
        season = stats[r["season"]]
        season["eligible"] += 1
        if r["covered"] == "True":
            season["nse_signed"] += 1
            issuer_codes = leads[isin]
            integrated_missing = (
                full_report
                and r["season"] in {"2025", "2026"}
                and any(len(objects[integrated_url + c]) != 1 for c in issuer_codes)
            )
            if (
                integrated_missing
                or not issuer_codes
                or any(
                    len(
                        objects[
                            "https://api.bseindia.com/BseIndiaAPI/api/Result_Arch_ng/w?SCRIP_CD="
                            + c
                        ]
                    )
                    != 1
                    for c in issuer_codes
                )
            ):
                season["nse_original_unresolved_catalogues"] += 1
                continue
            cutoff = datetime.combine(date.fromisoformat(r["window_end"]), time(15, 30), IST)
            for scope in ("consolidated", "standalone"):
                if not (editions := first_nse.get((isin, q, scope))):
                    continue
                at, tied = editions
                if at > cutoff:
                    continue
                financials = [
                    v["structured_values"]
                    for v in tied
                    if v.get("structured_sha256")
                    and v["filing_status"] != "revision"
                    and v.get("exchange_filing_type") != "Revision"
                    and not re.search(
                        r"revis(ed|ion)|corrigendum|correct(ed|ion)|re-?submi|restat",
                        v.get("exchange_revision_remark") or "",
                        re.IGNORECASE,
                    )
                ]
                if not any(all(f.get(k) is not None for k in FIELDS) for f in financials):
                    continue
                if any(len({f[k] for f in financials if f.get(k) is not None}) > 1 for k in FIELDS):
                    continue
                rivals = [
                    v
                    for c in issuer_codes
                    for v in clocks[c, q, scope]
                    if v[-1] in {"Quarter", "Year", "Half Year", "Integrated"}
                ]
                if any((t is None and new) or (t and t <= at) for t, _, new, _, _ in rivals):
                    continue
                checks_nse[(isin, q)] = (scope, at)
                season["nse_proved_under_both_venue_clocks"] += 1
                break
            else:
                season["nse_original_unresolved_cross_venue_clock"] += 1
            continue
        cutoff = datetime.combine(date.fromisoformat(r["window_end"]), time(15, 30), IST)
        # Overestimate additional cases: permit every available BSE XML regardless of
        # numerical content, share identity, scope, later edition, or cross-venue conflicts.
        possible = {
            doc
            for code in leads[isin]
            for scope in ("consolidated", "standalone")
            for at, doc, *_ in clocks[(code, q, scope)]
            if at and at <= cutoff
        }
        if possible:
            stats[r["season"]]["any_catalogued_dated_file"] += 1
        if any(objects[u] for u in possible):
            stats[r["season"]]["any_retrieved_dated_file"] += 1
    receipt = {
        "nse_cohort_sha256": sha(signed),
        "sealed_exchange_sources_sha256": bundle,
        "catalogues_rehashed": len({h for u in objects if "SCRIP_CD=" in u for h in objects[u]}),
        "integrated_catalogues_rehashed": len(
            {h for u in objects if u.startswith(integrated_url) for h in objects[u]}
        ),
        "raw_bse_documents_rehashed": len({h for _, h in metadata}),
        "observations": dict(Counter(obs[1] for obs in observations)),
    }
    if report:
        coverage_raw = (report / "coverage.json").read_bytes()
        published = json.loads(coverage_raw)
        details = (report / "stock-quarters.csv").read_bytes()
        assert sha(details) == published["stock_quarters_sha256"]
        seen, included = set(), []

        def check_selected(r, key):
            at = timestamp(r["filed_at"])
            corroborate_nse(at, first_nse.get((*key, r["basis"])), r)
            assert first_session(at, days) == r["usable_session"] <= active[key]["window_end"]
            assert r["scrip_code"] in leads[r["isin"]]
            first = [
                (t, u, n, h)
                for t, u, n, h, scope in clocks[r["scrip_code"], r["period_end"], r["basis"]]
                if t and scope in {"Quarter", "Year", "Half Year", "Integrated"}
            ]
            assert (at, r.get("edition_url") or r["url"], True, r["catalogue_sha256"]) in first
            if r.get("edition_url") and r["edition_url"] != r["url"]:
                assert (at, r["url"], True, r["inline_catalogue_sha256"]) in first
                a = Path(urlsplit(r["edition_url"]).path).stem
                b = Path(urlsplit(r["url"]).path).stem
                assert b in {
                    a,
                    *(
                        a + "_" + tax
                        for tax in ("IFIndAs", "IFNBFC", "IFBanking", "IFOtherthan", "IFInsurance")
                    ),
                }
            assert all(
                t > at or t == at
                for c in leads[key[0]]
                for t, _, _, _, s in clocks[c, key[1], r["basis"]]
                if t and s in {"Quarter", "Year", "Half Year", "Integrated"}
            )
            assert min(t for t, *_ in first) == at
            assert objects[r["url"]] == {r["sha256"]}
            for h in metadata[r["url"], r["sha256"]]:
                if h.get("last-modified"):
                    assert parsedate_to_datetime(h["last-modified"]) <= at
            raw_values(source(db, r["sha256"]), r)

        for r in csv.DictReader(io.StringIO(details.decode())):
            key = (r["isin"], r["period_end"])
            assert key in active and key not in seen
            seen.add(key)
            q = active[key]
            assert q["season"] == r["season"] and q["covered"] == "False"
            if r["covered"] != "True":
                continue
            check_selected(r, key)
            stats[r["season"]]["bse_verified"] += 1
            included.append((r["season"], r["isin"], r["period_end"], r["sha256"]))
        assert seen == {k for k, r in active.items() if r["covered"] == "False"}
        if full_report:
            prior = (report / "previously-covered.csv").read_bytes()
            assert sha(prior) == published["previously_covered_sha256"]
            priority_keys = set()
            for r in csv.DictReader(io.StringIO(prior.decode())):
                key = r["isin"], r["period_end"]
                assert key not in priority_keys and active[key]["covered"] == "True"
                priority_keys.add(key)
                if r["venue"] == "NSE":
                    assert r["supported"] == "True" and key in checks_nse
                    assert checks_nse[key] == (r["basis"], timestamp(r["filed_at"]))
                    assert r["sha256"] in {
                        x.get("structured_sha256") for x in first_nse[*key, r["basis"]][1]
                    }
                elif r["venue"] == "BSE":
                    assert r["supported"] == "True" and key not in checks_nse
                    check_selected(r, key)
                    stats[r["season"]]["bse_verified_previously_covered"] += 1
                    included.append((r["season"], r["isin"], r["period_end"], r["sha256"]))
                else:
                    assert r["supported"] == "False" and key not in checks_nse
            assert priority_keys == {k for k, r in active.items() if r["covered"] == "True"}
        for w in published["windows"]:
            v = stats[str(w["year"])]
            assert (v["eligible"], v["nse_signed"], v["bse_verified"]) == (
                w["eligible"],
                w["nse_verified"],
                w["bse_recovered"],
            )
        receipt.update(
            report_sha256=sha(coverage_raw),
            selected_bse_facts_verified=4 * len(included),
            distinct_bse_instances_selected=len({i[-1] for i in included}),
        )
    for v in stats.values():
        v["as_archived_upper_bound"] = v["nse_signed"] + v["any_retrieved_dated_file"]
        v["conservative_combined_lower_bound"] = (
            v["nse_proved_under_both_venue_clocks"]
            + v["bse_verified"]
            + v["bse_verified_previously_covered"]
        )
    receipt["windows"] = {y: dict(v) for y, v in sorted(stats.items())}
    receipt["upper_bound_passing_years"] = sorted(
        y for y, v in stats.items() if 5 * v["as_archived_upper_bound"] >= 4 * v["eligible"]
    )
    receipt["lower_bound_passing_years"] = sorted(
        y
        for y, v in stats.items()
        if 5 * v["conservative_combined_lower_bound"] >= 4 * v["eligible"]
    )
    if isinstance(db, sqlite3.Connection):
        db.close()
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, nargs="?")
    print(json.dumps(audit(parser.parse_args().report), indent=2, sort_keys=True))
