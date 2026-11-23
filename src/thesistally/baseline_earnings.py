"""Reconcile the Stage 2 board-meeting calendar to dated security identities."""

import hashlib
import json
import re
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

import pandas as pd

from . import exchange, market, pilot_data

NSDL_ISSUER_SCHEME = (
    "https://nsdl.co.in/downloadables/pdf/Demat%20and%20Remat%20of%20Securities%2001062021.pdf"
)
DVR_ISSUER_EVIDENCE = [
    "https://nsearchives.nseindia.com/content/press/03112008.htm",
    "https://www.bseindia.com/bseplus/AnnualReport/500570/5005700312.pdf",
]
HISTORICAL_PURPOSE = re.compile(r"results|financial statements", re.IGNORECASE)
DAILY_PURPOSE = re.compile(
    r"financial\s+results?|quarterly\s+results?|financial\s+statements?", re.IGNORECASE
)


def issuer(isin):
    # The exchange's original listing and the 2012 annual report identify
    # both special DVR-format security numbers as Tata Motors share classes.
    if isin in {"IN9155A01012", "IN9155A01020"}:
        return "INE155A"
    return isin[:7] if re.fullmatch(r"INE[A-Z0-9]{8}[0-9]", isin) else None


def parse_meetings(raw, expression):
    notices = defaultdict(set)
    for row in json.loads(raw):
        if not expression.search(f"{row.get('bm_purpose') or ''} {row.get('bm_desc') or ''}"):
            continue
        when = datetime.strptime(row["bm_date"], "%d-%b-%Y").date()
        value = row.get("bm_timestamp")
        announced = datetime.strptime(value, "%d-%b-%Y %H:%M:%S") if value else None
        notices[when].add((announced, row.get("sm_isin") or "", row.get("bm_symbol") or ""))
    return notices


def match_notices(day, members, notices, past_members):
    by_isin = {isin: symbol for symbol, isin in members.items()}
    by_issuer = defaultdict(set)
    for symbol, isin in members.items():
        if code := issuer(isin):
            by_issuer[code].add(symbol)
    found, uncertain = {}, set()
    cutoff = datetime.combine(day, time(15, 45))
    # An exchange results meeting belongs to the issuer across its share series.
    # Exact security identifiers have priority when they refer to the same meeting.
    for days_ahead in range(31):
        when = day + timedelta(days=days_ahead)
        for stamp, isin, symbol in notices.get(when, ()):
            if stamp is not None and stamp > cutoff:
                continue
            eligible = {}
            if code := issuer(isin):
                eligible.update({s: 1 for s in by_issuer.get(code, ())})
            if isin in by_isin:
                eligible[by_isin[isin]] = 0
            if isin in ("", "-") and symbol in members:
                # Vintage quote confirms the same security at announcement and scan.
                prior = past_members(stamp.date()) if stamp else {}
                if prior.get(symbol) == members[symbol]:
                    eligible[symbol] = 2
                else:
                    uncertain.add(symbol)
            for listed_symbol, method in eligible.items():
                if stamp is None:
                    uncertain.add(listed_symbol)
                    continue
                row = (days_ahead, method, isin, symbol, stamp.isoformat())
                if listed_symbol not in found or row < found[listed_symbol]:
                    found[listed_symbol] = row
    return found, uncertain


