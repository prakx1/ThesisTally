"""Prepare an isolated NSE-primary pilot archive and verify the data before replay."""

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import constituents, corporate, exchange, history, market, reconstitutions
from .historical_sessions import (
    FIELDS,
    bridge_delayed_split_identifiers,
    large_moves,
    reference_rows,
)


def snapshot_hash(
    root, days, *, control_root=None, include_earnings=True, include_static_bands=True
):
    """Bind the ordered market sessions to the files verified by the preflight."""
    root = Path(root)
    digest = hashlib.sha256()
    controls = control_root if control_root is not None else root / "sources" / "exchange-controls"
    ledger = root / "sources" / "adjusted" / "quarantine.json"
    digest.update(ledger.read_bytes() if ledger.is_file() else b"no-quarantine-ledger")
    aliases = root / "sources" / "adjusted" / "action-aliases.json"
    if aliases.is_file():
        digest.update(aliases.read_bytes())
    intervals = root / "universes" / "nifty200_membership.csv"
    if intervals.is_file():
        digest.update(intervals.read_bytes())
    earnings = root / "sources" / "earnings-by-isin"
    verified_earnings = include_earnings and (earnings / "evidence.json").is_file()
    if verified_earnings:
        digest.update((earnings / "evidence.json").read_bytes())
    static = root / "sources/static-bands/coverage.json"
    static_days = {}
    if include_static_bands and static.is_file():
        coverage = static.read_bytes()
        static_days = json.loads(coverage)["days"]
        digest.update(coverage)
    for day in map(str, days):
        if day in static_days:
            raw = (static.parent / (day + ".json")).read_bytes()
            if market.sha(raw) != static_days[day]:
                raise market.MarketError(f"Static opening band receipt disagrees on {day}")
            digest.update(raw)
        digest.update(day.encode())
        digest.update((root / "sessions" / day / "manifest.json").read_bytes())
        digest.update((root / "context" / day / "context.sha256").read_bytes())
        overlay = controls / day / "sources.json"
        digest.update(overlay.read_bytes() if overlay.is_file() else b"no-overlay")
        if verified_earnings:
            digest.update((earnings / (day + ".json")).read_bytes())
    return digest.hexdigest()


def write_json(path, data):
    payload = (json.dumps(data, indent=2, sort_keys=True, default=str) + "\n").encode()
    if path.is_file() and path.read_bytes() == payload:
        return
    with market.staging(path.parent) as tmp:
        (tmp / "result").write_bytes(payload)
        (tmp / "result").replace(path)


def checked_link(target, link):
    if link.exists() or link.is_symlink():
        if link.resolve() != target.resolve():
            raise market.MarketError(f"Unexpected archive input at {link}")
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target.resolve(), target_is_directory=target.is_dir())


def calendar_dates(root, start, end, *, include_raw=False):
    official = set(history.official_calendar(root, start, end))
    kite = set(history.calendar(root, start, end))
    for day in kite - official:
        equity, indices = constituents.load_daily(root, day)
        if equity.empty or "NIFTY 50" not in indices.index:
            raise market.MarketError(f"Unconfirmed special session {day}")
    days = sorted(official | kite)
    archive = {d for d in market.sessions(root) if start <= d <= end}
    if include_raw:
        archive.update(
            date.fromisoformat(d.name)
            for d in (root / "sources" / "nse-daily").glob("????-??-??")
            if str(start) <= d.name <= str(end) and (d / "sources.json").exists()
        )
    if set(days) != archive:
        raise market.MarketError(f"Archive/calendar differences: {sorted(archive ^ set(days))}")
    if not days or days[-1] != end:
        raise market.MarketError(f"No completed exchange final for {end}")
    return days, {
        "nse_monthly": len(official),
        "kite_benchmark": len(kite),
        "special_confirmed_in_exchange_finals": sorted(kite - official),
        "missing_in_kite": sorted(official - kite),
    }


def read_notices(root):
    folder = root / "sources" / "reference" / "notices"
    out = {}
    for path in folder.glob("*.json"):
        item = json.loads(path.read_bytes())
        out[item["url"]] = (item, path)
    return out


# Amendments with atypical tables and demerger listing aliases. Each entry is
# verified against the retained, dated publisher PDF before use.
REVOCATION_URL = "https://www.niftyindices.com/Press_Release/ind_prs25092024.pdf"
MULTI_REVIEW_URL = "https://www.niftyindices.com/Press_Release/ind_prs23082024.pdf"
VEDANTA_URL = "https://www.niftyindices.com/Press_Release/ind_prs23042026.pdf"


def interval_archive(root):
    folder = root / "universes"
    history_dir = folder / "history"
    pointer = history_dir / "pre_pilot_membership.json"
    if pointer.is_file():
        checksum = json.loads(pointer.read_bytes())["sha256"]
    else:
        raw = (folder / "nifty200_membership.csv").read_bytes()
        checksum = market.sha(raw)
        history_dir.mkdir(exist_ok=True)
        archive = history_dir / (checksum + ".csv")
        if not archive.exists():
            with market.staging(history_dir) as tmp:
                (tmp / "snapshot").write_bytes(raw)
                (tmp / "snapshot").replace(archive)
        if market.sha(archive.read_bytes()) != checksum:
            raise market.MarketError("Original membership backup failed verification")
        write_json(pointer, {"sha256": checksum})
    archive = history_dir / (checksum + ".csv")
    if market.sha(archive.read_bytes()) != checksum:
        raise market.MarketError("Original membership archive checksum mismatch")
    return archive


def identity_spans(frame):
    """Combine adjacent calendar-day spans for a strict interval-by-interval identity check."""
    spans = []
    for row in frame.sort_values(["symbol", "isin", "from_date", "to_date"]).itertuples():
        key = (row.symbol, row.isin)
        start, end = date.fromisoformat(row.from_date), date.fromisoformat(row.to_date)
        if start > end:
            raise market.MarketError("Invalid member interval")
        if spans and spans[-1][0] == key and (start - spans[-1][2]).days <= 1:
            if start <= spans[-1][2]:
                raise market.MarketError("Overlapping identifier intervals")
            spans[-1] = (key, spans[-1][1], end)
        else:
            spans.append((key, start, end))
    return spans


