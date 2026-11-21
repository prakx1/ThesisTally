"""Publish exchange-first daily bars, with point-in-time corporate action adjustments."""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd

from . import history, market
from .constituents import load_daily
from .exchange import AMOUNT, context_file

FIELDS = ["open", "high", "low", "close", "volume"]


def adjustment(subject, face_value, prior):
    """Price multiplier for observations before the ex-date, and split share multiplier."""
    factor = shares = 1.0
    if re.search(r"split|sub.division|consolidation", subject, re.IGNORECASE):
        match = re.search(
            r"from\s+(?:rs\.?|re\.?)?\s*([\d.]+).*?to\s+(?:rs\.?|re\.?)?\s*([\d.]+)",
            subject,
            re.IGNORECASE,
        )
        if not match:
            return None
        old, new = map(float, match.groups())
        if min(old, new) <= 0:
            return None
        factor *= new / old
        shares *= old / new
    if re.search("bonus", subject, re.IGNORECASE):
        match = re.search(
            r"bonus(?:\s+(?:issue|shares?))?\s*[-:]?\s*(\d+)\s*:\s*(\d+)", subject, re.IGNORECASE
        )
        if not match or float(match[2]) <= 0:
            return None
        multiplier = 1 + float(match[1]) / float(match[2])
        shares *= multiplier
        factor /= multiplier
    if re.search("rights", subject, re.IGNORECASE):
        match = re.search(
            r"rights(?:\s+issue)?\s*[-:]?\s*(\d+)\s*:\s*(\d+).*?@\s*(premium|at par|rs\.?|re\.?)\s*(?:rs\.?)?\s*([\d,.]+)?",
            subject,
            re.IGNORECASE,
        )
        if not match or not prior or float(match[2]) == 0:
            return None
        ratio = int(match[1]) / int(match[2])
        try:
            price = float((match[4] or "0").replace(",", ""))
            if match[3].lower() in {"premium", "at par"}:
                price += float(face_value)
        except (ValueError, TypeError):
            return None
        factor *= (prior + ratio * price) / (prior * (1 + ratio))
    return factor, shares


def reference_rows(root):
    # Source replacements invalidate the cache even if their names and sizes are unchanged.
    stamps = tuple(
        sorted(
            (p.name, s.st_mtime_ns, s.st_ctime_ns, s.st_size)
            for p in (root / "sources" / "reference").glob("*.json")
            if (s := p.stat())
        )
    )
    return _reference_rows(root, stamps)


@lru_cache(maxsize=2)
def _reference_rows(root, stamps):
    ref = root / "sources" / "reference"
    actions = defaultdict(list)
    meetings = defaultdict(list)
    availability = defaultdict(list)
    vix = {}
    for label in ("actions", "meetings", "vix"):
        for path in sorted(ref.glob(label + "-*.json")):
            if path.name.endswith(".source.json"):
                continue
            raw = path.read_bytes()
            meta = json.loads(path.with_suffix(".json.source.json").read_text())
            if market.sha(raw) != meta["sha256"]:
                raise market.MarketError(f"Corrupt {path.name}")
            rows = json.loads(raw)
            if label == "vix":
                for row in rows["data"]:
                    d = datetime.strptime(row["EOD_TIMESTAMP"], "%d-%b-%Y").date()
                    vix[d] = [
                        float(row["EOD_" + k + "_INDEX_VAL"])
                        for k in ("OPEN", "HIGH", "LOW", "CLOSE")
                    ]
                continue
            a, b = path.stem.split("-", 1)[1].split("_")
            availability[label].append((date.fromisoformat(a), date.fromisoformat(b)))
            for row in rows:
                if label == "actions":
                    if row.get("series") == "EQ" and row.get("exDate") not in (None, "-"):
                        d = datetime.strptime(row["exDate"], "%d-%b-%Y").date()
                        actions[d, row["symbol"]].append(row)
                elif re.search(
                    "results|financial statements",
                    str(row.get("bm_purpose")) + " " + str(row.get("bm_desc")),
                    re.IGNORECASE,
                ):
                    try:
                        announced = datetime.strptime(row["bm_timestamp"], "%d-%b-%Y %H:%M:%S")
                        meeting = datetime.strptime(row["bm_date"], "%d-%b-%Y").date()
                    except (KeyError, ValueError, TypeError):
                        continue
                    meetings[row["bm_symbol"]].append((meeting, announced))
    for events in meetings.values():
        events.sort()
    return actions, meetings, availability, vix


