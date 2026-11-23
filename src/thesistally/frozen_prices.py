"""Append one official exchange session to an ISIN-addressed, as-of-close rolling panel."""

import json
import re

import numpy as np
import pandas as pd

from . import convention_market, historical_sessions, market

FIELDS = historical_sessions.FIELDS
CAPITAL = re.compile(
    r"bonus|split|sub.division|consolidation|rights|demerger|scheme of arrangement", re.IGNORECASE
)
SUBDIVISION = re.compile(r"split|sub.division|consolidation", re.IGNORECASE)
NSE_FIELDS = ["OpnPric", "HghPric", "LwPric", "ClsPric", "TtlTradgVol"]
INDEX_FIELDS = ["Open Index Value", "High Index Value", "Low Index Value", "Closing Index Value"]


def adjustment_factors(subjects, prior_close, release):
    px = shares = 1.0
    for subject, face in sorted(subjects):
        if not CAPITAL.search(subject):
            continue
        if re.search(r"demerger|scheme of arrangement", subject, re.IGNORECASE):
            return (1.0, 1.0), True
        if "rights" in subject.lower():
            terms = convention_market.rights_issue(
                None, "", subject, face, prior_close, {"url": "", "sha256": ""}
            )
            pair = (terms["factor"], 1.0) if terms else None
        else:
            pair = historical_sessions.adjustment(subject, face, prior_close)
        if pair is None:
            return (1.0, 1.0), True
        px *= pair[0]
        shares *= pair[1]
    if release.get("unverified_jump") and not release.get("quarantine_release"):
        return (1.0, 1.0), True
    return (px, shares), False


def adjusted_previous(frame, subjects, prior_close, release):
    (px, shares), gap = adjustment_factors(subjects, prior_close, release)
    if gap:
        return frame.iloc[:0].copy(), True
    result = frame.copy()
    result[market.PRICES] *= px
    result["volume"] *= shares
    return result, False


def notices(raw, day):
    result = {}
    for row in json.loads(raw):
        if row.get("exDate") == day.strftime("%d-%b-%Y") and row.get("series", "").strip() in {
            "EQ",
            "BE",
            "BZ",
        }:
            result.setdefault((row["isin"], row["symbol"]), set()).add(
                (row["subject"], row.get("faceVal") or "")
            )
    return result


def session_rows(day, members, bhav, indices):
    by = bhav[bhav.SctySrs.str.strip().isin(["EQ", "BE", "BZ"])].copy()
    rows = members[["isin", "symbol", "series"]].merge(
        by,
        left_on=["isin", "symbol", "series"],
        right_on=["ISIN", "TckrSymb", "SctySrs"],
        how="left",
        validate="one_to_one",
    )
    current = rows[["isin", "symbol", *NSE_FIELDS]].rename(columns=dict(zip(NSE_FIELDS, FIELDS)))
    for f in FIELDS:
        current[f] = pd.to_numeric(current[f], errors="raise")
    lookup = indices.loc[list(market.BENCHMARKS), INDEX_FIELDS].apply(pd.to_numeric, errors="raise")
    lookup.columns = market.PRICES
    lookup["volume"] = 0.0
    lookup["isin"] = lookup["symbol"] = lookup.index
    current = pd.concat([current, lookup], ignore_index=True)
    if (
        not np.isfinite(current[FIELDS]).all().all()
        or current.low.le(0).any()
        or current.volume.lt(0).any()
        or current.high.lt(current[["open", "close", "low"]].max(axis=1)).any()
        or current.low.gt(current[["open", "close", "high"]].min(axis=1)).any()
    ):
        raise market.MarketError(f"Invalid official OHLCV at {day}")
    return current.assign(day=day)


def append_prices(
    previous,
    old_members,
    current,
    events,
    releases,
    old_resets,
    new_history,
    verified_predecessors=None,
):
    """A renamed issuer keeps its own ISIN; a reused ticker cannot inherit another issuer's bars."""
    prev_by_isin = dict(zip(old_members["isin"], old_members.symbol))
    prev_by_isin.update({s: s for s in market.BENCHMARKS})
    dates = sorted(previous.loc[previous.symbol.eq("NIFTY 50"), "day"].unique())
    old_groups = {s: g for s, g in previous.groupby("symbol", sort=False)}
    parts, resets = [], []
    for r in current.itertuples(index=False):
        old_name = prev_by_isin.get(r.isin)
        subjects = events.get((r.isin, r.symbol), set())
        if old_name is None and any(SUBDIVISION.search(s) for s, _ in subjects):
            old_id = (verified_predecessors or {}).get(r.isin)
            if old_id in prev_by_isin:
                old_name = prev_by_isin[old_id]
            elif r.symbol in old_groups:
                raise market.MarketError(
                    f"Subdivision on {r.day} needs published predecessor ISIN for {r.isin}"
                )
        if old_name is None:
            base = new_history(r.isin, r.symbol, dates)
        else:
            base = old_groups[old_name]
        reset = r.isin in old_resets
        if reset:
            base = base.iloc[:0]
        if len(base):
            base, gap = adjusted_previous(
                base, subjects, float(base.close.iloc[-1]), releases.get(r.isin, {})
            )
            if gap:
                resets.append(r.isin)
            if len(base):
                parts.append(base.assign(symbol=r.symbol))
    parts.append(current[["day", "symbol", *FIELDS]])
    all_rows = pd.concat(parts, ignore_index=True)
    return all_rows[all_rows.day.isin([*dates[-399:], current.day.iloc[0]])].copy(), resets
