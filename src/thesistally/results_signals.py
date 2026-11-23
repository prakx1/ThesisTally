"""Point-in-time earnings surprise, announcement reaction and research-only order plans."""

from __future__ import annotations

import bisect
import fcntl
import io
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import stdev
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import market, signals
from .discovery_guard import REPO
from .discovery_registry import digest, encoded, families, write_once
from .discovery_worker import archive_root
from .range_signals import MARKET_VIEW, SNAPSHOT, read_session

IST = ZoneInfo("Asia/Kolkata")
PLAN_COLUMNS = [
    "day",
    "isin",
    "symbol",
    "sector",
    "usable_session",
    "entry_session",
    "filing_id",
    "period_end",
    "basis",
    "announced_at",
    "sue",
    "sue_threshold",
    "peers",
    "reaction",
    "close",
    "atr14",
    "turnover",
]


def timestamp(value):
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise ValueError("Filing time must include its exchange time zone")
    return instant.astimezone(IST)


def usable(instant, calendar):
    local = instant.astimezone(IST)
    idx = bisect.bisect_left(calendar, local.date())
    if idx < len(calendar) and calendar[idx] == local.date() and local.time() > time(15, 30):
        idx += 1
    return calendar[idx] if idx < len(calendar) else None


def month_id(period):
    return period.year * 12 + period.month


def score(current, eps_by_month, cutoff, counts=None):
    """Only the original, same-basis observations published by the usable close enter SUE."""
    end = month_id(date.fromisoformat(current["period_end"]))
    values = []
    for idx in range(13):
        item = eps_by_month.get(end - 3 * idx)
        if not item or item["eps"] is None or timestamp(item["announced_at"]) > cutoff:
            break
        value = Decimal(str(item["eps"]))
        if not value.is_finite():
            break
        values.append(value)
    if counts is not None:
        counts[f"consecutive_original_eps_{len(values)}"] += 1
    if len(values) != 13:
        return None
    deltas = [values[i] - values[i + 4] for i in range(9)]
    sd = stdev(deltas[1:])
    if not sd and counts is not None:
        counts["zero_prior_change_variance"] += 1
    return float(deltas[0] / sd) if sd > 0 else None


def first_eps_session(originals):
    initial = min(d for d, _ in originals)
    known = [(d, r) for d, r in originals if r["eps"] is not None]
    if not known:
        return initial
    first, release = min(known, key=lambda x: (x[0], timestamp(x[1]["announced_at"])))
    if first == initial:
        return initial
    clock = timestamp(release["announced_at"])
    for day, previous in originals:
        if day >= first:
            continue
        start, end = previous.get("eps_absence_from"), previous.get("eps_absence_before")
        if not (
            start
            and end
            and previous.get("eps_absence_evidence")
            and timestamp(start) <= timestamp(previous["announced_at"]) <= clock < timestamp(end)
        ):
            return initial
    return first


def announcements(rows, calendar, membership):
    history, events = defaultdict(dict), defaultdict(list)
    counts = Counter()
    for row in rows:
        if row["first_reported"] is not True:
            continue
        instant = timestamp(row["announced_at"])
        period = date.fromisoformat(row["period_end"])
        if instant.date() < period or instant.date() > calendar[-1]:
            continue
        group = history[row["isin"], row["basis"]]
        if month_id(period) in group:
            raise ValueError(f"Duplicate original for {row['isin']} {row['basis']} {period}")
        group[month_id(period)] = row
        day = usable(instant, calendar)
        if day is None or instant.date() < calendar[0]:
            continue
        if str(day) != row["usable_session"]:
            raise ValueError("Filing calendar disagrees with certified exchange sessions")
        events[row["isin"], period].append((day, row))
    releases = defaultdict(list)
    for (isin, _), originals in events.items():
        day = first_eps_session(originals)
        if isin not in membership(day):
            counts["nonmember_at_release"] += 1
            continue
        preferred = min(
            (r for d, r in originals if d == day),
            key=lambda r: (
                r["eps"] is None,
                r["basis"] != "consolidated",
                timestamp(r["announced_at"]),
            ),
        )
        if preferred.get("earlier_unresolved_other_basis"):
            counts["unverified_first_eps_session"] += 1
            continue
        cutoff = datetime.combine(day, time(15, 30), tzinfo=IST)
        sue = score(preferred, history[isin, preferred["basis"]], cutoff, counts)
        if sue is None:
            counts["unavailable_sue"] += 1
            continue
        releases[day].append({**preferred, "sue": sue})
    ranked, past = [], []
    for day, batch in sorted(releases.items()):
        cutoff = datetime.combine(day, time(15, 30), tzinfo=IST) - timedelta(days=90)
        past.extend((timestamp(r["announced_at"]), r["sue"]) for r in batch)
        past = [(t, s) for t, s in past if t > cutoff]
        boundary = float(np.quantile([s for _, s in past], 0.80))
        for row in batch:
            if row["sue"] >= boundary:
                ranked.append({**row, "sue_threshold": boundary, "peers": len(past)})
            else:
                counts["below_sue_percentile"] += 1
    counts["sue_scored"] = sum(map(len, releases.values()))
    return ranked, counts