def prepare_tape(root, dates, members, actions, vix, progress):
    wanted = set(members["isin"])
    # Symbols additionally connect only an officially documented ex-date ISIN change.
    symbols = set(members.symbol)
    chunks = []
    indices = {}
    issues = []
    for i, d in enumerate(dates):
        try:
            bhav, idx = load_daily(root, d)
        except (KeyError, ValueError, OSError) as exc:
            issues.append({"day": str(d), "reason": str(exc)})
            continue
        equity = bhav[
            (bhav.ISIN.isin(wanted) | bhav.TckrSymb.isin(symbols))
            & bhav.SctySrs.str.strip().isin(["EQ", "BE", "BZ"])
        ].copy()
        equity = equity.rename(
            columns=dict(
                zip(
                    ["OpnPric", "HghPric", "LwPric", "ClsPric", "TtlTradgVol", "ISIN", "TckrSymb"],
                    [*FIELDS, "isin", "symbol"],
                )
            )
        )
        equity = equity[["isin", "symbol", *FIELDS]].assign(day=d)
        for key in FIELDS:
            equity[key] = pd.to_numeric(equity[key], errors="coerce")
        chunks.append(equity)
        records = []
        for sym in market.BENCHMARKS:
            if sym == "INDIA VIX" and d in vix:
                ohlc = vix[d]
            elif sym in idx.index:
                row = idx.loc[sym]
                try:
                    ohlc = [
                        float(row[k])
                        for k in (
                            "Open Index Value",
                            "High Index Value",
                            "Low Index Value",
                            "Closing Index Value",
                        )
                    ]
                except (ValueError, TypeError):
                    continue
            else:
                continue
            records.append({"day": d, "symbol": sym, "isin": sym, **dict(zip(FIELDS, [*ohlc, 0]))})
        indices[d] = len(records)
        chunks.append(pd.DataFrame(records))
        if i and i % 700 == 0:
            progress(f"Normalized exchange bars for {i}/{len(dates)} sessions")
    if not chunks:
        raise market.MarketError("No dated exchange prices available.")
    tape = pd.concat(chunks, ignore_index=True).sort_values(["day", "symbol"])
    tape = tape.drop_duplicates(["day", "isin"], keep=False).reset_index(drop=True)
    bad = (
        tape[FIELDS].isna().any(axis=1)
        | (tape.low <= 0)
        | (tape.volume < 0)
        | (tape.high < tape[["low", "open", "close"]].max(axis=1))
        | (tape.low > tape[["high", "open", "close"]].min(axis=1))
    )
    close_only = bad & (tape.symbol == "INDIA VIX") & (tape.close > 0)
    for r in tape[close_only].itertuples():
        issues.append(
            {
                "day": str(r.day),
                "symbol": r.symbol,
                "isin": r.isin,
                "reason": "benchmark_close_only",
            }
        )
    tape.loc[close_only, ["open", "high", "low"]] = np.nan
    bad &= ~close_only
    for r in tape[bad].itertuples():
        issues.append(
            {"day": str(r.day), "symbol": r.symbol, "isin": r.isin, "reason": "invalid_ohlcv"}
        )
    tape = tape[~bad].copy()
    tape["price_factor"] = 1.0
    tape["share_factor"] = 1.0
    tape["chain"] = tape["isin"]
    tape["prior_close"] = tape.groupby("isin", sort=False).close.shift()
    tape["unexplained_jump"] = (tape.close / tape.prior_close - 1).abs() > 0.20
    # Index moves are retained as observations. Quarantine concerns equity entries.
    tape.loc[tape.symbol.isin(market.BENCHMARKS), "unexplained_jump"] = False
    events = {
        (d, s)
        for d, s in actions
        if any(
            re.search("bonus|split|rights|consolidation|demerger", r["subject"], re.IGNORECASE)
            for r in actions[d, s]
        )
    }
    previous = tape.groupby("symbol", sort=False)[["isin", "close"]].shift()
    event_idx = pd.MultiIndex.from_frame(tape[["day", "symbol"]]).isin(events)
    lineage = {}
    for idx, r in tape[event_idx].iterrows():
        terms = sorted({(x["subject"], x.get("faceVal")) for x in actions[r.day, r.symbol]})
        old = previous.loc[idx]
        factor = shares = 1.0
        for subject, face in terms:
            if not re.search("bonus|split|rights|consolidation|demerger", subject, re.IGNORECASE):
                continue
            adj = adjustment(subject, face, old.close)
            if adj is None or "demerger" in subject.lower():
                issues.append(
                    {
                        "day": str(r.day),
                        "symbol": r.symbol,
                        "isin": r["isin"],
                        "reason": "unresolved_action",
                        "subject": subject,
                    }
                )
                # Restart history on an unpriced entitlement.
                lineage[(r["isin"], r.day)] = r["isin"] + "@" + str(r.day)
                continue
            factor *= adj[0]
            shares *= adj[1]
            if (
                pd.notna(old["isin"])
                and old["isin"] != r["isin"]
                and re.search("split|consolidation|bonus", subject, re.IGNORECASE)
            ):
                lineage[(r["isin"], r.day)] = old["isin"]
        tape.loc[idx, ["price_factor", "share_factor", "unexplained_jump"]] = [
            factor,
            shares,
            False,
        ]
    for (isin, day), prior in sorted(lineage.items(), key=lambda x: x[0][1]):
        prev_chain = tape.loc[(tape["isin"] == prior) & (tape.day < day), "chain"]
        tape.loc[(tape["isin"] == isin) & (tape.day >= day), "chain"] = (
            prev_chain.iloc[-1] if len(prev_chain) else prior
        )
    for row in tape[tape.unexplained_jump].itertuples():
        issues.append(
            {
                "day": str(row.day),
                "symbol": row.symbol,
                "isin": row.isin,
                "reason": "unexplained_move_over_20pct",
                "prior_close": row.prior_close,
                "close": row.close,
            }
        )
    tape["price_factor"] = tape.groupby("chain", sort=False).price_factor.cumprod()
    tape["share_factor"] = tape.groupby("chain", sort=False).share_factor.cumprod()
    return tape, issues


