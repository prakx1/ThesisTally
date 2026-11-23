"""Read period language as candidate evidence for an issuer's dated original statements."""

import calendar
import re
from datetime import date

MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
SEP = r"[-/.,\s]+"
DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
YEAR = r"(20\d{2})"
END = re.compile(
    r"\b(?:quarter|period|year|months)\s+end(?:ed|ing)\b\s*(?:(?:on|as\s+at|as\s+on)\s+)?",
    re.IGNORECASE,
)
DMY = re.compile(DAY + SEP + "(" + MONTH + ")" + SEP + YEAR + r"\b", re.IGNORECASE)
MDY = re.compile("(" + MONTH + ")" + SEP + DAY + SEP + YEAR + r"\b", re.IGNORECASE)
NUMERIC = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b")
ISO = re.compile(r"(20\d{2})-(\d{2})-(\d{2})\b")
FY_RANGE = re.compile(r"\b(20\d{2})\s*[-–—/]\s*['’]?(\d{4}|\d{2})\b")
ANNUAL_ACCOUNTS = re.compile(
    r"\b(?:approv(?:al|ed)|adopt(?:ion|ed))\s+(?:of\s+)?(?:the\s+)?(?:audited\s+)?annual\s+accounts\b",
    re.IGNORECASE,
)
MONTHS = {calendar.month_abbr[i].lower(): i for i in range(1, 13)}


def normalize(text):
    # Extracted public accessibility text sometimes separates the two digits of
    # a printed day. Rejoin only before a month/date delimiter.
    return re.sub(
        r"\b([0-3])\s+([0-9])(?=(?:st|nd|rd|th)?\s*[-/.,]?\s*" + MONTH + r"\b)",
        r"\1\2",
        text,
        flags=re.IGNORECASE,
    )


def stated_ends(text):
    dates = []
    text = normalize(text)
    for marker in END.finditer(text):
        tail = text[marker.end() :]
        try:
            if hit := DMY.match(tail):
                d, m, y = hit.groups()
                dates.append(date(int(y), MONTHS[m[:3].lower()], int(d)))
            elif hit := MDY.match(tail):
                m, d, y = hit.groups()
                dates.append(date(int(y), MONTHS[m[:3].lower()], int(d)))
            elif hit := ISO.match(tail):
                y, m, d = map(int, hit.groups())
                dates.append(date(y, m, d))
            elif hit := NUMERIC.match(tail):
                d, m, y = map(int, hit.groups())
                dates.append(date(y if y >= 100 else 2000 + y, m, d))
            else:
                dates.append(None)
        except ValueError:
            dates.append(None)
    return dates


def explicit_end_dates(text):
    return {day for day in stated_ends(text) if day}


def refers_only_to_other_ends(text, wanted):
    ends = stated_ends(text)
    return bool(ends) and all(day and day != wanted for day in ends)


def fiscal_year_candidate(text, end):
    # Financial-year shorthand only proposes the probable March closing quarter.
    # All numerical proof still requires the actual date in the original statement.
    if (end.month, end.day) != (3, 31):
        return False
    clause = re.search(
        r"\b(?:quarter\s*(?:and|&)\s*(?:the\s+)?(?:financial\s+)?year\s+end(?:ed|ing)|(?:fourth\s+quarter|q4)\s*(?:of\s+)?(?:the\s+)?(?:fy|financial\s+year))\s*(?:on\s+)?(?:fy\s*)?",
        text,
        re.IGNORECASE,
    )
    if not clause:
        return False
    period = text[clause.end() : clause.end() + 18].lstrip()
    match = FY_RANGE.match(period)
    if match:
        start, short = match.groups()
        last = int(short) if len(short) == 4 else int(start[:2] + short)
        return int(start) + 1 == last == end.year
    return False
