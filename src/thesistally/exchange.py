"""Dated NSE surveillance, opening price bands, earnings notices and entitlements."""

from __future__ import annotations

import gzip
import json
import re
from datetime import date, datetime, time, timedelta

from .market import ARCHIVE, MarketError, csv_table, sha, staging

API = "https://www.nseindia.in/api"
AMOUNT = re.compile(
    r"(?:Rs\.?|Re\.?)\s*-?\s*(\d[\d,]*(?:\.\d+)?)\s*/?-?\s*(?:per|/-\s*per)\s*(?:equity\s+)?sh(?:are)?\b",
    re.IGNORECASE,
)


def settle_notices(info):
    dividend = info.get("dividend", 0.0)
    unresolved = []
    for subject in info.get("other_actions", []):
        if re.fullmatch(r"annual general meeting|buy[ -]?back", subject.strip(), re.IGNORECASE):
            continue  # The simulated account retains its shares in voluntary tenders.
        amounts = AMOUNT.findall(subject)
        if (
            "dividend" in subject.lower()
            and amounts
            and not re.search(r"bonus|split|rights|demerger", subject, re.IGNORECASE)
        ):
            dividend += sum(float(a.replace(",", "")) for a in amounts)
        else:
            unresolved.append(subject)
    return {**info, "dividend": dividend, "other_actions": unresolved}


def context_file(root, day):
    return root / "context" / str(day) / "context.json"


def collect(root, feed, day: date, prior: date, members):
    dest = context_file(root, day).parent
    if dest.exists():
        return read_context(root, day)
    surveillance = f"{ARCHIVE}/cm/REG_IND{day:%d%m%y}.csv"
    # The end-of-day master contains bands based on that day's close, valid
    # for the *next* opening. Read the previous trading session's master.
    security = f"{ARCHIVE}/cm/NSE_CM_security_{prior:%d%m%Y}.csv.gz"
    raw_flags = feed.get(surveillance).content
    raw_master = feed.get(security).content
    meeting_url = API + "/corporate-board-meetings"
    action_url = API + "/corporates-corporateActions"
    # Store known events for the maximum swing holding horizon. Rules can
    # independently choose a shorter exclusion window from this set.
    params = {
        "index": "equities",
        "from_date": day.strftime("%d-%m-%Y"),
        "to_date": (day + timedelta(days=30)).strftime("%d-%m-%Y"),
    }
    board_resp = feed.get(meeting_url, params=params)
    action_resp = feed.get(action_url, params={**params, "to_date": params["from_date"]})
    board, actions = board_resp.json(), action_resp.json()
    rows = decode(day, members, raw_flags, raw_master, board, actions)
    with staging(dest.parent) as tmp:
        sources = {}
        for filename, data, url in [
            ("surveillance.csv", raw_flags, surveillance),
            ("security.csv.gz", raw_master, security),
            ("meetings.json", board_resp.content, str(board_resp.url)),
            ("actions.json", action_resp.content, str(action_resp.url)),
        ]:
            (tmp / filename).write_bytes(data)
            sources[filename] = {"url": url, "sha256": sha(data)}
        data = {"day": str(day), "band_reference": str(prior), "sources": sources, "symbols": rows}
        raw = (json.dumps(data, indent=2) + "\n").encode()
        (tmp / "context.json").write_bytes(raw)
        (tmp / "context.sha256").write_text(sha(raw))
        try:
            tmp.rename(dest)
        except OSError:
            if not dest.is_dir():
                raise
    return read_context(root, day)


def read_context(root, day, verify_sources=False):
    path = context_file(root, day)
    if not path.exists():
        raise MarketError(
            f"Missing NSE surveillance and event data for {day}. Run radar sync or backfill."
        )
    raw = path.read_bytes()
    if sha(raw) != (path.parent / "context.sha256").read_text():
        raise MarketError(f"Invalid exchange context checksum for {day}.")
    data = json.loads(raw)
    if verify_sources:
        for name, source in data["sources"].items():
            if sha((path.parent / name).read_bytes()) != source["sha256"]:
                raise MarketError(f"Exchange source checksum failed for {name} in {day}.")
    overlay = root / "sources" / "exchange-controls" / str(day)
    if (overlay / "sources.json").is_file():
        manifest = json.loads((overlay / "sources.json").read_text())
        controls = (overlay / "symbols.json").read_bytes()
        if sha(controls) != manifest["symbols_hash"]:
            raise MarketError(f"Invalid controls for {day}")
        if verify_sources:
            for name, source in manifest["files"].items():
                if sha((overlay / name).read_bytes()) != source["sha256"]:
                    raise MarketError(f"Invalid {name} on {day}")
        for symbol, values in json.loads(controls).items():
            if symbol in data["symbols"]:
                data["symbols"][symbol].update(values)
    return {s: settle_notices(row) for s, row in data["symbols"].items()}