def context_for(d, rows, meetings, actions, coverage, issues):
    cutoff = datetime.combine(d, time(15, 45))
    out = {}
    for r in rows.itertuples():
        known = any(a <= d and d + timedelta(days=30) <= b for a, b in coverage["meetings"])
        upcoming = [
            (when - d).days
            for when, stamp in meetings.get(r.symbol, [])
            if d <= when <= d + timedelta(days=30) and stamp <= cutoff
        ]
        notices = {
            x["subject"]
            for x in actions.get((d, r.symbol), [])
            if x.get("isin") == r.isin or x.get("symbol") == r.symbol
        }
        dividend = 0.0
        other = []
        for subject in sorted(notices):
            amounts = AMOUNT.findall(subject)
            if (
                "dividend" in subject.lower()
                and amounts
                and not re.search("bonus|split|rights", subject, re.IGNORECASE)
            ):
                dividend += sum(float(x.replace(",", "")) for x in amounts)
            else:
                other.append(subject)
        out[r.symbol] = {
            "surveillance": "unknown",
            "floor": None,
            "ceiling": None,
            "earnings_in_days": min(upcoming, default=None),
            "earnings_coverage": "known" if known else "unknown",
            "dividend": dividend,
            "other_actions": other,
            "quarantined": (d, r.isin) in issues,
        }
    return out


