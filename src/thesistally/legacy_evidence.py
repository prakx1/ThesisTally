"""Read point-in-time GSM/ASM evidence at ISIN level, failing closed on ambiguous lists."""

from __future__ import annotations

import csv
import io
import posixpath
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .legacy_surveillance import ST

ISIN = re.compile(r"IN[A-Z0-9]{9}[0-9]\Z")


def verified_isin(value):
    """ISO 6166 check digit; never recover a missing ISIN from a current symbol."""
    if not ISIN.fullmatch(value):
        return False
    digits = "".join(str(int(c, 36)) for c in value)
    return (
        sum(sum(divmod(int(x) * (2 if i % 2 else 1), 10)) for i, x in enumerate(reversed(digits)))
        % 10
        == 0
    )


def workbooks(raw):
    with ZipFile(io.BytesIO(raw)) as archive:
        for member in archive.namelist():
            if member.lower().endswith(".xlsx") and not Path(member).name.startswith("~"):
                yield read_workbook(archive.read(member))
            elif member.lower().endswith(".xls") and not Path(member).name.startswith("~"):
                import xlrd

                book = xlrd.open_workbook(file_contents=archive.read(member), on_demand=True)
                try:
                    tables = {}
                    for sheet in book.sheets():
                        rows = []
                        for ri in range(sheet.nrows):
                            cells = {}
                            for ci, cell in enumerate(sheet.row(ri)):
                                if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                                    continue
                                value = cell.value
                                if isinstance(value, float) and value.is_integer():
                                    value = int(value)
                                if str(value).strip():
                                    cells[xlrd.colname(ci)] = str(value).strip()
                            if cells:
                                rows.append(cells)
                        tables[sheet.name] = rows
                    yield tables
                finally:
                    book.release_resources()


def read_workbook(raw):
    sheets = {}
    with ZipFile(io.BytesIO(raw)) as book:
        ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        rels = {
            r.get("Id"): r.get("Target")
            for r in ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        }
        strings = []
        if "xl/sharedStrings.xml" in book.namelist():
            strings = [
                "".join(t.text or "" for t in si.iterfind(".//{*}t")).strip()
                for si in ET.fromstring(book.read("xl/sharedStrings.xml"))
            ]
        for s in ET.fromstring(book.read("xl/workbook.xml")).iterfind(".//{*}sheet"):
            target = rels[s.get(f"{{{ns}}}id")]
            name = target[1:] if target.startswith("/") else posixpath.normpath("xl/" + target)
            rows = []
            for row in ET.fromstring(book.read(name)).iterfind(".//{*}sheetData/{*}row"):
                cells = {}
                for c in row.iterfind("{*}c"):
                    value = c.findtext("{*}v") or ""
                    typ = c.get("t")
                    if typ == "s" and value:
                        value = strings[int(value)]
                    elif typ == "inlineStr":
                        value = "".join(t.text or "" for t in c.iterfind(".//{*}t"))
                    if value.strip():
                        cells[re.match(r"[A-Z]+", c.get("r")).group()] = value.strip()
                if cells:
                    rows.append(cells)
            sheets[s.get("name")] = rows
    return sheets


DATE = r"(?:[a-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?[,\s]+\d{4}|\d{1,2}(?:st|nd|rd|th)?[\s.-]+[a-z]{3,9}[,\s.-]+\d{4}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4})"
EFFECTIVE = re.compile(
    r"(?:w\s*\.?\s*e\s*\.?\s*f\s*\.?|with effect from|effective from|effective date\s*[:=]?)\s*(?:from\s*)?(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day[,\s]+(?:i\.e\.\s*)?)?("
    + DATE
    + ")",
    re.IGNORECASE,
)


def effective_dates(text):
    values = set()
    for match in EFFECTIVE.finditer(text):
        string = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", match[1], flags=re.IGNORECASE)
        string = " ".join(re.sub(r"[,./-]", " ", string).split())
        for fmt in ("%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y", "%d %m %Y"):
            try:
                values.add(datetime.strptime(string, fmt).date())
                break
            except ValueError:
                continue
    return values


def flatten(sheets):
    return "\n".join(value for rows in sheets.values() for row in rows for value in row.values())


def parse_table(rows):
    """Prove completeness from the ISIN column, numbered rows and explicit NIL tables."""
    ids = set()
    bad = []
    nil = False
    isin_col = number_col = symbol_col = None
    numbers = []
    for row in rows:
        head = {re.sub(r"[^a-z0-9]+", "", v.lower()): k for k, v in row.items()}
        if "isin" in head:
            if numbers and sorted(numbers) != list(range(1, max(numbers) + 1)):
                bad.append("serial gap")
            numbers = []
            isin_col = head["isin"]
            symbol_col = head.get("symbol")
            number_col = next(
                (head[k] for k in ("srno", "sn", "sno", "sr", "serialno", "srnum") if k in head),
                None,
            )
            if not number_col:
                bad.append("missing serial-number header")
            continue
        if isin_col is None:
            continue
        value = row.get(isin_col, "").upper()
        is_nil = any(v.strip(".- ").lower() in {"nil", "none"} for v in row.values())
        nil |= is_nil
        num = row.get(number_col, "").strip(". ")
        # Some legacy annexures contain an asterisk-numbered explanatory
        # footnote spanning only the first two columns. It is not a security.
        if (
            re.fullmatch(r"\*+", num)
            and set(row) == {number_col, symbol_col}
            and len(row[symbol_col].split()) > 3
        ):
            continue
        if verified_isin(value):
            ids.add(value)
            if num.isdigit():
                numbers.append(int(num))
            else:
                bad.append("unnumbered ISIN")
        elif (
            num.isdigit()
            or ISIN.fullmatch(value)
            or ((value or (symbol_col in row and len(row) > 1)) and not is_nil and len(row) >= 2)
        ):
            bad.append("unresolved data row")
    if numbers and sorted(numbers) != list(range(1, max(numbers) + 1)):
        bad.append("serial gap")
    return ids, bool(isin_col and (ids or nil) and not bad), sorted(set(bad))


