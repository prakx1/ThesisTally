"""Dated evidence for live paper calls, using the same release and rights arithmetic as replay."""

import json
from datetime import date

import httpx

from . import conventions, market
from .convention_market import master, rights_issue, verified_close
from .pilot_data import write_json


def end_master(raw, day):
    folder = raw / str(day)
    manifest = folder / "sources.json"
    if manifest.is_file():
        return folder
    url = f"{market.ARCHIVE}/cm/NSE_CM_security_{day:%d%m%Y}.csv.gz"
    response = httpx.get(url, timeout=25, follow_redirects=True)
    if response.status_code == 404 or not response.content.strip():
        return None
    response.raise_for_status()
    if not response.content.startswith(bytes.fromhex("1f8b")):
        return None
    folder.mkdir(parents=True, exist_ok=True)
    with market.staging(folder) as tmp:
        (tmp / "security.csv.gz").write_bytes(response.content)
        (tmp / "security.csv.gz").replace(folder / "security.csv.gz")
    write_json(
        manifest,
        {"files": {"security.csv.gz": {"url": url, "sha256": market.sha(response.content)}}},
    )
    return folder


def prepare(root, day, members, following, *, session_folder=None):
    """Quarantine unknown returns; retry unpublished same-day evidence on later invocations."""
    session = session_folder or root / "sessions" / str(day)
    archive = json.loads((session / "manifest.json").read_bytes())
    ctx = root / "context" / str(day)
    context = json.loads((ctx / "context.json").read_bytes())
    sources = context["sources"]
    dest = root / "sources/forward-conventions"
    dest.mkdir(parents=True, exist_ok=True)
    prior = date.fromisoformat(archive["prior_day"])
    if not (ctx / "actions.json").is_file() or "actions.json" not in sources:
        raise market.MarketError(f"No dated forward corporate-action source for {day}")
    notices = (ctx / "actions.json").read_bytes()
    if market.sha(notices) != sources["actions.json"]["sha256"]:
        raise market.MarketError(f"Changed corporate-action notices for {day}")
    raw = (session / "bhavcopy.csv").read_bytes()
    source = {
        "url": f"{market.ARCHIVE}/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip",
        "member_sha256": market.sha(raw),
    }
    if (session / "prior-bhavcopy.csv").is_file():
        previous = (session / "prior-bhavcopy.csv").read_bytes()
    else:
        previous = (root / "sessions" / str(prior) / "bhavcopy.csv").read_bytes()
    isins = set(members["isin"]) | {c["isin"] for c in following}
    from . import corporate

    registry = corporate.actions(root)
    # Include quotes for the successor of any suspended or removed holding.
    for old in tuple(isins):
        term = registry.get(old)
        while (
            term
            and term["suspended"] <= str(day)
            and (new := term.get("successor_isin"))
            and new not in isins
        ):
            isins.add(new)
            term = registry.get(new)

    def equities(blob):
        frame = market.csv_table(blob)
        result = frame.loc[
            frame.ISIN.isin(isins) & frame.SctySrs.str.strip().isin(["EQ", "BE", "BZ"])
        ]
        if result.ISIN.duplicated().any():
            raise market.MarketError("Ambiguous tradable series in forward convention inputs")
        return result.set_index("ISIN")

    old = equities(previous)
    now = equities(raw)
    # A repeat of another run on this date can have a different off-index holding.
    # Preserve earlier evidence and add or resolve only records checked in this pass.
    events = dict(conventions.dated(root, day))
    capital = set()
    for r in json.loads(notices):
        isin = r.get("isin")
        if (
            isin not in old.index
            or isin not in now.index
            or r.get("series") != "EQ"
            or r.get("exDate") != day.strftime("%d-%b-%Y")
        ):
            continue
        subject = r.get("subject", "")
        if any(
            s in subject.lower()
            for s in ("split", "bonus", "rights", "consolidation", "demerger", "dividend")
        ):
            capital.add(isin)
        parsed = rights_issue(
            day,
            isin,
            subject,
            r.get("faceVal"),
            float(old.loc[isin].ClsPric),
            sources["actions.json"],
        )
        if parsed:
            item = events.setdefault(isin, {"symbol": now.loc[isin].TckrSymb})
            if "rights_issue" in item and item["rights_issue"] != parsed:
                raise market.MarketError(f"Multiple dated rights notices for {isin}")
            item["rights_issue"] = parsed
    for isin in sorted((set(old.index) & set(now.index)) - capital):
        row = now.loc[isin]
        close, prior_close = float(row.ClsPric), float(old.loc[isin].ClsPric)
        if not market.is_strict_move(close, prior_close):
            continue
        symbol, series = row.TckrSymb, row.SctySrs.strip()
        issue = {
            "isin": isin,
            "symbol": symbol,
            "day": str(day),
            "close": close,
            "prior_close": prior_close,
        }
        release = None
        try:
            opened = master(ctx, prior, isin, symbol, series)
            final = end_master(dest / "raw", day) if opened else None
            if final:
                band = master(final, day, isin, symbol, series)
                release = verified_close(issue, prior, row, source, opened, band, dest)
        except httpx.HTTPError as error:
            issue["unavailable"] = type(error).__name__
        events[isin] = {
            "symbol": symbol,
            **({"quarantine_release": release} if release else {"unverified_jump": issue}),
        }
    write_json(
        dest / f"{day}.json",
        {
            "version": conventions.VERSION,
            "day": str(day),
            "events": events,
            "events_sha256": market.sha(json.dumps(events, sort_keys=True).encode()),
        },
    )