def publish_canonical(root, view, start, end):
    archive = interval_archive(root)
    original = pd.read_csv(archive)
    validated = pd.read_csv(view / "universes" / "nifty200_membership.csv")
    before = original[original.from_date.lt(str(start))].copy()
    before["to_date"] = before.to_date.clip(upper=str(start - timedelta(days=1)))
    after = original[original.to_date.gt(str(end))].copy()
    after["from_date"] = after.from_date.clip(lower=str(end + timedelta(days=1)))
    rows = pd.concat([before, validated, after], ignore_index=True).sort_values(
        ["isin", "from_date", "symbol"]
    )
    if identity_spans(rows) != identity_spans(original):
        raise market.MarketError(
            "Pilot provenance publication would change membership on some dates"
        )
    path = root / "universes" / "nifty200_membership.csv"
    raw = rows.to_csv(index=False).encode()
    live = path.read_bytes()
    if live != raw:
        if live == archive.read_bytes():
            with market.staging(path.parent) as tmp:
                (tmp / path.name).write_bytes(raw)
                (tmp / path.name).replace(path)
        else:
            # Rechecking the Stage 1 slice after a full-history publication
            # must preserve both certified editions and their original receipts.
            full = root / "baseline" / "preflight.json"
            proof = json.loads(full.read_bytes()) if full.is_file() else {}
            prior = archive.parent / (market.sha(raw) + ".csv")
            if (
                proof.get("complete") is not True
                or proof.get("canonical_membership", {}).get("published_sha256") != market.sha(live)
                or not prior.is_file()
                or prior.read_bytes() != raw
            ):
                raise market.MarketError(
                    "Membership changed independently; refusing to overwrite new evidence"
                )
    return {
        "source_archive": "universes/history/" + archive.name,
        "original_sha256": archive.stem,
        "published_sha256": market.sha(raw),
        "preserved_all_identity_intervals": True,
    }


def membership_audit(root, view, members, days):
    anchors = reconstitutions.weight_anchors(root)
    start = max(d for d in anchors if d < days[0])
    previous = set(anchors[start][0])
    if not previous:
        raise market.MarketError("No published constituent anchor before pilot")
    notices = read_notices(root)
    events = reconstitutions.rebalance_events(root / "sources" / "reference" / "notices")
    prior = start
    ids = {}
    changes = []
    columns = ["symbol", "isin", "from_date", "to_date", "source_url"]
    intervals = pd.read_csv(interval_archive(root))
    if list(intervals.columns) != columns:
        raise market.MarketError("Unexpected constituent interval schema")
    intervals = intervals[
        intervals.to_date.ge(str(days[0])) & intervals.from_date.le(str(days[-1]))
    ]
    register = json.loads(
        (Path(__file__).with_name("revisions") / "unlisted-constituents.json").read_bytes()
    )
    unlisted = [(alias, included, listing) for alias, _, included, listing, _ in register]
    pilot = set(days)
    for day, group in members[members.day.between(start, days[-1])].groupby("day", sort=True):
        actual = set(group.symbol)
        changed = previous != actual
        if day in pilot:
            section = intervals[intervals.from_date.le(str(day)) & intervals.to_date.ge(str(day))]
            placeholders = {alias for alias, a, b in unlisted if a <= str(day) < b}
            traded = section[~section.symbol.isin(placeholders)]
            if (
                set(zip(traded.symbol, traded["isin"])) != set(zip(group.symbol, group["isin"]))
                or traded["isin"].duplicated().any()
            ):
                raise market.MarketError(
                    f"ISIN intervals disagree with dated trading universe on {day}"
                )
        if day in pilot or changed or day == start:
            frame, vol, turnover = constituents.day_table(root, day)
            expected = constituents.reconcile_members(
                frame, vol, turnover, actual, dict(zip(group.symbol, group["isin"]))
            )
            if expected is None or set(zip(expected.index, expected["isin"])) != set(
                zip(group.symbol, group["isin"])
            ):
                raise market.MarketError(f"Basket and official two-control totals differ on {day}")
        if changed or day == start:
            available = [
                e for e in events if prior < e[0] <= day and notices[e[3]][0]["issued"] <= str(day)
            ]
            proposed = previous.copy()
            used = []
            for _, add, remove, url in available:
                if day == date(2024, 9, 30) and url != MULTI_REVIEW_URL:
                    continue
                proposed = (proposed - remove) | add
                used.append(url)
            if day == date(2024, 9, 30):
                proposed = (proposed - {"CENTRALBK"}) | {"IDEA"}
                used.append(REVOCATION_URL)
            if day == date(2026, 6, 15):
                proposed |= {"VAML", "VEDPOWER", "VOGL", "VISL"}
                used.append(VEDANTA_URL)
            found = None
            method = ""
            for seed in [proposed, previous]:
                found = constituents.reconcile_members(frame, vol, turnover, seed, ids)
                if found is not None:
                    method = "published notice / prior exact basket and daily master"
                else:
                    found = constituents.single_change(
                        frame, vol, turnover, seed, ids, set(frame.index)
                    )
                    method = "unique same-session exchange volume and turnover solution"
                if found is not None and set(found.index) == actual:
                    break
            if found is None or set(found.index) != actual:
                raise market.MarketError(f"Forward-only membership evidence insufficient on {day}")
            for url in used:
                info, path = notices[url]
                if (
                    info["issued"] > str(day)
                    or market.sha(path.with_suffix(".pdf").read_bytes()) != info["sha256"]
                ):
                    raise market.MarketError(
                        f"Misdated or corrupt reconstitution evidence on {day}"
                    )
            if changed and day in pilot:
                changes.append(
                    {
                        "day": day,
                        "added": sorted(actual - previous),
                        "removed": sorted(previous - actual),
                        "method": method,
                        "notices": used,
                    }
                )
        previous = actual
        prior = day
        ids = dict(zip(group.symbol, group["isin"]))
    evidence = publish_intervals(root, view, intervals, changes, register, days)
    return {
        **evidence,
        "anchor": start,
        "anchor_sha256": anchors[start][1]["sha256"],
        "changes": changes,
        "traded_isins": int(members[members.day.isin(pilot)]["isin"].nunique()),
    }


