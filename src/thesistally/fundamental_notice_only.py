"""Find first-release statements where an issuer has no quarterly structured catalogue row."""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date

from .fundamental_notice_proof import (
    RESULT,
    REVISION,
    UNRELATED,
    firsts,
    period_match,
    prospective_notice,
)
from .fundamental_nse import members


def supplement(plans, notices):
    existing = set(firsts(plans))
    names = defaultdict(list)
    for p in plans:
        m = p["metadata"]
        name = m.get("companyName") or m.get("cmName")
        if name:
            names[p["isin"]].append((p["filed_at"], name, m.get("bank")))
    intervals = defaultdict(list)
    for i in members():
        intervals[i["symbol"], i["isin"]].append(i)
    result = []
    for symbol, items in notices.items():
        for n in sorted(items, key=lambda x: x["announced_at"]):
            text = n["text"]
            inline = (
                n.get("release_language") == "legacy_results_summary"
                and n.get("declared_duration") == "quarter"
            )
            if (
                not (inline or RESULT.search(n["description"] + " " + text))
                or UNRELATED.search(n["description"])
                or REVISION.search(text)
                or (not inline and prospective_notice(n["description"], text))
            ):
                continue
            isin = n.get("isin")
            if not intervals[symbol, isin]:
                continue
            published = date.fromisoformat(n["announced_at"][:10])
            for year in (published.year - 1, published.year):
                for month in (3, 6, 9, 12):
                    quarter = date(year, month, calendar.monthrange(year, month)[1])
                    if quarter >= published or not (
                        n.get("declared_period_end") == str(quarter)
                        if inline
                        else period_match(text, quarter)
                    ):
                        continue
                    end = quarter.isoformat()
                    if not any(
                        i["from_date"] <= end <= i["to_date"] for i in intervals[symbol, isin]
                    ):
                        continue
                    earlier = [x for x in names[isin] if x[0] <= n["announced_at"]]
                    prior = max(earlier, default=(None, None, None))
                    legal = n.get("issuer_name") or prior[1]
                    if not legal and not inline:
                        continue
                    # A positively identified results statement/quarterly summary can carry
                    # an exchange-attested reporting basis. Unknown or mixed submissions
                    # remain candidates for both accounting statements.
                    declared = n.get("declared_basis")
                    if inline and declared not in {"standalone", "consolidated"}:
                        continue
                    for basis in (
                        [declared]
                        if declared in {"consolidated", "standalone"}
                        else ["consolidated", "standalone"]
                    ):
                        key = isin, end, basis
                        if key in existing:
                            continue
                        existing.add(key)
                        result.append(
                            {
                                "id": f"notice/{symbol}/{isin}/{end}/{basis}/{n['sequence']}",
                                "symbol": symbol,
                                "isin": isin,
                                "identity": "exchange_and_membership",
                                "basis": basis,
                                "period_end": end,
                                "filed_at": n["announced_at"],
                                "source_url": None,
                                "catalogue_url": n["catalogue_url"],
                                "metadata": {"companyName": legal, "bank": prior[2], "isin": isin},
                            }
                        )
    return result