def price_setup(frame, benchmark, reaction_days):
    frame = frame.loc[: reaction_days[-1]]
    window = frame.reindex(reaction_days)
    idx = benchmark.reindex(reaction_days)
    if window.close.isna().any() or idx.isna().any() or min(window.close.min(), idx.min()) <= 0:
        return None, "reaction_missing"
    reaction = window.close.iloc[-1] / window.close.iloc[0] - idx.iloc[-1] / idx.iloc[0]
    if reaction <= 0:
        return None, "nonpositive_reaction"
    turnover = (frame.close * frame.volume).tail(20)
    if len(turnover) != 20 or turnover.isna().any():
        return None, "liquidity_unknown"
    if turnover.median() < signals.rules(revision="r2")["filters"]["min_turnover_inr"]:
        return None, "illiquid"
    prev = frame.close.ffill().shift()
    tr = np.fmax(
        frame.high - frame.low, np.fmax((frame.high - prev).abs(), (frame.low - prev).abs())
    )
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14, ignore_na=True).mean().iloc[-1]
    if not math.isfinite(atr) or atr <= 0:
        return None, "atr_unknown"
    return {
        "reaction": float(reaction),
        "close": float(window.close.iloc[-1]),
        "atr14": float(atr),
        "turnover": float(turnover.median()),
    }, None


def verify_filing_products(root, coverage_bytes):
    from .fundamental_coverage import ledger_bytes

    coverage = json.loads(coverage_bytes)
    canonical = (root / "first-releases.jsonl").read_bytes()
    for filename in (
        "first-releases.jsonl",
        "quarter-coverage.csv",
        "stock-coverage.csv",
        "stock-year-coverage.csv",
    ):
        raw = canonical if filename == "first-releases.jsonl" else (root / filename).read_bytes()
        if digest(raw) != coverage["products"][filename]:
            raise ValueError(f"Quarterly coverage product changed: {filename}")
    for exchange in ("nse", "bse"):
        ledger_bytes(root, exchange, coverage["filing_sources"][exchange]["sha256"])
    return canonical


def publish_signals(root, raw, proof):
    """Retain every decision vintage when more original filings become available."""
    receipt = encoded(proof)
    versions = root / "signal-history"
    versions.mkdir(exist_ok=True)
    with (versions / ".publish.lock").open("a") as lock, TemporaryDirectory(dir=root) as scratch:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if digest((root / "coverage.json").read_bytes()) != proof["coverage_sha256"]:
            raise ValueError("Coverage changed while calculating signals")
        for name, value in (("signals.parquet", raw), ("signals-receipt.json", receipt)):
            write_once(versions / digest(receipt) / name, value)
            tmp = Path(scratch) / name
            tmp.write_bytes(value)
            tmp.replace(root / name)


def generate(root, archive):
    registry = families()["results-d-v1"]
    view = archive / MARKET_VIEW
    manifest = json.loads((view / "preflight.json").read_bytes())
    if not manifest["complete"] or manifest["snapshot_sha256"] != SNAPSHOT:
        raise ValueError("Certified D price and membership snapshot required")
    session_hashes = manifest["session_manifests"]
    calendar = sorted(date.fromisoformat(d) for d in session_hashes)
    folder = view / "market"
    coverage = (root / "coverage.json").read_bytes()
    source = verify_filing_products(root, coverage)
    rows = [json.loads(line) for line in source.splitlines() if line]
    cache = {}

    def membership(day):
        if day not in cache:
            session = folder / "sessions" / str(day)
            proof = (session / "manifest.json").read_bytes()
            members = (session / "members.csv").read_bytes()
            if (
                digest(proof) != session_hashes[str(day)]
                or digest(members) != json.loads(proof)["sha256"]["members.csv"]
            ):
                raise ValueError("Changed signal-date membership")
            cache[day] = set(market.read_members(members)["isin"])
        return cache[day]

    ranked, counts = announcements(rows, calendar, membership)
    batches = defaultdict(list)
    for row in ranked:
        batches[row["usable_session"]].append(row)
    output = []
    for usable_day, batch in sorted(batches.items()):
        i = bisect.bisect_left(calendar, date.fromisoformat(usable_day))
        if i < 2 or i + 2 >= len(calendar):
            counts["reaction_or_entry_outside_snapshot"] += len(batch)
            continue
        day = calendar[i + 1]
        bars, member_frame = read_session(folder, day, session_hashes[str(day)])
        members = member_frame.set_index("isin")
        quotes = {
            symbol: grp.set_index("day").sort_index() for symbol, grp in bars.groupby("symbol")
        }
        bench = quotes["NIFTY 200"].close
        seen = set()
        for row in sorted(batch, key=lambda r: (-r["sue"], r["isin"], r["period_end"])):
            isin = row["isin"]
            if isin not in members.index:
                counts["nonmember_at_signal"] += 1
                continue
            symbol = members.loc[isin, "symbol"]
            # Preserve exchange-session gaps when evaluating trailing liquidity and ATR.
            frame = quotes[symbol].reindex(bench.index)
            data, reason = price_setup(frame, bench, calendar[i - 2 : i + 2])
            if reason:
                counts[reason] += 1
                continue
            if isin in seen:
                counts["duplicate_issuer_same_trade_day"] += 1
                continue
            seen.add(isin)
            output.append(
                {
                    **row,
                    **data,
                    "day": str(day),
                    "entry_session": str(calendar[i + 2]),
                    "symbol": symbol,
                    "sector": members.loc[isin, "sector"],
                    "filing_id": row["sha256"],
                }
            )
    plans = pd.DataFrame(output, columns=PLAN_COLUMNS)
    buf = io.BytesIO()
    plans.to_parquet(buf, index=False)
    raw = buf.getvalue()
    proof = {
        "signals_sha256": digest(raw),
        "signal_method_sha256": digest(Path(__file__).read_bytes()),
        "source_sha256": digest(source),
        "coverage_sha256": digest(coverage),
        "snapshot": SNAPSHOT,
        "registration_commit": registry["registration_commit"],
        "originals": len(rows),
        "calendar_end": str(calendar[-1]),
        "plans": len(plans),
        "funnel": dict(counts),
    }
    publish_signals(root, raw, proof)
    return proof


if __name__ == "__main__":
    print(json.dumps(generate(REPO / "data/fundamentals", archive_root()), indent=2))