PLACEHOLDER_NOTICES = {
    "JIOFIN": "9a5b0146fe1a8143",
    "DUMMYITC": "041e801d0bb42315",
    "DUMMYSIEMS": "f693b2bb6cc29c61",
    "DUMMYABFRL": "464b31601ac7d575",
    "DUMMYTATAM": "832f94cccde5984a",
    "DUMMYHDLVR": "205144750b314581",
    **{f"DUMMYVEDL{i}": "50b2570c42eabc6e" for i in range(1, 5)},
}


def exchange_url(day, name):
    return "https://nsearchives.nseindia.com/content/" + history.source_urls(day)[name]


def publish_intervals(root, view, intervals, changes, register, days):
    """Publish the verified pilot slice only, retaining after-the-fact identity crosswalks separately."""
    directory = view / "universes"
    if directory.is_symlink():
        if directory.resolve() != (root / "universes").resolve():
            raise market.MarketError("Unexpected pilot universe location")
        directory.unlink()  # detach only this pilot-view link
    directory.mkdir(exist_ok=True)
    canonical = intervals.copy()
    intervals = intervals.copy()
    intervals["from_date"] = intervals.from_date.clip(lower=str(days[0]))
    intervals["to_date"] = intervals.to_date.clip(upper=str(days[-1]))
    evidence = []
    events = reconstitutions.rebalance_events(root / "sources" / "reference" / "notices")
    changes_by_day = {str(c["day"]): c for c in changes}
    notice_folder = root / "sources" / "reference" / "notices"
    for row in intervals.itertuples():
        day = date.fromisoformat(row.from_date)
        change = changes_by_day.get(row.from_date, {})
        cited = change.get("notices", [])
        # An index closing file, paired with its underlying bhavcopy, identifies
        # the verified basket on days without a named inclusion circular.
        intervals.loc[row.Index, "source_url"] = exchange_url(day, "indices.csv")
        for url in cited:
            entry = next((k for k in events if k[3] == url and row.symbol in k[1]), None)
            if entry:
                intervals.loc[row.Index, "source_url"] = url
                break
    for alias, symbol, included, listed, _ in register:
        if listed <= str(days[0]) or included > str(days[-1]):
            continue
        chosen = (
            intervals.symbol.eq(alias)
            & intervals.from_date.lt(listed)
            & intervals.to_date.ge(included)
        )
        if not chosen.any():
            continue
        notice_key = PLACEHOLDER_NOTICES[alias]
        info = json.loads((notice_folder / (notice_key + ".json")).read_bytes())
        text = (notice_folder / (notice_key + ".txt")).read_text()
        if (
            market.sha((notice_folder / (notice_key + ".pdf")).read_bytes()) != info["sha256"]
            or (("Jio Financial Services" if alias == "JIOFIN" else alias) not in text)
            or info["issued"] > included
            or not re.search("nifty\\s*200", text, re.IGNORECASE)
        ):
            raise market.MarketError(f"Incomplete contemporary inclusion evidence for {alias}")
        isin = intervals.loc[chosen, "isin"].item()
        listing = date.fromisoformat(listed)
        frame, _, _ = constituents.day_table(root, listing)
        if isin not in set(frame.loc[[symbol], "isin"]):
            raise market.MarketError(f"Missing listing-day ISIN for {alias}")
        intervals.loc[
            chosen | (intervals.symbol.eq(symbol) & intervals.from_date.eq(listed)), "source_url"
        ] = info["url"]
        evidence.append(
            {
                "alias": alias,
                "symbol": symbol,
                "isin": isin,
                "included": included,
                "included_by": info["url"],
                "included_notice_sha256": info["sha256"],
                "isin_in_inclusion_notice": isin in text,
                "isin_verified_on": listed,
                "isin_source": exchange_url(listing, "bhavcopy.zip"),
            }
        )
    path = directory / "nifty200_membership.csv"
    csv = intervals.to_csv(index=False).encode()
    if not path.is_file() or path.read_bytes() != csv:
        with market.staging(directory) as tmp:
            (tmp / path.name).write_bytes(csv)
            (tmp / path.name).replace(path)
    proof = canonical.rename(
        columns={
            "from_date": "archive_from",
            "to_date": "archive_to",
            "source_url": "archive_source",
        }
    )
    for name in ["from_date", "to_date", "source_url"]:
        proof[name] = intervals[name]
    proof["daily_bhavcopy"] = proof.from_date.map(
        lambda d: exchange_url(date.fromisoformat(d), "bhavcopy.zip")
    )
    proof["daily_index_controls"] = proof.from_date.map(
        lambda d: exchange_url(date.fromisoformat(d), "indices.csv")
    )
    proof["identity_verified_on"] = proof.from_date
    for identity in evidence:
        proof.loc[
            proof.symbol.eq(identity["alias"]) & proof.from_date.lt(identity["isin_verified_on"]),
            "identity_verified_on",
        ] = identity["isin_verified_on"]
    proof["archived_index_checksum"] = proof.from_date.map(
        {
            d: market.sha((root / "sessions" / d / "indices.csv").read_bytes())
            for d in proof.from_date.unique()
        }
    )
    proof["archived_bhavcopy_checksum"] = proof.from_date.map(
        {
            d: market.sha((root / "sessions" / d / "bhavcopy.csv").read_bytes())
            for d in proof.from_date.unique()
        }
    )
    with market.staging(view.parent) as tmp:
        proof.to_csv(tmp / "membership-provenance.csv", index=False)
        (tmp / "membership-provenance.csv").replace(view.parent / "membership-provenance.csv")
    return {
        "membership_csv_sha256": market.sha(csv),
        "placeholder_evidence": evidence,
        "interval_count": len(intervals),
        "unverified_traded_member_sessions": 0,
    }