def prepare(root, sessions):
    days = list(map(date.fromisoformat, sessions))
    start, end = days[0], days[-1]
    view = root / "baseline" / "market"
    dest = view / "sources" / "earnings-by-isin"
    dest.mkdir(parents=True, exist_ok=True)
    frame = pd.read_parquet(
        root / "universes" / "reconstructed.parquet", columns=["day", "symbol", "isin"]
    )
    members = {d: dict(zip(group.symbol, group["isin"])) for d, group in frame.groupby("day")}
    trading_days = sorted(members)

    def past_members(d):
        i = bisect_right(trading_days, d)
        return members[trading_days[i - 1]] if i else {}

    references = root / "sources" / "reference"
    evidence, annual = {}, defaultdict(set)
    for path in sorted(references.glob("meetings-????-??-??_????-??-??.json")):
        first, last = map(date.fromisoformat, path.stem.removeprefix("meetings-").split("_"))
        if last < start or first > end + timedelta(days=30):
            continue
        raw = path.read_bytes()
        meta = json.loads(path.with_suffix(".json.source.json").read_bytes())
        if market.sha(raw) != meta["sha256"]:
            raise market.MarketError(f"Corrupt board-meeting evidence {path.name}")
        evidence[str(path.relative_to(root))] = {"from": str(first), "to": str(last), **meta}
        for when, values in parse_meetings(raw, HISTORICAL_PURPOSE).items():
            annual[when].update(values)
    counts, by_year, methods, flips, signatures = (
        Counter(
            member_observations=0,
            unknown_member_observations=0,
            revised_days_to_meeting=0,
            blackout_added=0,
            blackout_removed=0,
        ),
        defaultdict(Counter),
        Counter(),
        [],
        hashlib.sha256(),
    )
    labels = ["security_isin", "nsdl_issuer", "dated_symbol"]
    for day in days:
        old = exchange.context_file(view, day)
        data = json.loads(old.read_bytes())
        if set(data["symbols"]) != set(members[day]):
            raise market.MarketError(f"Historical results membership differs on {day}")
        meetings = annual
        if "meetings.json" in data["sources"]:
            filename = old.parent / "meetings.json"
            raw = filename.read_bytes()
            meta = data["sources"]["meetings.json"]
            if market.sha(raw) != meta["sha256"]:
                raise market.MarketError(f"Corrupt daily board intimation file on {day}")
            evidence[f"context/{day}/meetings.json"] = meta
            meetings = parse_meetings(raw, DAILY_PURPOSE)
        found, uncertain = match_notices(day, members[day], meetings, past_members)
        changes = {}
        for symbol, current in data["symbols"].items():
            counts["member_observations"] += 1
            point = found.get(symbol)
            ahead = point[0] if point else None
            if point:
                methods[labels[point[1]]] += 1
            old_coverage = current.get("earnings_coverage", "known")
            coverage = "unknown" if symbol in uncertain else old_coverage
            if coverage == "unknown":
                counts["unknown_member_observations"] += 1
            prior = current["earnings_in_days"]
            counts["blackout_before_identity_audit"] += int(prior is not None and prior <= 5)
            counts["blackout_after_identity_audit"] += int(ahead is not None and ahead <= 5)
            if (prior, old_coverage) == (ahead, coverage):
                continue
            changes[symbol] = {"earnings_in_days": ahead, "earnings_coverage": coverage}
            if prior != ahead:
                counts["revised_days_to_meeting"] += 1
                before = prior is not None and prior <= 5
                after = ahead is not None and ahead <= 5
                if before != after:
                    reason = "blackout_added" if after else "blackout_removed"
                    counts[reason] += 1
                    by_year[str(day.year)][reason] += 1
                    flips.append(
                        {
                            "day": str(day),
                            "symbol": symbol,
                            "isin": members[day][symbol],
                            "before": prior,
                            "after": ahead,
                            "matched_notice": point,
                        }
                    )
        raw = json.dumps(changes, sort_keys=True).encode()
        payload = {
            "day": str(day),
            "member_sha256": market.sha(
                (view / "sessions" / str(day) / "members.csv").read_bytes()
            ),
            "changes": changes,
            "changes_sha256": market.sha(raw),
        }
        path = dest / f"{day}.json"
        pilot_data.write_json(path, payload)
        signatures.update(path.read_bytes())
    pilot_data.write_json(dest / "evidence.json", evidence)
    pilot_data.write_json(root / "baseline" / "earnings-blackout-changes.json", flips)
    return {
        "complete": True,
        "sessions": len(days),
        **counts,
        "match_methods": dict(methods),
        "added_blackouts_by_method": dict(
            Counter(
                labels[change["matched_notice"][1]]
                for change in flips
                if change["after"] is not None and change["after"] <= 5
            )
        ),
        "changes_by_year": dict(by_year),
        "source_sha256": market.sha((dest / "evidence.json").read_bytes()),
        "session_overlays_sha256": signatures.hexdigest(),
        "blackout_changes_sha256": market.sha(
            (root / "baseline" / "earnings-blackout-changes.json").read_bytes()
        ),
        "issuer_definition": NSDL_ISSUER_SCHEME,
        "dvr_issuer_sources": DVR_ISSUER_EVIDENCE,
    }
