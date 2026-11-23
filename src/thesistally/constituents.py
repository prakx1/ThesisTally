"""Reconstruct dated memberships and reconcile identities to published index totals."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

from . import history, market
from .reconstitutions import rebalance_events, weight_anchors


def load_daily(root, day):
    raw_dir = root / "sources" / "nse-daily" / str(day)
    old = root / "sessions" / str(day)
    if raw_dir.is_dir():
        raw = history.extract_bhav((raw_dir / "bhavcopy.zip").read_bytes())
        index = raw_dir / "indices.csv"
    elif (old / "bhavcopy.csv").is_file():
        raw = (old / "bhavcopy.csv").read_bytes()
        index = old / "indices.csv"
    else:
        raise market.MarketError("official daily equity or index file unavailable")
    bhav = history.normalize_bhav(raw, day)
    idx = market.csv_table(index.read_bytes())
    dates = idx["Index Date"].str.strip().str.replace("/", "-", regex=False)
    idx = idx[dates == day.strftime("%d-%m-%Y")].copy()
    idx["name"] = (
        idx["Index Name"]
        .str.upper()
        .str.strip()
        .replace({"S&P CNX NIFTY": "NIFTY 50", "CNX NIFTY": "NIFTY 50", "CNX 200": "NIFTY 200"})
    )
    return bhav, idx.set_index("name")


def day_table(root, day):
    bhav, idx = load_daily(root, day)
    quote = idx.loc["NIFTY 200"]
    frame = bhav[bhav.SctySrs.str.strip().isin(["EQ", "BE", "BZ"])].copy()
    frame = frame.rename(
        columns={
            "TckrSymb": "symbol",
            "ISIN": "isin",
            "SctySrs": "series",
            "TtlTradgVol": "volume",
            "TtlTrfVal": "value",
            "TOTTRDVAL": "value",
        }
    )
    for col in ("value", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    frame = frame[["symbol", "isin", "series", "volume", "value"]].set_index("symbol")
    volume = int(float(quote["Volume"]))
    printed = quote["Turnover (Rs. Cr.)"].strip()
    turnover = float(printed)
    reference = root / "sources" / "index-turnover" / str(day)
    if (reference / "source.json").exists():
        raw = (reference / "response.json").read_bytes()
        if market.sha(raw) != json.loads((reference / "source.json").read_text())["sha256"]:
            raise market.MarketError(f"Index turnover source checksum failed on {day}")
        rows = json.loads(raw)["data"]
        precision = len(printed.partition(".")[2])
        if (
            len(rows) == 1
            and pd.Timestamp(rows[0]["EOD_TIMESTAMP"]).date() == day
            and int(rows[0]["HIT_TRADED_QTY"]) == volume
        ):
            precise = float(rows[0]["HIT_TURN_OVER"])
            if abs(precise - turnover) <= 0.5 * 10 ** (-precision) + 1e-9:
                turnover = precise
    return frame, volume, turnover


def anchors(root):
    out = {}
    for day, (symbols, proof) in weight_anchors(root).items():
        stamp = pd.Timestamp(day).strftime("%b%Y")
        url = f"https://www.niftyindices.com/Indices_-_Market_Capitalisation_and_Weightage/indices_data{stamp}.zip"
        out[day] = (symbols, url)
    for p in sorted((root / "universes").glob("????-??-??_????-??-??")):
        info = json.loads((p / "source.json").read_text())
        if info["start"] == info["end"] and info["source"] == market.UNIVERSE_URL:
            members = market.read_members((p / "members.csv").read_bytes())
            out[date.fromisoformat(info["start"])] = (
                dict(zip(members.symbol, members.sector)),
                info["source"],
            )
    return out


def reconcile_members(frame, volumes, turnover, symbols, ids):
    # Names follow each historical ISIN through official daily security masters.
    mapping = dict(zip(frame["isin"], frame.index))
    syms = [s if s in frame.index else mapping.get(ids.get(s)) for s in symbols]
    if None in syms or len(set(syms)) != len(symbols):
        return None
    rows = frame.loc[syms]
    if len(rows) != len(symbols) or rows["isin"].duplicated().any():
        return None
    if int(rows.volume.sum()) != volumes or abs(rows.value.sum() / 1e7 - turnover) > 0.0051:
        return None
    return rows


def single_change(frame, volume, turnover, symbols, ids, evidenced):
    """Resolve a single change only when a unique official-listed candidate matches both totals."""
    mapping = dict(zip(frame["isin"], frame.index))
    present = {s if s in frame.index else mapping.get(ids.get(s)) for s in symbols}
    present.discard(None)
    if len(symbols) - len(present) > 1:
        return None
    existing = frame.loc[list(present)]
    if len(existing) != len(present):
        return None
    other = frame[~frame.index.isin(present) & frame.index.isin(evidenced)]
    by_volume = defaultdict(list)
    for sym, qty in zip(other.index, other.volume):
        by_volume[int(qty)].append(sym)
    missing = volume - int(existing.volume.sum())
    delta = turnover * 1e7 - existing.value.sum()
    removals = [(None, 0, 0.0)]
    if len(present) == len(symbols):
        removals.extend(existing[["volume", "value"]].itertuples(name=None))
    possibilities = []
    for removed, qty, value in removals:
        if (
            removed is not None
            and len(present) > 200
            and missing + qty == 0
            and abs(value + delta) <= 51000
        ):
            possibilities.append(existing.drop(index=removed))
        for added in by_volume.get(missing + qty, []):
            item = other.loc[added]
            if isinstance(item, pd.DataFrame):
                continue
            if abs(item.value - value - delta) > 51000:
                continue
            candidate = (present - {removed}) | {added}
            if len(candidate) < 200:
                continue
            verified = reconcile_members(frame, volume, turnover, candidate, {})
            if verified is not None:
                possibilities.append(verified)
    return possibilities[0] if len(possibilities) == 1 else None


def include_unlisted(root, days, rows):
    """Identify demerged index placeholders retrospectively by their first official traded ISIN."""
    spans = json.loads(
        (Path(__file__).with_name("revisions") / "unlisted-constituents.json").read_text()
    )
    missing = []
    for alias, symbol, entry, listing, key in spans:
        period = [d for d in days if entry <= str(d) < listing]
        if not period:
            continue
        notice = root / "sources" / "reference" / "notices" / (key + ".json")
        try:
            meta = json.loads(notice.read_text())
            if market.sha(notice.with_suffix(".pdf").read_bytes()) != meta["sha256"]:
                raise market.MarketError("Notice checksum mismatch")
            raw, _ = load_daily(root, date.fromisoformat(listing))
            ids = raw.loc[
                raw.TckrSymb.eq(symbol) & raw.SctySrs.str.strip().isin(["EQ", "BE", "BZ"]), "ISIN"
            ].unique()
            if len(ids) != 1:
                raise market.MarketError("Missing unique dated listing ISIN")
            rows.append(
                {
                    "symbol": alias,
                    "isin": ids[0],
                    "from_date": str(period[0]),
                    "to_date": str(period[-1]),
                    "source_url": meta["url"],
                }
            )
        except (ValueError, OSError, KeyError) as exc:
            missing.append(
                {"symbol": alias, "from": str(period[0]), "to": str(period[-1]), "reason": str(exc)}
            )
    return missing


def reconstruct(root, days, progress=print):
    official = anchors(root)
    changes = rebalance_events(root / "sources" / "reference" / "notices")
    evidence = {}
    for day, (members, url) in sorted(official.items()):
        for symbol in members:
            evidence[symbol] = url
    for _, added, removed, url in changes:
        for symbol in added | removed:
            evidence[symbol] = url
    for path in sorted((root / "sources" / "reference" / "notices").glob("*.txt")):
        url = json.loads(path.with_suffix(".json").read_text())["url"]
        for word in re.findall(
            r"(?<![A-Za-z0-9])[A-Z][A-Z0-9&.-]{2,}(?![A-Za-z0-9])", path.read_text()
        ):
            evidence.setdefault(word, url)
    verified = {}
    gaps = {}
    trusted_days = sorted(official)
    for ordered in (days, list(reversed(days))):
        last_set = None
        identities = {}
        for n, d in enumerate(ordered):
            if n and n % 500 == 0:
                progress(f"Checked {n}/{len(days)} dates in this pass, {len(verified)} verified")
            if d in verified:
                frame, source = verified[d]
                last_set = (d, set(frame.index), source)
                identities.update(zip(frame.index, frame["isin"]))
                continue
            try:
                frame, volume, turnover = day_table(root, d)
            except (ValueError, KeyError, OSError) as exc:
                gaps[d] = str(exc)
                continue
            around = sorted(trusted_days, key=lambda x: abs((x - d).days))[:4]
            seeds = ([(last_set)] if last_set else []) + [
                (a, set(official[a][0]), official[a][1]) for a in around
            ]
            tried = set()
            found = None
            for anchor, seed, url in seeds:
                events = [e for e in changes if min(anchor, d) < e[0] <= max(anchor, d)]
                if anchor > d:
                    events.reverse()
                adjusted = seed.copy()
                for _, add, remove, source in events:
                    if anchor > d:
                        add, remove = remove, add
                    adjusted = (adjusted - remove) | add
                for candidate, source in [
                    (adjusted, events[-1][3] if events else url),
                    (seed, url),
                ]:
                    key = frozenset(candidate)
                    if key in tried or len(key) < 200:
                        continue
                    tried.add(key)
                    match = reconcile_members(frame, volume, turnover, candidate, identities)
                    if match is None and abs((d - anchor).days) <= 35:
                        match = single_change(
                            frame, volume, turnover, candidate, identities, evidence
                        )
                        if match is not None:
                            added = set(match.index) - set(candidate)
                            if len(added) == 1:
                                source = evidence.get(next(iter(added)), source)
                    if match is not None:
                        found = (match[["isin", "series"]].copy(), source)
                        break
                if found is not None:
                    break
            if found is None:
                if (
                    turnover.is_integer()
                    and not (root / "sources" / "index-turnover" / str(d)).exists()
                ):
                    # A subsequent pass can use the more precise official historical series.
                    try:
                        check_turnover_reference(root, d)
                    except (ValueError, OSError, httpx.HTTPError):
                        pass
                gaps[d] = (
                    "No official constituent set reconciles to both published volume and turnover"
                )
                continue
            verified[d] = found
            gaps.pop(d, None)
            last_set = (d, set(found[0].index), found[1])
            identities.update(zip(found[0].index, found[0]["isin"]))
    intervals = []
    series = []
    last_rows = {}
    sectors = {}
    anchor_index = 0
    for d in days:
        while anchor_index < len(trusted_days) and trusted_days[anchor_index] <= d:
            sectors.update(official[trusted_days[anchor_index]][0])
            anchor_index += 1
        if d not in verified:
            last_rows = {}
            continue
        frame, source = verified[d]
        current = {}
        for row in frame.itertuples():
            sym, isin = row.Index, row.isin
            key = (isin, sym)
            interval = last_rows.get(key)
            if interval is None:
                interval = {
                    "symbol": sym,
                    "isin": isin,
                    "from_date": str(d),
                    "to_date": str(d),
                    "source_url": source,
                }
                intervals.append(interval)
            else:
                interval["to_date"] = str(d)
            current[key] = interval
            series.append((d, sym, isin, sectors.get(sym, "Unknown"), row.series))
        last_rows = current
    placeholder_gaps = include_unlisted(root, days, intervals)
    intervals.sort(key=lambda r: (r["isin"], r["from_date"]))
    folder = root / "universes"
    with market.staging(folder) as temp:
        pd.DataFrame(
            intervals, columns=["symbol", "isin", "from_date", "to_date", "source_url"]
        ).to_csv(temp / "nifty200_membership.csv", index=False)
        pd.DataFrame(series, columns=["day", "symbol", "isin", "sector", "series"]).sort_values(
            ["day", "isin"]
        ).to_parquet(temp / "reconstructed.parquet", index=False)
        by_reason = defaultdict(list)
        for d, why in gaps.items():
            by_reason[why].append(d)
        report = {
            "from": str(days[0]),
            "to": str(days[-1]),
            "expected_sessions": len(days),
            "verified_sessions": len(verified),
            "unlisted_identity_gaps": placeholder_gaps,
            "official_weight_anchors": len(official),
            "parsed_reconstitutions": len(changes),
            "gaps": {why: history.ranges(group, days) for why, group in by_reason.items()},
            "verification": "Each accepted session matches both official Nifty 200 total shares traded and turnover rounded to 0.01 crore. Each security is identified by its same-day exchange ISIN.",
        }
        (temp / "membership-gaps.json").write_text(json.dumps(report, indent=2) + "\n")
        for name in ("reconstructed.parquet", "nifty200_membership.csv", "membership-gaps.json"):
            if not (folder / name).exists() or market.sha((temp / name).read_bytes()) != market.sha(
                (folder / name).read_bytes()
            ):
                (temp / name).replace(folder / name)
    return report


def check_turnover_reference(root, d):
    """A rounded daily CSV can be corroborated by the official historical index record."""
    import httpx

    dest = root / "sources" / "index-turnover" / str(d)
    if dest.is_dir():
        return
    stamp = d.strftime("%d-%m-%Y")
    url = "https://www.nseindia.in/api/historicalOR/indicesHistory"
    response = httpx.get(
        url,
        params={"indexType": "NIFTY 200", "from": stamp, "to": stamp},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    response.raise_for_status()
    with market.staging(dest.parent) as tmp:
        (tmp / "response.json").write_bytes(response.content)
        (tmp / "source.json").write_text(
            json.dumps({"url": str(response.url), "sha256": market.sha(response.content)})
        )
        tmp.rename(dest)