def audit_moves(tape, actions, issues, first_bar):
    # Recompute prior raw closes independently; switches to a successor ISIN
    # reset the comparison rather than mixing price units across securities.
    equity = tape[~tape.symbol.isin(market.BENCHMARKS)].sort_values(["isin", "day"]).copy()
    prior = equity.groupby("isin", sort=False).close.shift()
    raw_move = equity.close.div(prior).sub(1)
    window = equity.day.ge(first_bar)
    equity["move"] = raw_move
    large = large_moves(equity.close, prior)
    found = equity[window & large]
    checks = []
    for r in found.itertuples():
        dated = [
            a["subject"]
            for a in actions.get((r.day, r.symbol), [])
            if re.search("split|bonus|rights|consolidation|demerger", a["subject"], re.IGNORECASE)
        ]
        reason = issues.get((str(r.day), r.isin))
        if not dated and (reason != "unexplained_move_over_20pct" or not r.unexplained_jump):
            raise market.MarketError(f"Unquarantined raw move on {r.day} {r.isin}")
        checks.append(
            {
                "day": r.day,
                "isin": r.isin,
                "symbol": r.symbol,
                "return": r.move,
                "official_ex_date_actions": dated,
                "quarantine_reason": reason,
            }
        )
    incorrectly_marked = equity[window & equity.unexplained_jump & ~large & prior.notna()]
    if len(incorrectly_marked):
        raise market.MarketError("False positive on raw move quarantine")
    return {"start": first_bar, "moves": checks}


def audit_split_transitions(tape, members, actions, delayed, days):
    """Check each same-symbol ISIN switch in pilot names against exchange action terms."""
    ids = set(members["isin"])
    ordered = tape.sort_values(["symbol", "day"]).reset_index(drop=True)
    previous = ordered.groupby("symbol")[
        ["day", "isin", "chain", "share_factor", "price_factor"]
    ].shift()
    mask = (
        ordered.day.between(days[0], days[-1])
        & previous["isin"].notna()
        & ordered["isin"].ne(previous["isin"])
        & (ordered["isin"].isin(ids) | previous["isin"].isin(ids))
    )
    effective_days = {
        (r["old_isin"], r["new_isin"]): date.fromisoformat(r["effective"]) for r in delayed
    }
    active = set(zip(members.day, members.symbol))
    dates = ordered.set_index(["symbol", "day"], verify_integrity=True)
    transitions = []
    for row in ordered[mask].itertuples():
        old = previous.loc[row.Index]
        effective = effective_days.get((old["isin"], row.isin), row.day)
        evidence = sorted({a["subject"] for a in actions.get((effective, row.symbol), [])})
        split = next(
            (
                re.search(
                    r"from\s*(?:rs\.?|re\.?)?\s*(\d+(?:\.\d+)?)\s*.*?to\s*(?:rs\.?|re\.?)?\s*(\d+(?:\.\d+)?)",
                    s,
                    re.IGNORECASE,
                )
                for s in evidence
                if "split" in s.lower()
            ),
            None,
        )
        if not split:
            raise market.MarketError(
                f"Unproven mechanical ISIN switch for {row.symbol} on {row.day}"
            )
        ratio = Fraction(split[1]) / Fraction(split[2])
        for subject in evidence:
            if bonus := re.search(r"bonus\s+(\d+)\s*:\s*(\d+)", subject, re.IGNORECASE):
                ratio *= 1 + Fraction(int(bonus[1]), int(bonus[2]))
        event = dates.loc[row.symbol, effective]
        prior = previous.loc[
            ordered.index[(ordered.symbol == row.symbol) & (ordered.day == effective)].item()
        ]
        if not (
            old.chain == row.chain
            and np.isclose(event.share_factor / prior.share_factor, float(ratio))
            and np.isclose(event.price_factor / prior.price_factor, 1 / float(ratio))
        ):
            raise market.MarketError(
                f"Split factors or identifier lineage disagree on {effective} for {row.symbol}"
            )
        next_membership = members[(members["isin"] == row.isin) & members.day.ge(row.day)]
        transitions.append(
            {
                "symbol": row.symbol,
                "old_isin": old["isin"],
                "new_isin": row.isin,
                "effective": effective,
                "first_new_isin_print": row.day,
                "last_old_isin_print": old["day"],
                "active_on_ex_date": (effective, row.symbol) in active,
                "subjects": evidence,
                "new_units": ratio.numerator,
                "old_units": ratio.denominator,
                "first_new_isin_member_session": next_membership.day.min()
                if len(next_membership)
                else None,
                "old_quote_source": exchange_url(old["day"], "bhavcopy.zip"),
                "new_quote_source": exchange_url(row.day, "bhavcopy.zip"),
            }
        )
    return transitions