def decode(day, members, raw_flags, raw_master, board, actions):
    flags = csv_table(raw_flags)
    flags = flags[flags.Status == "A"].set_index(["Symbol", "Series"])
    flag_cols = [c for c in flags if c == "GSM" or "Term ASM)" in c]
    if len(flag_cols) != 3:
        raise MarketError("NSE surveillance file lacks ASM/GSM classifications.")
    master = csv_table(gzip.decompress(raw_master))
    master = master[master.TckrSymb.isin(members.symbol) & master.SctySrs.isin(members.series)]
    bands = dict(zip(zip(master.TckrSymb, master.SctySrs, master.ISIN), master.PricRg))
    flag_values = {
        key: any(v != "100" for v in row)
        for key, row in zip(flags.index, flags[flag_cols].itertuples(index=False, name=None))
    }
    if not isinstance(board, list) or not isinstance(actions, list):
        raise MarketError("NSE corporate-event response is incomplete.")
    earnings = {}
    for meeting in board:
        text = " ".join(str(meeting.get(k) or "") for k in ("bm_desc", "bm_purpose"))
        if not re.search(
            r"financial\s+results?|quarterly\s+results?|financial\s+statements?",
            text,
            re.IGNORECASE,
        ):
            continue
        announced = datetime.strptime(meeting["bm_timestamp"], "%d-%b-%Y %H:%M:%S")
        when = datetime.strptime(meeting["bm_date"], "%d-%b-%Y").date()
        if announced <= datetime.combine(day, time(15, 45)) and when >= day:
            symbol = meeting["bm_symbol"]
            earnings[symbol] = min(earnings.get(symbol, when), when)
    by_isin = {}
    for a in actions:
        if a.get("series") == "EQ" and datetime.strptime(a["exDate"], "%d-%b-%Y").date() == day:
            by_isin.setdefault(a["isin"], set()).add(a["subject"])
    rows = {}
    for m in members.itertuples(index=False):
        key = (m.symbol, m.series)
        flagged = flag_values.get(key, "unknown")
        band = bands.get((m.symbol, m.series, m.isin), "")
        bounds = re.fullmatch(r"([\d.]+)-([\d.]+)", str(band))
        dividend = 0.0
        unresolved = []
        for subject in sorted(by_isin.get(m.isin, [])):
            amounts = AMOUNT.findall(subject)
            if (
                "dividend" in subject.lower()
                and amounts
                and not re.search(r"bonus|split|rights|demerger", subject, re.IGNORECASE)
            ):
                dividend += sum(float(a.replace(",", "")) for a in amounts)
            else:
                unresolved.append(subject)
        rows[m.symbol] = {
            "surveillance": flagged,
            "earnings_in_days": (earnings[m.symbol] - day).days if m.symbol in earnings else None,
            "floor": float(bounds[1]) if bounds else None,
            "ceiling": float(bounds[2]) if bounds else None,
            "dividend": dividend,
            "other_actions": unresolved,
        }
    return rows


def historical_controls(root, days, members):
    """Preserve dated source files for available surveillance and next-open bands."""
    from concurrent.futures import ThreadPoolExecutor
    from itertools import pairwise

    import httpx

    groups = dict(tuple(members.groupby("day")))
    target = [(d, p) for p, d in pairwise(days) if d in groups and d >= date(2024, 7, 8)]
    errors = {}
    folder = root / "sources" / "exchange-controls"

    def one(pair):
        day, prior = pair
        dest = folder / str(day)
        if (dest / "sources.json").exists():
            return
        legacy = context_file(root, day).parent
        if (legacy / "surveillance.csv").is_file():
            return
        urls = {
            "surveillance.csv": f"{ARCHIVE}/cm/REG_IND{day:%d%m%y}.csv",
            "security.csv.gz": f"{ARCHIVE}/cm/NSE_CM_security_{prior:%d%m%Y}.csv.gz",
        }
        try:
            with staging(folder) as tmp:
                sources = {}
                for name, url in urls.items():
                    response = client.get(url)
                    if response.status_code == 404:
                        continue
                    response.raise_for_status()
                    data = response.content
                    (tmp / name).write_bytes(data)
                    sources[name] = {"url": url, "sha256": sha(data)}
                symbols = decode_controls(
                    groups[day],
                    (tmp / "surveillance.csv").read_bytes()
                    if "surveillance.csv" in sources
                    else None,
                    (tmp / "security.csv.gz").read_bytes()
                    if "security.csv.gz" in sources
                    else None,
                )
                raw = json.dumps(symbols, sort_keys=True).encode()
                (tmp / "symbols.json").write_bytes(raw)
                (tmp / "sources.json").write_text(
                    json.dumps({"files": sources, "symbols_hash": sha(raw), "prior": str(prior)})
                )
                tmp.rename(dest)
        except (httpx.HTTPError, ValueError, OSError, KeyError) as exc:
            return str(day), type(exc).__name__

    with (
        httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as client,
        ThreadPoolExecutor(max_workers=4) as pool,
    ):
        errors.update(x for x in pool.map(one, target) if x)
    return errors


def decode_controls(members, raw_flags, raw_master):
    flag_values = {}
    bands = {}
    if raw_flags is not None:
        flags = csv_table(raw_flags)
        flags = flags[flags.Status == "A"].set_index(["Symbol", "Series"])
        flag_cols = [c for c in flags if c == "GSM" or "Term ASM)" in c]
        if len(flag_cols) == 3:
            flag_values = {
                key: any(v != "100" for v in row)
                for key, row in zip(
                    flags.index, flags[flag_cols].itertuples(index=False, name=None)
                )
            }
    if raw_master is not None:
        master = csv_table(gzip.decompress(raw_master))
        bands = dict(zip(zip(master.TckrSymb, master.SctySrs, master.ISIN), master.PricRg))
    result = {}
    for row in members.itertuples():
        limits = re.fullmatch(
            r"([\d.]+)-([\d.]+)", bands.get((row.symbol, row.series, row.isin), "")
        )
        result[row.symbol] = {
            "surveillance": flag_values.get((row.symbol, row.series), "unknown"),
            "floor": float(limits[1]) if limits else None,
            "ceiling": float(limits[2]) if limits else None,
        }
    return result