def publish(root, dates, requested, progress=print):
    pending = [d for d in requested if not (root / "sessions" / str(d) / "manifest.json").is_file()]
    audit_file = root / "sources" / "adjusted" / "quarantine.json"
    if not pending and audit_file.is_file():
        return {
            "published": 0,
            "unpublished": {},
            "quarantine_entries": len(json.loads(audit_file.read_text())),
        }
    requested = pending
    members = pd.read_parquet(root / "universes" / "reconstructed.parquet")
    groups = dict(tuple(members.groupby("day")))
    actions, meetings, coverage, vix = reference_rows(root)
    tape, issues = prepare_tape(root, dates, members, actions, vix, progress)
    dest = root / "sources" / "adjusted"
    dest.mkdir(parents=True, exist_ok=True)
    quarantined = {(date.fromisoformat(r["day"]), r.get("isin")) for r in issues}
    # The adjusted series is expressed in common units and rebased only with factors known on the scan day.
    for k in market.PRICES:
        tape["adjusted_" + k] = tape[k] / tape.price_factor
    tape["adjusted_volume"] = tape.volume / tape.share_factor
    for y, g in tape.groupby(tape.day.map(lambda d: d.year)):
        with market.staging(dest) as tmp:
            p = tmp / f"{y}.parquet"
            g.to_parquet(p, index=False)
            p.replace(dest / p.name)
    with market.staging(dest) as tmp:
        p = tmp / "quarantine.json"
        p.write_text(json.dumps(issues, indent=2, default=str))
        p.replace(dest / p.name)
    # Dense slices share a tape across dates and keep archived daily files self-contained.
    axis = pd.Index(dates)
    tape = tape.set_index(["day", "chain"]).sort_index()
    if tape.index.duplicated().any():
        raise market.MarketError("Ambiguous ex-date ISIN chain in adjusted history.")
    panels = {
        k: tape[k].unstack().reindex(axis)
        for k in ["adjusted_" + x for x in FIELDS] + ["price_factor", "share_factor", "isin"]
    }
    price = panels["price_factor"]
    names = list(price.columns)
    position = {x: i for i, x in enumerate(names)}
    written = 0
    gaps = {}
    for d in requested:
        folder = root / "sessions" / str(d)
        if (folder / "manifest.json").is_file():
            continue
        if d not in groups:
            gaps[str(d)] = "membership unavailable"
            continue
        i = axis.get_loc(d)
        group = groups[d]
        id_to_chain = {v: c for c, v in panels["isin"].iloc[i].dropna().items()}
        ids = [*group["isin"], *market.BENCHMARKS]
        if any(s not in id_to_chain for s in ids):
            gaps[str(d)] = "missing or quarantined member or benchmark price"
            continue
        cols = [position[id_to_chain[s]] for s in ids]
        labels = [*group.symbol, *market.BENCHMARKS]
        first = max(0, i - 399)
        span = dates[first : i + 1]
        # At archive boundaries, replay explicitly checks the independent session calendar.
        if i == 0:
            continue
        block = pd.DataFrame(
            {"day": np.repeat(span, len(cols)), "symbol": np.tile(labels, len(span))}
        )
        for key in FIELDS:
            factor = (
                panels["share_factor" if key == "volume" else "price_factor"]
                .iloc[i, cols]
                .to_numpy(dtype=float)
            )
            block[key] = (
                panels["adjusted_" + key].iloc[first : i + 1, cols].to_numpy(dtype=float) * factor
            ).ravel()
        block = block.dropna(subset=["close"])
        block[block.day == d]
        past = dates[i - 1]
        if past not in block[block.symbol == "NIFTY 50"].day.values:
            gaps[str(d)] = "missing previous benchmark session"
            continue
        src = root / "sources" / "nse-daily" / str(d)
        datesrc = json.loads((src / "sources.json").read_text())
        ctx = context_for(d, group, meetings, actions, coverage, quarantined)
        cpath = context_file(root, d)
        if not cpath.exists():
            with market.staging(cpath.parent.parent) as tmp:
                payload = json.dumps(
                    {"day": str(d), "band_reference": str(past), "sources": {}, "symbols": ctx}
                ).encode()
                (tmp / "context.json").write_bytes(payload)
                (tmp / "context.sha256").write_text(market.sha(payload))
                tmp.rename(cpath.parent)
        with market.staging(folder.parent) as tmp:
            raw = history.normalize_bhav(
                history.extract_bhav((src / "bhavcopy.zip").read_bytes()), d
            )
            raw.to_csv(tmp / "bhavcopy.csv", index=False)
            shutil.copyfile(src / "indices.csv", tmp / "indices.csv")
            group.rename(
                columns={
                    "symbol": "Symbol",
                    "series": "Series",
                    "sector": "Industry",
                    "isin": "ISIN Code",
                }
            ).drop(columns="day").to_csv(tmp / "members.csv", index=False)
            block.to_parquet(tmp / "bars.parquet", index=False)
            hashes = {p.name: market.sha(p.read_bytes()) for p in tmp.iterdir()}
            meta = {
                "format": "exchange-history-v2",
                "day": str(d),
                "requested_start": str(span[0]),
                "prior_day": str(past),
                "verified_closes": len(group),
                "members": len(group),
                "candles": len(block),
                "instruments": len(labels),
                "source": "NSE bhavcopy, dated index weights and index changes",
                "source_files": datesrc,
                "price_basis": "NSE raw OHLCV with ex-date-rebased warm-up",
                "sha256": hashes,
            }
            (tmp / "manifest.json").write_text(json.dumps(meta, indent=2))
            tmp.rename(folder)
        written += 1
        if written % 300 == 0:
            progress(f"Published {written} verified historical sessions")
    return {"published": written, "unpublished": gaps, "quarantine_entries": len(issues)}