def kite_audit(root, members, actions, days):
    pieces = []
    for path in sorted((root / "sources" / "kite-history").glob("*/*.parquet")):
        pieces.append(pd.read_parquet(path, columns=["day", *market.PRICES]).assign(isin=path.stem))
    if not pieces:
        raise market.MarketError("Independent Kite cross-check archive unavailable")
    kite = pd.concat(pieces, ignore_index=True).drop_duplicates()
    ambiguous = kite.duplicated(["day", "isin"], keep=False)
    joined = members.merge(
        kite[~ambiguous],
        on=["day", "isin"],
        how="left",
        suffixes=("_nse", "_kite"),
        validate="one_to_one",
    )
    both = joined.close_kite.notna()
    scale = joined.close_nse / joined.close_kite
    mismatch = np.zeros(len(joined), dtype=bool)
    for key in ["open", "high", "low"]:
        nse = joined[key + "_nse"]
        mismatch |= (joined[key + "_kite"] * scale - nse).abs() > np.maximum(0.10, 0.001 * nse)
    shape_rows = joined[mismatch]
    missing_rows = joined[~both]
    joined = joined.sort_values(["isin", "day"])
    same = joined.groupby("isin", sort=False)
    prior_day = same.day.shift()
    previous = joined.day.map(dict(zip(days[1:], days[:-1])))
    consecutive = prior_day.notna() & prior_day.eq(previous)
    joined["return_nse"] = joined.close_nse.div(same.close_nse.shift()).sub(1)
    joined["return_kite"] = joined.close_kite.div(same.close_kite.shift()).sub(1)
    comparable = consecutive & joined.return_kite.notna()
    # A difference of 10 bp in return is recorded. Keep even an ex-date
    # difference in the output so an incorrectly adjusted bar is visible.
    differences = joined[
        comparable & joined.return_nse.sub(joined.return_kite).abs().gt(0.001)
    ].copy()
    differences["ex_date_events"] = [
        sorted({r["subject"] for r in actions.get((d, s), [])})
        for d, s in zip(differences.day, differences.symbol)
    ]
    return {
        "close_return": {
            "eligible_consecutive_pairs": int(consecutive.sum()),
            "comparable": int(comparable.sum()),
            "missing": int((consecutive & ~comparable).sum()),
            "difference_threshold_bps": 10,
            "mismatches": len(differences),
            "with_documented_ex_date": int(differences.ex_date_events.map(bool).sum()),
            "differences": differences[
                ["day", "isin", "symbol", "return_nse", "return_kite", "ex_date_events"]
            ].to_dict("records"),
        },
        "member_observations": len(joined),
        "comparable": int(both.sum()),
        "missing": int((~both).sum()),
        "mismatches": int(mismatch.sum()),
        "differences": shape_rows[["day", "isin", "close_nse", "close_kite"]].to_dict("records"),
        "missing_by_isin": missing_rows.groupby("isin").size().to_dict(),
    }


def collect_action_evidence(root):
    """Cache the exact public documents cited by the study, verifying on every run."""
    folder = root / "sources" / "compulsory-actions"
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for isin in corporate.actions(root):
            bundle = folder / isin
            manifest = bundle / "sources.json"
            if not manifest.is_file():
                raise market.MarketError(f"Missing compulsory-action source manifest for {isin}")
            for name, evidence in json.loads(manifest.read_bytes()).items():
                if Path(name).name != name or not evidence["url"].startswith("https://"):
                    raise market.MarketError(f"Invalid compulsory-action source for {isin}")
                path = bundle / name
                if path.exists():
                    if market.sha(path.read_bytes()) != evidence["sha256"]:
                        raise market.MarketError(f"Corrupt compulsory-action source {isin}/{name}")
                    continue
                response = client.get(evidence["url"])
                response.raise_for_status()
                if market.sha(response.content) != evidence["sha256"]:
                    raise market.MarketError(f"Changed compulsory-action source {isin}/{name}")
                with market.staging(bundle) as tmp:
                    (tmp / name).write_bytes(response.content)
                    (tmp / name).replace(path)


def pilot_sources(root, view, *, full_history=False):
    """Expose immutable originals and a dated correction to the runtime quarantine ledger."""
    original = root / "sources"
    directory = view / "sources"
    if directory.is_symlink():
        if directory.resolve() != original.resolve():
            raise market.MarketError("Unexpected pilot source location")
        directory.unlink()
    directory.mkdir(exist_ok=True, parents=True)
    for source in original.iterdir():
        if source.name == "exchange-controls" and full_history:
            # Stage 2 overlays verified earlier controls inside its own view.
            dest = directory / source.name
            if not dest.exists() or dest.is_symlink():
                checked_link(source, dest)
        elif source.name != "adjusted":
            checked_link(source, directory / source.name)
    adjusted = directory / "adjusted"
    adjusted.mkdir(exist_ok=True)
    for source in (original / "adjusted").iterdir():
        if source.name != "quarantine.json":
            checked_link(source, adjusted / source.name)
    rows = json.loads((original / "adjusted" / "quarantine.json").read_bytes())
    key = PLACEHOLDER_NOTICES["DUMMYTATAM"]
    folder = original / "reference" / "notices"
    notice = json.loads((folder / (key + ".json")).read_bytes())
    if (
        market.sha((folder / (key + ".pdf")).read_bytes()) != notice["sha256"]
        or notice["issued"] > "2025-10-14"
    ):
        raise market.MarketError("Tata Motors dated demerger receipt failed")
    heg_file = "HEG_24082026185355_FinalOutcome24082026.pdf"
    heg_url = "https://nsearchives.nseindia.com/corporate/" + heg_file
    evidence_dir = view.parent / "evidence"
    evidence_dir.mkdir(exist_ok=True)
    path = evidence_dir / heg_file
    checksum = "454eb663af73944b4e34c095be6be3f4cac814c6319e95e5fc6fd5e29b73c4a0"
    cached = root / "pilot" / "evidence" / heg_file
    if not path.exists() and cached.is_file() and market.sha(cached.read_bytes()) == checksum:
        checked_link(cached, path)
    if not path.exists():
        with httpx.Client(
            follow_redirects=True, timeout=20, headers={"User-Agent": "Mozilla/5.0"}
        ) as client:
            reply = client.get(heg_url)
            reply.raise_for_status()
            if market.sha(reply.content) != checksum:
                raise market.MarketError("HEG demerger evidence hash mismatch")
            with market.staging(evidence_dir) as tmp:
                (tmp / heg_file).write_bytes(reply.content)
                (tmp / heg_file).replace(path)
    if market.sha(path.read_bytes()) != checksum:
        raise market.MarketError("HEG demerger evidence hash mismatch")
    supplemental = {
        (date(2025, 10, 14), "TATAMOTORS"): {
            "subject": "Demerger of commercial vehicle business",
            "issued": notice["issued"],
            "source": notice["url"],
            "sha256": notice["sha256"],
        },
        (date(2026, 9, 7), "HEG"): {
            "subject": "Demerger of graphite business",
            "issued": "2026-08-24",
            "source": heg_url,
            "sha256": checksum,
        },
    }
    for row in rows:
        key = (date.fromisoformat(row["day"]), row["symbol"])
        if key in supplemental:
            row.update(reason="unresolved_action", **supplemental[key])
    equality = [
        row
        for row in rows
        if row["reason"] == "unexplained_move_over_20pct"
        and not market.is_strict_move(row["close"], row["prior_close"])
    ]
    rows = [row for row in rows if row not in equality]
    write_json(adjusted / "quarantine.json", rows)
    return rows, equality, {key: [event] for key, event in supplemental.items()}


