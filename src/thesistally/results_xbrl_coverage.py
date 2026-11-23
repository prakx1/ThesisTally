"""Count first official XBRL releases against the registered Strategy D cohorts."""

import bisect
import csv
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from urllib.parse import urlsplit

from .discovery_guard import REPO, confined, output_root
from .discovery_registry import digest, encoded, write_once
from .fundamental_coverage import (
    IST,
    METRICS,
    complete,
    csv_text,
    first_session,
    has_field,
    ledger_bytes,
    season_year,
    stock_quarters,
)
from .fundamental_notice_proof import REVISION

PARENT_COVERAGE = "0e93eb357fb6e0905f52fd9a518a571f1738030a5b38ec4a97b0dca9ea7bb6ce"
OFFICIAL = {"nsearchives.nseindia.in", "nsearchives.nseindia.com", "archives.nseindia.com"}


def candidates(rows):
    grouped = defaultdict(list)
    for row in rows:
        link = urlsplit(row.get("structured_url") or "")
        if row.get("original_record") or not link.path.lower().endswith(".xml"):
            continue
        if link.scheme != "https" or link.hostname not in OFFICIAL:
            raise ValueError(f"Unrecognized source for XBRL filing {row['id']}")
        if row["basis"] not in {"consolidated", "standalone"}:
            raise ValueError("Structured filing lacks a recognized accounting basis")
        at = datetime.fromisoformat(row["filed_at"])
        if at.utcoffset() is None or at.astimezone(IST).date() <= date.fromisoformat(
            row["period_end"]
        ):
            raise ValueError(f"Missing or inconsistent XBRL publication time for {row['id']}")
        sha = row.get("structured_sha256")
        if sha and (
            not re.fullmatch(r"[a-f0-9]{64}", sha)
            or sha != row.get("structured_document_sha256")
            or row.get("structured_parse_gap")
        ):
            raise ValueError(f"Inconsistent XML source or parse evidence for {row['id']}")
        grouped[row["isin"], row["period_end"]].append(row)
    return grouped


def original_in_basis(rows):
    # Include inaccessible and partial XMLs when dating the first accession.
    # Synthetic PDF publication events were excluded in candidates().
    earliest = min(datetime.fromisoformat(r["filed_at"]) for r in rows)
    first = [r for r in rows if datetime.fromisoformat(r["filed_at"]) == earliest]
    first = [
        r
        for r in first
        if r["filing_status"] != "revision"
        and r.get("exchange_filing_type") != "Revision"
        and not REVISION.search(r.get("exchange_revision_remark") or "")
    ]
    if not first:
        return None, "first_xbrl_is_revision"
    parsed = [r for r in first if r.get("structured_sha256")]
    for key in METRICS:
        if (
            len(
                {
                    r["structured_values"][key]
                    for r in parsed
                    if has_field(r["structured_values"], key)
                }
            )
            > 1
        ):
            return None, "conflicting_simultaneous_first_xbrls"
    verified = [r for r in parsed if complete(r["structured_values"])]
    if verified:
        return min(verified, key=lambda r: r["id"]), None
    reasons = {r.get("structured_parse_gap") or "incomplete_xbrl_fields" for r in first}
    return None, "|".join(sorted(reasons))


def assess(rows, cutoff, sessions):
    """Count one ISIN-quarter once, with no filling of cells across releases or bases."""
    bases = defaultdict(list)
    usable_complete = []
    for row in rows:
        usable = first_session(datetime.fromisoformat(row["filed_at"]), sessions)
        if usable and usable <= cutoff:
            bases[row["basis"]].append(row)
            if row.get("structured_sha256") and complete(row["structured_values"]):
                usable_complete.append(row)
    gaps = set()
    chosen = None
    for basis in ("consolidated", "standalone"):
        if bases[basis]:
            selected, reason = original_in_basis(bases[basis])
            if selected:
                chosen = selected
                break
            gaps.update(reason.split("|"))
    known = [r for group in bases.values() for r in group]
    return {
        "listed_xml_by_cutoff": bool(known),
        "archived_xml_by_cutoff": any(r.get("structured_document_sha256") for r in known),
        "complete_any_xml_edition": bool(usable_complete),
        "covered": bool(chosen),
        "basis": chosen["basis"] if chosen else "",
        "filed_at": chosen["filed_at"] if chosen else "",
        "usable_session": first_session(datetime.fromisoformat(chosen["filed_at"]), sessions)
        if chosen
        else "",
        "accession": chosen["id"] if chosen else "",
        "structured_url": chosen["structured_url"] if chosen else "",
        "structured_sha256": chosen["structured_sha256"] if chosen else "",
        "reason": ""
        if chosen
        else "|".join(sorted(gaps)) or ("xml_unavailable_by_cutoff" if rows else "no_xml_listed"),
    }