def positive_changes(sheets, scheme, effective):
    ids = set()
    for name, rows in sheets.items():
        if "consolidated" in name.lower():
            continue
        group = []
        valid = False
        for row in [*rows, {"A": "List of securities"}]:
            heading = " ".join(row.values()).lower()
            if heading.startswith(("list of securit", "securities ")):
                if valid:
                    ids.update(parse_table(group)[0])
                group = []
                standard = not re.search(
                    r"insolvency|resolution process|inter[- ]creditor|\b(?:ica|irp|ibc|sme)\b",
                    heading,
                )
                short = bool(re.search(r"short\s*[- ]?\s*term|\bst[- ]?asm", heading))
                valid = (
                    "shortlisted" in heading
                    and "asm" in heading
                    and standard
                    and (scheme == ST) == short
                    and effective_dates(heading) == {effective}
                )
            elif valid:
                group.append(row)
    return ids


def asm_status(raw, component, effective):
    snapshots = []
    additions = set()
    for sheets in workbooks(raw):
        # Dates of action in an unrelated IBC/ICA tab can differ. Verify the
        # general-list headings and their companion general applicability annexures only.
        public = {
            k: v
            for k, v in sheets.items()
            if not re.search(r"\b(?:IBC|ICA|IRP)\b", k, re.IGNORECASE)
        }
        additions.update(positive_changes(public, component, effective))
        dates = effective_dates(flatten(public))
        if dates != {effective}:
            continue
        for rows in public.values():
            heading = " ".join(value for row in rows[:3] for value in row.values()).lower()
            if "consolidated" not in heading or "asm" not in heading:
                continue
            if any(
                s in heading
                for s in (
                    "insolvency",
                    "resolution process",
                    "inter-creditor",
                    "creditor agreement",
                    "ica framework",
                    "small and medium",
                    "sme segment",
                )
            ):
                continue
            is_short = bool(
                re.search(r"\b(?:short\s*[- ]?\s*term|st\s*[- ]?\s*asm)\b", heading, re.IGNORECASE)
            )
            # More recent ST notices use a generic consolidated tab. Require
            # corroboration in that same workbook's dated ST annexures.
            if component == ST and not is_short:
                other_headings = " ".join(
                    value
                    for sheet in public.values()
                    for row in sheet[:2]
                    for value in row.values()
                )
                is_short = bool(
                    re.search(r"short\s*[- ]?\s*term\s+asm", other_headings, re.IGNORECASE)
                )
            if (component == ST) != is_short:
                continue
            snapshots.append(parse_table(rows))
    if len(snapshots) == 1:
        return snapshots[0]
    return additions, False, ["no uniquely dated consolidated ISIN list"]


def gsm_table(raw, dated_ids, member_ids):
    """Match NSE's daily next-session GSM remark by ISIN and dated symbol/series."""
    by_symbol = defaultdict(set)
    for (sym, series), isin in dated_ids.items():
        if isin in member_ids:
            by_symbol[sym].add(isin)
    status = defaultdict(set)
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))):
        symbol, series = row["Symbol"].strip(), row["Series"].strip()
        # Some ASM/GSM stocks move from EQ to BE/BZ for the very next
        # session. An actual same-day traded symbol with the same ISIN can
        # establish that transition within the common-equity series family.
        isin = dated_ids.get((symbol, series))
        if isin is None and series in {"EQ", "BE", "BZ"} and len(by_symbol[symbol]) == 1:
            alternatives = {
                v for (s, ss), v in dated_ids.items() if s == symbol and ss in {"EQ", "BE", "BZ"}
            }
            if len(alternatives) == 1:
                isin = next(iter(alternatives))
        if isin not in member_ids:
            continue
        remark = row["Remarks"].strip().upper()
        flag = (
            True
            if re.fullmatch(r"GSM\s*-?\s*STAGE\s*[-:]?\s*(?:[0-6]|I{1,3}|IV|V|VI)", remark)
            else False
            if remark == "-"
            else None
        )
        status[isin].add(flag)
    return {
        i: next(iter(flags)) for i, flags in status.items() if len(flags) == 1 and None not in flags
    }


def compose(components):
    components = tuple(components)
    if len(components) != 3:
        raise ValueError("All three surveillance frameworks must be assessed")
    if any(x is True for x in components):
        return True
    if all(x is False for x in components):
        return False
    return None