def prepare(root, start, end, *, full_history=False):
    root = Path(root).resolve()
    output = root / ("baseline" if full_history else "pilot")
    view = output / "market"
    collect_action_evidence(root)
    ledger, equality, supplemental_actions = pilot_sources(root, view, full_history=full_history)
    for name in ("calendars", "corporate_actions.json"):
        if (root / name).exists():
            checked_link(root / name, view / name)
    days, calendar = calendar_dates(root, start, end)
    all_members = pd.read_parquet(root / "universes" / "reconstructed.parquet")
    membership = membership_audit(root, view, all_members, days)
    members = all_members[all_members.day.isin(days)]
    groups = dict(tuple(members.groupby("day")))
    first_year = start.year - 2
    tape = pd.concat(
        [
            pd.read_parquet(root / "sources" / "adjusted" / f"{y}.parquet")
            for y in range(first_year, end.year + 1)
        ],
        ignore_index=True,
    )
    tape = tape[tape.day.le(end)].copy()
    delayed = bridge_delayed_split_identifiers(tape)
    reference = members.merge(
        tape[["day", "isin", *market.PRICES]], on=["day", "isin"], validate="one_to_one", how="left"
    )
    if reference[market.PRICES].isna().any().any():
        raise market.MarketError("Missing official member prices")
    actions, meetings, availability, _ = reference_rows(root)
    covered_meetings = {
        first + timedelta(days=i)
        for first, last in availability["meetings"]
        for i in range((last - first).days + 1)
    }
    actions = {
        **actions,
        **{key: [*actions.get(key, []), *events] for key, events in supplemental_actions.items()},
    }
    if full_history:
        from .baseline_data import (
            index_identifier_history,
            restore_benchmark_warmup,
            restore_dated_splits,
        )

        tape, benchmark_restorations = restore_benchmark_warmup(root, tape)
        from .baseline_actions import restore_action_aliases

        alias_actions, cleared_aliases = restore_action_aliases(root, tape, view, ledger, actions)
        split_repairs = restore_dated_splits(tape, view, ledger, actions)
        write_json(view / "sources" / "adjusted" / "quarantine.json", ledger)

        transitions = index_identifier_history(tape, members, actions, days)
    else:
        transitions = audit_split_transitions(tape, members, actions, delayed, days)
    kite = kite_audit(root, reference, actions, days)
    issues = {(r["day"], r.get("isin")): r["reason"] for r in ledger}
    session_axis = pd.Index(sorted(tape[tape.symbol.eq("NIFTY 50")].day.unique()))
    first_bar = session_axis[max(0, session_axis.get_loc(days[0]) - 399)]
    warmup_calendar, _ = calendar_dates(root, first_bar, days[0], include_raw=full_history)
    if (
        list(session_axis[session_axis.get_loc(first_bar) : session_axis.get_loc(days[0]) + 1])
        != warmup_calendar
    ):
        raise market.MarketError("Warm-up exchange calendar disagrees with the adjusted tape")
    false_flags = {(r["day"], r["isin"]) for r in equality}
    if full_history:
        false_flags.update(cleared_aliases)
    for r in equality:
        tape.loc[
            tape.day.eq(date.fromisoformat(r["day"])) & tape["isin"].eq(r["isin"]),
            "unexplained_jump",
        ] = False
    move_audit = audit_moves(tape, actions, issues, first_bar)
    keyed = tape.set_index(["day", "chain"], verify_integrity=True)
    pivots = {
        k: keyed[k].unstack().reindex(session_axis)
        for k in ["adjusted_" + f for f in FIELDS] + ["price_factor", "share_factor", "isin"]
    }
    cols = pivots["isin"].columns
    flagged = tape[tape.unexplained_jump].groupby("chain").day.agg(list).to_dict()
    unknown = defaultdict(list)
    missing_sector = Counter()
    year_bytes = Counter()
    source_formats = Counter()
    resets = defaultdict(list)
    member_flags = []
    restored_earnings = []
    warmups = defaultdict(Counter)
    close_only_vix_days = set()
    for d in days:
        day = str(d)
        session = root / "sessions" / day
        target = view / "sessions" / day
        group = groups[d]
        original = json.loads((session / "manifest.json").read_bytes())
        for key in ("bhavcopy.csv", "indices.csv", "members.csv"):
            if market.sha((session / key).read_bytes()) != original["sha256"][key]:
                raise market.MarketError(f"Original exchange input checksum failed {day}: {key}")
        legacy = original.get("format") != "exchange-history-v2"
        source_formats["legacy_kite_warmup" if legacy else "exchange_history"] += 1
        idx = session_axis.get_loc(d)
        axis = session_axis[max(0, idx - 399) : idx + 1]
        observed = pivots["isin"].iloc[idx].dropna()
        mapper = dict(zip(observed, observed.index))
        ids = [*group["isin"], *market.BENCHMARKS]
        chains = [mapper[v] for v in ids]
        selection = cols.get_indexer(chains)
        labels = [*group.symbol, *market.BENCHMARKS]
        cuts = {
            k: max((v for v in flagged.get(c, []) if axis[0] <= v <= d), default=None)
            for k, c in zip(labels, chains)
        }
        cuts = {k: v for k, v in cuts.items() if v}
        by_symbol = dict(zip(labels, ids))
        for symbol, cut in cuts.items():
            resets[(by_symbol[symbol], symbol, cut)].append(d)
        folder = root / "context" / day
        ctx_target = view / "context" / day
        source_context = exchange.read_context(root, d, verify_sources=True)
        modified = {}
        for sym, isin in zip(group.symbol, group["isin"]):
            reason = issues.get((day, isin))
            if reason:
                tag = (
                    "unexplained_close_move" if reason == "unexplained_move_over_20pct" else reason
                )
                modified[sym] = tag
                member_flags.append(
                    {"day": day, "symbol": sym, "isin": isin, "quarantine_reason": tag}
                )
        recover = {
            sym
            for sym, item in source_context.items()
            if item.get("earnings_coverage") == "unknown"
        }
        if recover and all(d + timedelta(days=i) in covered_meetings for i in range(31)):
            cutoff = datetime.combine(d, time(15, 45))
            for sym in recover:
                announced = [
                    (when - d).days
                    for when, stamp in meetings.get(sym, [])
                    if d <= when <= d + timedelta(days=30) and stamp <= cutoff
                ]
                if min(announced, default=None) != source_context[sym]["earnings_in_days"]:
                    raise market.MarketError(
                        f"Dated earnings announcements disagree for {sym} on {d}"
                    )
                source_context[sym]["earnings_coverage"] = "known"
            restored_earnings.append({"day": day, "members": len(recover)})
        else:
            recover = set()
        clear = {
            sym
            for sym, isin in zip(group.symbol, group["isin"])
            if (day, isin) in false_flags and (day, isin) not in issues
        }
        if not modified and not recover and not clear:
            checked_link(folder, ctx_target)
        else:
            raw = json.loads((folder / "context.json").read_bytes())
            if ctx_target.is_symlink():
                if ctx_target.resolve() != folder.resolve():
                    raise market.MarketError(f"Unexpected context link on {day}")
                ctx_target.unlink()  # detach this pilot link before writing its local copy
            ctx_target.mkdir(parents=True, exist_ok=True)
            for sym in clear:
                raw["symbols"][sym]["quarantined"] = False
                raw["symbols"][sym].pop("quarantine_reason", None)
            for sym in recover:
                raw["symbols"][sym]["earnings_coverage"] = "known"
            for sym, reason in modified.items():
                raw["symbols"][sym].update(quarantined=True, quarantine_reason=reason)
            for name in raw["sources"]:
                checked_link(folder / name, ctx_target / name)
            blob = json.dumps(raw, sort_keys=True).encode()
            with market.staging(ctx_target.parent) as tmp:
                for name, value in (
                    ("context.json", blob),
                    ("context.sha256", market.sha(blob).encode()),
                ):
                    existing = ctx_target / name
                    if not existing.exists() or existing.read_bytes() != value:
                        (tmp / name).write_bytes(value)
                        (tmp / name).replace(existing)
        raw_ctx = json.loads((folder / "context.json").read_bytes())
        if raw_ctx.get("band_reference") and raw_ctx["band_reference"] != str(
            session_axis[idx - 1]
        ):
            raise market.MarketError(f"Wrong prior session for opening bands on {day}")
        if (
            any("earnings_coverage" not in row for row in source_context.values())
            and "meetings.json" not in raw_ctx["sources"]
        ):
            raise market.MarketError(f"Missing evidence of earnings calendar coverage on {day}")
        for field, key in (
            ("surveillance", "surveillance"),
            ("opening_band", "floor"),
            ("earnings_calendar", "earnings_coverage"),
        ):
            count = sum(
                c.get(key, "known" if key == "earnings_coverage" else None) in (None, "unknown")
                for c in source_context.values()
            )
            if count:
                unknown[field].append({"day": day, "members": count})
        missing_sector[str(d.year)] += int(group.sector.eq("Unknown").sum())
        affected_lineages = any(
            t["new_isin"] in ids
            and str(axis[0]) <= t["effective"]
            and t["first_printed_new_isin"] <= day
            for t in delayed
        )
        if not full_history and not legacy and not cuts and not affected_lineages:
            checked_link(session, target)
        else:
            block = pd.DataFrame(
                {"day": np.repeat(axis, len(labels)), "symbol": np.tile(labels, len(axis))}
            )
            for f in FIELDS:
                scale = (
                    pivots["share_factor" if f == "volume" else "price_factor"]
                    .iloc[idx, selection]
                    .to_numpy()
                )
                block[f] = (
                    pivots["adjusted_" + f]
                    .iloc[idx - len(axis) + 1 : idx + 1, selection]
                    .to_numpy()
                    * scale
                ).ravel()
            block = block.dropna(subset=["close"])
            if cuts:
                bounds = block.symbol.map(cuts)
                block = block[bounds.isna() | (block.day > bounds) | (block.day == d)]
            with market.staging(target.parent) as tmp:
                for key in ("bhavcopy.csv", "indices.csv"):
                    checked_link(session / key, tmp / key)
                group.rename(
                    columns={
                        "symbol": "Symbol",
                        "isin": "ISIN Code",
                        "sector": "Industry",
                        "series": "Series",
                    }
                ).drop(columns=["day"]).to_csv(tmp / "members.csv", index=False)
                block.to_parquet(tmp / "bars.parquet", index=False)
                stamp = {
                    key: market.sha((tmp / key).read_bytes())
                    for key in ("bhavcopy.csv", "indices.csv", "members.csv", "bars.parquet")
                }
                meta = {
                    **{k: v for k, v in original.items() if k in ["source", "source_files"]},
                    "format": "exchange-history-v2",
                    "day": day,
                    "prior_day": str(session_axis[idx - 1]),
                    "requested_start": str(axis[0]),
                    "members": len(group),
                    "instruments": len(labels),
                    "candles": len(block),
                    "verified_closes": len(group),
                    "sha256": stamp,
                    "price_basis": "NSE OHLCV, ex-date rebasing, discontinuity after quarantined move",
                }
                write_json(tmp / "manifest.json", meta)
                if target.is_symlink():
                    if target.resolve() != session.resolve():
                        raise market.MarketError(f"Unexpected pilot session link {day}")
                    target.unlink()  # detach the pilot's symlink before writing corrected history
                if not target.exists():
                    tmp.rename(target)
                else:
                    for name in ("bhavcopy.csv", "indices.csv"):
                        checked_link(session / name, target / name)
                    for name in ("members.csv", "bars.parquet", "manifest.json"):
                        if not (target / name).is_file() or market.sha(
                            (target / name).read_bytes()
                        ) != market.sha((tmp / name).read_bytes()):
                            (tmp / name).replace(target / name)
        meta = market.verify(view, d)
        bars = pd.read_parquet(target / "bars.parquet")
        histories = bars[bars.symbol.isin(group.symbol)].groupby("symbol").day.agg(["min", "count"])
        chain_for = dict(zip(labels, chains))
        for sym, first, count in histories[histories["count"].lt(len(axis))].itertuples():
            if sym in cuts or "@" in str(chain_for[sym]) and first > axis[0]:
                reason = "quarantined_discontinuity"
            elif first > axis[0]:
                reason = "first_observed_chain_within_lookback"
            else:
                reason = "no_exchange_bar_inside_lookback"
            bucket = warmups[reason, by_symbol[sym]]
            bucket["under_400"] += 1
            bucket["under_252"] += int(count < 252)
            bucket["under_200"] += int(count < 200)
        values = bars[FIELDS]
        if full_history:
            close_only = (
                bars.symbol.eq("INDIA VIX")
                & bars[["open", "high", "low"]].isna().all(axis=1)
                & np.isfinite(bars.close)
                & bars.close.gt(0)
                & bars.volume.eq(0)
            )
            close_only_vix_days.update(bars.loc[close_only, "day"])
            values = values[~close_only]
        if (
            not np.isfinite(values).all().all()
            or (values[market.PRICES] <= 0).any().any()
            or (values.volume < 0).any()
            or bars.duplicated(["day", "symbol"]).any()
            or bars.day.max() != d
            or (values.high + 1e-9 < values[["open", "close", "low"]].max(axis=1)).any()
            or (values.low - 1e-9 > values[["open", "close", "high"]].min(axis=1)).any()
        ):
            raise market.MarketError(f"Invalid historical OHLCV in pilot session {day}")
        if cuts:
            for sym, cut in cuts.items():
                if (bars.symbol.eq(sym) & bars.day.le(cut) & bars.day.ne(d)).any():
                    raise market.MarketError(f"Contaminated indicator history: {day} {sym}")
        if set(exchange.read_context(view, d, verify_sources=True)) != set(group.symbol):
            raise market.MarketError(f"Incomplete pilot exchange context {day}")
        year_bytes[str(d.year)] += sum(
            (target / k).stat().st_size for k in [*meta["sha256"], "manifest.json"]
        )
    return {
        "complete": True,
        "start": str(start),
        "end": str(end),
        "sessions": list(map(str, days)),
        "snapshot_sha256": snapshot_hash(
            view,
            days,
            control_root=(root / "sources" / "exchange-controls") if full_history else None,
            include_earnings=not full_history,
            include_static_bands=not full_history,
        ),
        "calendar": calendar,
        **(
            {
                "close_only_vix_days": sorted(close_only_vix_days),
                "historical_split_repairs": split_repairs,
                "corporate_actions_by_isin": alias_actions,
                "benchmark_alias_restorations": benchmark_restorations,
            }
            if full_history
            else {}
        ),
        "membership": membership,
        "year_sessions": dict(Counter(d.year for d in days)),
        "year_logical_bytes": year_bytes,
        "input_formats": source_formats,
        "reset_symbol_sessions": sum(map(len, resets.values())),
        "discontinuity_resets": [
            {
                "isin": i,
                "symbol": s,
                "cut": c,
                "reason": issues.get((str(c), i), "predecessor_chain_discontinuity"),
                "sessions": ds,
            }
            for (i, s, c), ds in sorted(resets.items())
        ],
        "member_flags": member_flags,
        "raw_move_checks": move_audit,
        "delayed_split_identifiers": delayed,
        "split_identifier_transitions": transitions,
        "exact_boundary_repairs": [r for r in equality if str(first_bar) <= r["day"] <= str(end)],
        "quarantine": [r for r in ledger if str(start) <= r["day"] <= str(end)],
        "warmup_calendar": {"start": first_bar, "end": days[0], "sessions": len(warmup_calendar)},
        "short_warmups": [
            {"cause": reason, "isin": isin, **counts}
            for (reason, isin), counts in sorted(warmups.items())
        ],
        "earnings_boundary_repairs": restored_earnings,
        "unknown_controls": unknown,
        "unknown_sector_observations": missing_sector,
        "kite": kite,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    path = args.root / "pilot" / "preflight.json"
    write_json(
        path,
        {"complete": False, "start": str(args.start), "end": str(args.end), "status": "checking"},
    )
    try:
        result = prepare(args.root, args.start, args.end)
        result["canonical_membership"] = publish_canonical(
            args.root, args.root / "pilot" / "market", args.start, args.end
        )
    except Exception as exc:
        write_json(
            path,
            {"complete": False, "start": str(args.start), "end": str(args.end), "error": str(exc)},
        )
        raise
    write_json(path, result)
    print(f"{len(result['sessions'])} verified exchange sessions. {path}")


if __name__ == "__main__":
    main()
