"""Distinguish separately submitted standalone and consolidated result documents."""

import re

SCOPE = re.compile(r"\b(standalone|consolidated)\b", re.IGNORECASE)
SUBMITS = re.compile(
    r"\b(?:has submitted|has furnished|submitted to the exchange)\b.{0,125}?\b(?:standalone|consolidated)\b.{0,30}?\bfinancial results\b",
    re.IGNORECASE,
)


def declared_other(notice, basis):
    body = notice.get("text") or ""
    if not SUBMITS.search(body):
        return None
    scopes = {s.lower() for s in SCOPE.findall(body)}
    return next(iter(scopes)) if len(scopes) == 1 and basis not in scopes else None


def corroborated_other(notice, documents, basis):
    other = declared_other(notice, basis)
    if not other or not documents:
        return False
    # Keep unreadable scans ambiguous. Require source text on every non-sentinel
    # page; even a mention of the requested accounting scope prevents exclusion.
    all_scopes = set()
    for d in documents:
        pages = list(d["pages"])
        if pages and not pages[-1].strip():
            pages.pop()
        if not pages or any(len(page.strip()) < 100 for page in pages):
            return False
        all_scopes.update(x.lower() for page in pages for x in SCOPE.findall(page))
    return all_scopes == {other}