def read_checked(path, expected):
    raw = path.read_bytes()
    if digest(raw) != expected:
        raise ValueError(f"Signed coverage input changed: {path}")
    return raw


def run(repo, dest):
    root = repo / "data/fundamentals"
    prior = json.loads(
        read_checked(root / "coverage-history" / (PARENT_COVERAGE + ".json"), PARENT_COVERAGE)
    )
    certificate = json.loads(
        read_checked(repo / "data/baseline/preflight.json", prior["preflight_sha256"])
    )
    membership = read_checked(
        repo / "data/universes/nifty200_membership.csv", prior["membership_sha256"]
    )
    sessions = certificate["sessions"]
    if (
        sessions != sorted(set(sessions))
        or not certificate["complete"]
        or sessions[-1] != prior["as_of"]
    ):
        raise ValueError("The trading calendar differs from the signed Strategy D study")
    sources = {k: v["sha256"] for k, v in prior["filing_sources"].items()}
    bse = ledger_bytes(root, "bse", sources["bse"])
    if bse.strip():
        raise ValueError("A new BSE source requires a dated XBRL adapter before counting")
    ledger = ledger_bytes(root, "nse", sources["nse"])
    rows = candidates(json.loads(line) for line in io.BytesIO(ledger))
    quarters = []
    annual = defaultdict(list)
    members = list(csv.DictReader(io.StringIO(membership.decode())))
    for q in stock_quarters(members, sessions):
        year = season_year(q["period_end"])
        cutoff = sessions[bisect.bisect_right(sessions, f"{year}-12-31") - 1]
        detail = {
            "season": year,
            "isin": q["isin"],
            "symbol": q["symbol"],
            "period_end": q["period_end"],
            "window_end": cutoff,
            **assess(rows[q["isin"], q["period_end"]], cutoff, sessions),
        }
        quarters.append(detail)
        if str(year) in prior["windows"]:
            annual[year].append(detail)
    if len(quarters) != prior["eligible"] or set(map(str, annual)) != set(prior["windows"]):
        raise ValueError("Quarter-end cohort differs from the signed study")
    result = []
    for year, cases in sorted(annual.items()):
        n = len(cases)
        if n != prior["windows"][str(year)]["eligible"]:
            raise ValueError(f"Denominator changed for {year}")
        counts = {
            k: sum(r[k] for r in cases)
            for k in (
                "covered",
                "complete_any_xml_edition",
                "listed_xml_by_cutoff",
                "archived_xml_by_cutoff",
            )
        }
        result.append(
            {
                "season": year,
                "window_end": cases[0]["window_end"],
                "eligible": n,
                **counts,
                "required": (4 * n + 4) // 5,
                "pass": counts["covered"] * 5 >= n * 4,
            }
        )
    stock_bytes = csv_text(quarters).encode()
    summary = {
        "source_rule": "first_official_xbrl_per_isin_quarter_basis_v1",
        "parent_coverage_sha256": PARENT_COVERAGE,
        "filing_ledgers": sources,
        "preflight_sha256": prior["preflight_sha256"],
        "membership_sha256": prior["membership_sha256"],
        "as_of": prior["as_of"],
        "windows": result,
        "passing_windows": sum(v["pass"] for v in result),
        "ceiling_passing_windows": sum(
            v["complete_any_xml_edition"] >= v["required"] for v in result
        ),
        "required_windows": 5,
        "eligible_quarters_including_development": len(quarters),
        "covered_by_own_season_end_including_development": sum(r["covered"] for r in quarters),
        "test_period_gap_reasons_overlap": dict(
            sorted(
                Counter(
                    reason
                    for cases in annual.values()
                    for r in cases
                    for reason in r["reason"].split("|")
                    if reason
                ).items()
            )
        ),
        "stock_quarters_sha256": digest(stock_bytes),
    }
    write_once(dest / "stock-quarters.csv", stock_bytes)
    write_once(dest / "windows.csv", csv_text(result).encode())
    write_once(dest / "coverage.json", encoded(summary))
    return summary


def main():
    if not (REPO / ".git").is_file():
        raise ValueError("Use the linked research worktree")
    if sys.argv[1:] == ["--worker"]:
        print(json.dumps(run(REPO, REPO / "data/fundamentals/d2-xbrl"), indent=2))
        return
    if sys.argv[1:]:
        raise SystemExit("Usage: python -m thesistally.results_xbrl_coverage")
    root = output_root(workstream="fundamentals")
    dest = root / "d2-xbrl"
    dest.mkdir(exist_ok=True)
    runner = confined(
        [sys.executable, "-B", "-m", __spec__.name, "--worker"],
        dest,
        env={**os.environ, "PYTHONPATH": str(REPO / "src"), "TMPDIR": str(dest)},
        check=False,
    )
    print(runner.stdout, end="")
    print(runner.stderr, file=sys.stderr, end="")
    raise SystemExit(runner.returncode)


if __name__ == "__main__":
    main()
