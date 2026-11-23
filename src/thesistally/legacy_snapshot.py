"""Build the dated legacy controls overlay; raw circulars and original sessions stay immutable."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from .legacy_evidence import (
    asm_status,
    compose,
    gsm_table,
    parse_table,
    verified_isin,
    workbooks,
)
from .legacy_surveillance import (
    COMPONENTS,
    END,
    FOLDER,
    GSM,
    LT,
    REVIEWED,
    ST,
    START,
    component,
    sha,
    verified_inventory,
    write_json,
)

# The shortlist was published before the first action. Leave named securities
# unknown during that initial interval; an unlisted ISIN is definitively clear.
GSM_FIRST = date(2017, 3, 3)
GSM_EFFECTIVE = date(2017, 3, 14)
ASM_FIRST = date(2018, 3, 21)
ST_EFFECTIVE = date(2018, 11, 14)
GSM_START = "SURV34321.pdf"
ASM_START = "SURV37253.zip"
ST_START = "SURV39265.pdf"


def inventory(folder):
    rows = []
    for f in sorted((folder / "catalogue").glob("????-??.json")):
        receipt = json.loads(f.with_suffix(".json.source.json").read_bytes())
        raw = f.read_bytes()
        if receipt["sha256"] != sha(raw):
            raise ValueError(f"Corrupt circular inventory {f}")
        for row in json.loads(raw)["data"]:
            if row["fileDept"] == "SURV":
                rows.append(row)
    return sorted(rows, key=lambda r: (r["cirDate"], r["circDisplayNo"]))


def checked_raw(folder, name):
    path = folder / "raw" / name
    raw = path.read_bytes()
    meta = json.loads(path.with_suffix(path.suffix + ".source.json").read_bytes())
    if meta["sha256"] != sha(raw):
        raise ValueError(f"Corrupt surveillance evidence {name}")
    return raw


def pdf_text(raw):
    return subprocess.run(
        ["pdftotext", "-layout", "-", "-"], input=raw, capture_output=True, check=True
    ).stdout.decode()


def gsm_first_pool(raw):
    text = pdf_text(raw)
    ids = re.findall(r"\bIN[A-Z0-9]{10}\b", text)
    if len(ids) != 43 or len(set(ids)) != 43 or not all(verified_isin(i) for i in ids):
        raise ValueError(
            "First NSE GSM list must include all 37 listed and six suspended securities"
        )
    return set(ids)


def full_quarter(raw):
    """Conservative membership superset: entrants, retained, AND those awaiting removal."""
    if raw.startswith(b"%PDF"):
        # The first update is a text PDF with numeric rows split across pages.
        # Check the serial sequence of all three annexures individually.
        parts = re.split(r"(?m)^\s*Annexure (I{1,3})\s*$", pdf_text(raw))
        if parts[1::2] != ["I", "II", "III"]:
            return None
        ids = set()
        for text in parts[2::2]:
            numbered = [m for m in re.finditer(r"(?m)^\s*(\d+)\s+([^\n]+)", text)]
            if not numbered or [int(m[1]) for m in numbered] != list(range(1, len(numbered) + 1)):
                return None
            for m in numbered:
                row_ids = re.findall(r"\bIN[A-Z0-9]{10}\b", m[2])
                if len(row_ids) != 1 or not verified_isin(row_ids[0]):
                    return None
                ids.update(row_ids)
        return ids
    sheets = list(workbooks(raw))
    if len(sheets) != 1:
        return None
    w = sheets[0]
    expected = {
        "annexure i": "shortlisted",
        "annexure ii": "retained",
        "annexure iii": "moving out",
    }
    if {s.strip().lower() for s in w} != set(expected):
        return None
    pool = set()
    for name, rows in w.items():
        label = expected[name.strip().lower()]
        if label not in " ".join(rows[0].values()).lower():
            return None
        ids, complete, _ = parse_table(rows)
        if not complete:
            return None
        pool.update(ids)
    return pool


def stage_pdf(raw):
    """Read only a fully numbered ISIN table confined to an official stage-action letter."""
    text = pdf_text(raw)
    head = re.search(r"(?im)^\s*(?:sr\.?\s*no\.?|sn)\s+.*?symbol.*?isin\s*$", text)
    if not head:
        return None
    end = re.search(
        r"(?im)^\s*(?:trading in|trading shall|the above mentioned securities|members are requested)",
        text[head.end() :],
    )
    if not end:
        return None
    table = text[head.end() : head.end() + end.start()]
    rows = [r for r in table.splitlines() if r.strip()]
    numbered = []
    ids = set()
    for row in rows:
        cells = re.match(r"^\s*(\d+)\s+.+?\b(IN[A-Z0-9]{10})\b\s*$", row)
        if not cells or not verified_isin(cells[2]):
            return None
        numbered.append(int(cells[1]))
        ids.add(cells[2])
    if not ids or numbered != list(range(1, len(numbered) + 1)):
        return None
    if set(re.findall(r"\bIN[A-Z0-9]{10}\b", text)) != ids:
        return None
    if not re.search(
        r"following securities shall be moved to stage", " ".join(text.lower().split())
    ):
        return None
    return ids


class QuarterlyGSM:
    """Prove negative ISINs from a full list and every intervening SURV notice.

    Names that entered or left, or whose timing is ambiguous, remain in the
    superset. Any unclassified exchange surveillance notice breaks the chain.
    A stage-change notice must expose a complete, validated annexure before
    its named ISINs may be added to the superset.
    """

    def __init__(self, folder):
        self.folder = folder
        self.pool = None
        self.evidence = set()
        self.anchor = None
        self.reason = "no complete quarterly list"
        self.initial = set()

    def stop(self, reason):
        self.pool = None
        self.reason = reason
        self.evidence = set()

    def apply(self, row):
        title = " ".join(row["sub"].lower().split())
        name = row["circFilename"]
        published = date.fromisoformat(row["cirDate"])
        cat = "catalogue/" + published.strftime("%Y-%m") + ".json"
        if name == GSM_START:
            self.initial = gsm_first_pool(checked_raw(self.folder, name))
            self.pool = set(self.initial)
            self.evidence = {"raw/" + name, cat}
            self.anchor = name
            self.reason = None
            return
        scheme = component(row)
        if scheme == GSM and "updated list of companies" in title:
            path = self.folder / "raw" / name
            pool = full_quarter(checked_raw(self.folder, name)) if path.is_file() else None
            if pool is None:
                return self.stop("unresolved quarterly annexures: " + name)
            self.pool = pool
            self.evidence = {"raw/" + name, cat}
            self.anchor = name
            self.reason = None
            return
        if self.pool is None:
            return
        self.evidence.add(cat)
        if name in REVIEWED:
            raw = checked_raw(self.folder, name)
            if sha(raw) != REVIEWED[name]["sha256"]:
                return self.stop("source bytes differ from reviewed directive: " + name)
            self.evidence.add("raw/" + name)
            if REVIEWED[name]["action"] == "nse-shell-list":
                sheets = [sheet for book in workbooks(raw) for sheet in book.values()]
                if len(sheets) != 1:
                    return self.stop("ambiguous NSE shell annexure")
                ids, complete, _ = parse_table(sheets[0])
                if not complete or len(ids) != 48:
                    return self.stop("unverified NSE shell annexure")
                self.pool.update(ids)
            return
        if scheme in {LT, ST}:
            return
        if scheme != GSM:
            # All other subjects (including SEBI orders/court directions)
            # break the carry until the next independently complete list.
            if re.search(
                r"currency derivatives|commodity derivatives|^derivatives contracts in |^participation of non resident indians.*exchange traded currency",
                title,
            ):
                return
            if re.search(
                r"^(?:trade\s*(?:for|to|[-])\s*trade\b|changes in price bands?\b|illiquid securit|revised ratios for conversion of usdinr|mechanism to monitor revised limits relating to requirement of underlying exposure for currency derivatives|(?:additional )?exposure margin.*mwpl)",
                title,
            ):
                return
            return self.stop("unresolved surveillance notice: " + name)
        if re.fullmatch(
            r"(?:list of )?securities moving out of (?:stage [iv]+ of )?graded surveillance measures? \(gsm\)(?:\s*[-–]\s*update)?",
            title,
        ):
            # Deliberately keep outgoing ISINs in the conservative superset.
            return
        if (
            title.startswith("graded surveillance measure")
            and "periodic relaxation of surveillance action" in title
        ):
            # Relaxation only lowers existing members' stages. Retaining every
            # pre-relaxation candidate cannot incorrectly clear an ISIN.
            return
        if not re.fullmatch(
            r"list of securities moving to stage [iv]+ (?:of )?graded surveillance measures?(?: \(gsm\))?",
            title,
        ):
            return self.stop("unresolved GSM instruction: " + name)
        path = self.folder / "raw" / name
        if not path.is_file():
            return self.stop("unresolved GSM stage annexure: " + name)
        if path.suffix.lower() == ".pdf":
            ids = stage_pdf(checked_raw(self.folder, name))
            if ids is None:
                return self.stop("unresolved GSM stage PDF: " + name)
            self.pool.update(ids)
            self.evidence.add("raw/" + name)
            return
        if path.suffix.lower() != ".zip":
            return self.stop("unresolved GSM stage annexure: " + name)
        sheets = [s for w in workbooks(checked_raw(self.folder, name)) for s in w.values()]
        if not sheets:
            return self.stop("missing stage workbook: " + name)
        for table in sheets:
            ids, complete, _ = parse_table(table)
            if not complete:
                return self.stop("incomplete stage workbook: " + name)
            self.pool.update(ids)
        self.evidence.add("raw/" + name)


def dated_market_ids(session):
    with (session / "bhavcopy.csv").open(errors="replace", newline="") as f:
        pairs = defaultdict(set)
        for row in csv.DictReader(f):
            symbol = row["TckrSymb"].strip()
            series = row["SctySrs"].strip()
            isin = row["ISIN"].strip()
            if verified_isin(isin):
                pairs[(symbol, series)].add(isin)
    return {k: next(iter(ids)) for k, ids in pairs.items() if len(ids) == 1}


def members(session):
    with (session / "members.csv").open(newline="") as f:
        return {r["Symbol"].strip(): r["ISIN Code"].strip() for r in csv.DictReader(f)}


def asm_on_day(folder, component_name, notices, effective):
    found = []
    reasons = []
    files = set()
    for row in notices:
        if component(row) != component_name:
            continue
        name = row["circFilename"]
        path = folder / "raw" / name
        if not path.is_file() or path.suffix.lower() != ".zip":
            reasons.append(name + ": no applicable ISIN workbook")
            continue
        raw = checked_raw(folder, name)
        ids, complete, errors = asm_status(raw, component_name, effective)
        if ids or complete:
            found.append((ids, complete))
            files.add("raw/" + name)
        reasons.extend(f"{name}: {e}" for e in errors)
    candidates = [r for r in notices if component(r) == component_name]
    if len(candidates) > 1 and (
        len(found) != len(candidates) or not all(x == found[0] for x in found)
    ):
        return (
            set(),
            False,
            set(),
            [
                "Conflicting or unverified same-day scheme notices: "
                + ", ".join(r["circFilename"] for r in candidates)
            ],
        )
    if len(found) == 1:
        ids, complete = found[0]
        return ids, complete, files, reasons
    if len(found) > 1 and all(result == found[0] for result in found):
        return *found[0], files, reasons
    return set(), False, set(), reasons or ["No uniquely dated complete ISIN snapshot"]


def export(folder, sessions, *, require_complete=True):
    # Certification validates these dates again against the reconciled NSE calendar.
    receipts = ("acquisition.json", "daily-gsm-acquisition.json", "inventory-reconciliation.json")
    if require_complete:
        for name in receipts:
            if not (folder / name).is_file():
                raise ValueError("Acquisition still running: " + name)
    days = sorted(date.fromisoformat(f.name) for f in sessions.glob("????-??-??"))
    opens = dict(itertools.pairwise(days))
    selected = [d for d in days if START <= d <= END]
    source_rows = verified_inventory(folder) if require_complete else inventory(folder)
    monthly_sources = {}
    unresolved = set()
    receipt = folder / "inventory-reconciliation.json"
    if receipt.exists():
        reconciliations = json.loads(receipt.read_bytes())["months"]
        unresolved = {d for r in reconciliations for d in r["unresolved_daily_inventory"]}
        monthly_sources = {
            r["month"]: [x["file"] for x in r["observed_sources"]] for r in reconciliations
        }
    by_day = defaultdict(list)
    for r in source_rows:
        by_day[date.fromisoformat(r["cirDate"])].append(r)
    state = QuarterlyGSM(folder)
    i = 0
    global_counts = defaultdict(Counter)
    annual = defaultdict(Counter)
    coverage = []
    gaps = []
    raw_info = {}
    for day in selected:
        session = sessions / str(day)
        effective = opens[day]
        universe = members(session)
        ids = set(universe.values())
        first_gsm = date(2017, 2, 1)
        next_file = folder / "raw" / f"sec_list_{day:%d%m%Y}.csv"
        # Apply publications only through this day's EOD: weekend changes
        # must not reach a preceding Friday. A same-date daily GSM file takes priority.
        while i < len(source_rows) and date.fromisoformat(source_rows[i]["cirDate"]) <= day:
            publication = str(date.fromisoformat(source_rows[i]["cirDate"]))
            if publication in unresolved:
                state.stop("unreconciled official notice inventory on " + publication)
            else:
                state.apply(source_rows[i])
            i += 1
        used = set()
        info = {}
        flags = {}
        if day < GSM_FIRST:
            used.add("raw/" + GSM_START)
            flags[GSM] = dict.fromkeys(ids, False)
            info[GSM] = {
                "method": "not yet shortlisted; first list 2017-03-03, first monitoring 2017-03-14"
            }
        elif next_file.is_file():
            used.update({"raw/" + next_file.name, "raw/SURV34415.zip"})
            flags[GSM] = gsm_table(
                checked_raw(folder, next_file.name), dated_market_ids(session), ids
            )
            info[GSM] = {
                "method": "dated complete next-open price-band list, exact GSM remark and contemporaneous security identity"
            }
        elif state.pool is not None:
            flags[GSM] = {isin: False for isin in ids if isin not in state.pool}
            if state.anchor == GSM_START and effective == GSM_EFFECTIVE:
                flags[GSM].update({isin: True for isin in ids & state.initial})
            used.update(state.evidence)
            # The catalogue pages certify inventory as read through the cutoff.
            # Raw attachment publication dates are independently checked.
            if day >= first_gsm:
                used.add(f"catalogue/{day:%Y-%m}.json")
            info[GSM] = {
                "method": "complement of verified complete shortlist and all later classified notices through this close; timing-sensitive members remain unknown",
                "anchor": state.anchor,
            }
        else:
            flags[GSM] = {}
            info[GSM] = {
                "method": "unknown",
                "reason": "Daily complete price-band file unavailable; " + state.reason,
            }
        for scheme, launch, filename in ((LT, ASM_FIRST, ASM_START), (ST, ST_EFFECTIVE, ST_START)):
            not_live = day < launch if scheme == LT else effective < launch
            if not_live:
                flags[scheme] = dict.fromkeys(ids, False)
                used.add("raw/" + filename)
                info[scheme] = {
                    "method": "framework not yet introduced or effective",
                    "first_dedicated_shortlist_found": "2018-03-21"
                    if scheme == LT
                    else "2018-11-21",
                    "launch_effective": "2018-03-26" if scheme == LT else "2018-11-14",
                }
                continue
            if str(day) in unresolved:
                positives, complete, files, issues = (
                    set(),
                    False,
                    set(),
                    ["Unreconciled official notice inventory on " + str(day)],
                )
            else:
                positives, complete, files, issues = asm_on_day(
                    folder, scheme, by_day[day], effective
                )
            used.update(files)
            flags[scheme] = {
                isin: isin in positives for isin in ids if complete or isin in positives
            }
            info[scheme] = {
                "method": "dated consolidated annexure"
                if complete
                else "partial or missing dated consolidated annexure",
                "complete": complete,
                "reasons": issues,
            }
        for scheme in COMPONENTS:
            info[scheme]["known_members"] = len(flags[scheme])
            info[scheme]["positive_members"] = sum(flags[scheme].values())
            info[scheme]["unknown_members"] = len(ids) - len(flags[scheme])
            global_counts[scheme]["known"] += len(flags[scheme])
            global_counts[scheme]["unknown"] += len(ids) - len(flags[scheme])
        symbols = {}
        stats = Counter()
        for symbol, isin in universe.items():
            value = compose(flags[s].get(isin) for s in COMPONENTS)
            stats["unknown" if value is None else "flagged" if value else "clear"] += 1
            if value is not None:
                symbols[symbol] = {
                    "isin": isin,
                    "surveillance": value,
                    "floor": None,
                    "ceiling": None,
                }
        for name in tuple(used):
            if name.startswith("catalogue/"):
                used.update(monthly_sources.get(Path(name).stem, []))
        for name in used - raw_info.keys():
            path = folder / name
            raw = path.read_bytes()
            m = json.loads(path.with_suffix(path.suffix + ".source.json").read_bytes())
            if m["sha256"] != sha(raw):
                raise ValueError("Evidence source hash mismatch: " + name)
            raw_info[name] = m
        dst = folder / str(day)
        write_json(dst / "symbols.json", symbols)
        prior = json.loads((session / "manifest.json").read_bytes())["prior_day"]
        write_json(
            dst / "sources.json",
            {
                "prior": prior,
                "signal_close": str(day),
                "effective_next_open": str(effective),
                "clock": "NSE EOD release on the signal date; intraday publication time unverified",
                "components": info,
                "files": {n: raw_info[n] for n in sorted(used)},
                "symbols_hash": sha((dst / "symbols.json").read_bytes()),
            },
        )
        coverage.append(
            {
                "day": str(day),
                "sources_sha256": sha((dst / "sources.json").read_bytes()),
                "observations": dict(stats),
            }
        )
        missing = {s: info[s] for s in COMPONENTS if info[s]["unknown_members"]}
        if missing:
            gaps.append(
                {
                    "day": str(day),
                    "next_open": str(effective),
                    "unknown_composite": stats["unknown"],
                    "components": missing,
                }
            )
        annual[str(day.year)].update(stats)
    totals = Counter()
    for v in annual.values():
        totals.update(v)
    report = {
        "complete": require_complete,
        "from": str(selected[0]),
        "to": str(selected[-1]),
        "sessions": len(coverage),
        "observations": dict(totals),
        "by_year": dict(annual),
        "components": dict(global_counts),
        "days": coverage,
    }
    if require_complete:
        write_json(
            folder / "gaps.json", {"sessions_with_unknown_components": len(gaps), "days": gaps}
        )
        report["gap_receipt_sha256"] = sha((folder / "gaps.json").read_bytes())
        report["input_receipts_sha256"] = {
            name: sha((folder / name).read_bytes()) for name in receipts
        }
        write_json(folder / "coverage.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "days"}, indent=2), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=FOLDER)
    parser.add_argument("--sessions", type=Path, default=Path("data/sessions"))
    args = parser.parse_args()
    export(args.folder, args.sessions)
