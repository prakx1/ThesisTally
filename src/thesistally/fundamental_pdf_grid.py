"""Corroborate first-quarter figures when dated PDF headings span several lines."""

import re
from datetime import date, datetime, timedelta

from .fundamental_pdf_match import (
    DURATION,
    FIRST_FIELD,
    _candidate,
    _classify,
    _legal_header,
    _norm,
)

_CELL_NUMBER = re.compile(r"\s*([(-]?\d[\d,.]*\)?)\s*(?=\s|$)")
_QUARTER_HEADING = re.compile(r"(?:\bquarter(?:ly)?\b|\b(?:three|3)\s+months?\b)", re.IGNORECASE)
_DATE = re.compile(
    r"\b(?:\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{4}|"
    r"\d{1,2}[\s/-]+[a-z]{3,9}[\s,/-]+\d{4}|"
    r"[a-z]{3,9}[\s/-]+\d{1,2}[\s,/-]+\d{4})\b",
    re.IGNORECASE,
)


def _printed_date(tokens):
    dates = set()
    for hit in _DATE.finditer(" ".join(tokens)):
        text = " ".join(re.sub(r"[/.,-]", " ", hit[0]).split())
        for fmt in ("%d %m %Y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
            try:
                dates.add(datetime.strptime(text, fmt).date())
                break
            except ValueError:
                continue
    return dates.pop() if len(dates) == 1 else None


def _quarter_grid(filing, lines):
    for index, line in enumerate(lines):
        cell = FIRST_FIELD.match(line)
        if cell and _classify(_norm(cell[1]), "") in {"revenue", "bank_income"}:
            break
    else:
        return None
    if not _legal_header(filing, lines[:index]):
        return None
    # Infer the boundaries from the positions of two printed monetary cells,
    # independently of the figures in the structured corroborating statement.
    second = _CELL_NUMBER.match(line, cell.end(2))
    if not second or not re.fullmatch(r"[(-]?\d[\d,.]*\)?", cell[2].strip()):
        return None
    first_x = cell.start(2) + len(cell[2].strip()) / 2
    second_x = (second.start(1) + second.end(1)) / 2
    radius = (second_x - first_x) / 2
    if radius < 4:
        return None
    expected = date.fromisoformat(filing["period_end"])
    for row, header in enumerate(lines[:index]):
        duration = DURATION.search(header)
        if not duration or not _QUARTER_HEADING.fullmatch(duration[0]):
            continue
        # Table heading only. The issuer's prose title may also say "quarter".
        lead = _norm(header[: duration.start()])
        if lead and not re.fullmatch(r"(?:(?:s[lir]|sr)no[i]?)?(?:particulars)?(?:forthe)?", lead):
            continue
        dates = []
        for x in (first_x, second_x):
            tokens = [
                word[0]
                for line in lines[row:index]
                for word in re.finditer(r"\S+", line)
                if x - radius <= (word.start() + word.end()) / 2 < x + radius
            ]
            dates.append(_printed_date(tokens))
        if (
            dates[0] == expected
            and dates[1]
            and expected - timedelta(days=370) <= dates[1] < expected
        ):
            return (expected, (first_x - radius, first_x + radius)), dates[1]
    return None


def corroborate_grid(filing, documents):
    """Require the issuer, basis, first two column dates and exact agreement on all four facts."""
    if any(filing.get(k) is None for k in ("eps", "revenue", "operating_profit", "net_profit")):
        return None
    for doc in documents:
        for index, page in enumerate(doc["pages"], 1):
            if "financial results" not in page.lower():
                continue
            grid = _quarter_grid(filing, page.splitlines())
            if grid and (proof := _candidate(filing, page, dated_column=grid[0])):
                return {
                    **proof,
                    "method": "issuer_two_dated_columns_and_four_structured_matches",
                    "pdf_sha256": doc["pdf_sha256"],
                    "page": index,
                    "first_column_bounds": list(grid[0][1]),
                    "comparison_column_date": grid[1].isoformat(),
                }
    return None
