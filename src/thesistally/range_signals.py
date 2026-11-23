"""Vintage monthly selection and close-of-session signals for preregistered Range Bouncers."""

import io
import itertools
import json
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from . import market, range_history, signals, stage3_market
from .evaluation import walk_windows
from .pilot_data import write_json

MARKET_VIEW = Path("stage3/close-verification/views/a79daa45cf9271df5dbf/stage3")
SNAPSHOT = "9033de9d28bdc23dcbe7207e63f029502bb83be4230c37dd30bb6a8027c8dea5"
MIN_TURNOVER = signals.rules(revision="r2")["filters"]["min_turnover_inr"]


def count_traversals(position):
    armed = False
    count = 0
    for value in position:
        if value <= 0.20:
            armed = True
        elif armed and value >= 0.80:
            count += 1
            armed = False
    return count


def classify(frame, expected, turnover):
    row = {
        "cohort": "unknown",
        "reason": "",
        "total_return": None,
        "traversals": None,
        "previous_low": None,
        "evaluation_min_close": None,
    }
    if np.isfinite(turnover) and turnover < MIN_TURNOVER:
        return {**row, "cohort": "ineligible", "reason": "monthly_illiquid"}
    if not np.isfinite(turnover):
        return {**row, "reason": "monthly_liquidity_unknown"}
    if len(expected) != 500 or not frame.index.equals(expected):
        return {**row, "reason": "incomplete_500_session_history"}
    payouts = frame["cash_yield"].iloc[-250:]
    if payouts.isna().any():
        return {**row, "reason": "dividend_terms_or_coverage_unknown"}
    # Share units affect the passive holder's wealth. A non-subscribed right pays
    # no cash and does not increase the number of units, regardless of chart rebasing.
    holder_price = frame.close * frame.share_factor
    total = holder_price.iloc[-1] / holder_price.iloc[-251] * np.prod(1 + payouts) - 1
    low = frame.adj_low.rolling(60).min()
    high = frame.adj_high.rolling(60).max()
    interval = high - low
    position = ((frame.adj_close - low) / interval.where(interval.gt(0))).iloc[-250:]
    count = count_traversals(position)
    previous = float(frame.adj_low.iloc[:250].min())
    minimum = float(frame.adj_close.iloc[-250:].min())
    reasons = [
        s
        for ok, s in [
            (-0.15 - 1e-12 <= total <= 0.15 + 1e-12, "total_return_outside_band"),
            (count >= 3, "fewer_than_three_traversals"),
            (minimum >= 0.90 * previous, "collapse"),
        ]
        if not ok
    ]
    return {
        **row,
        "cohort": "other" if reasons else "range",
        "reason": "|".join(reasons),
        "total_return": float(total),
        "traversals": count,
        "previous_low": previous * frame.price_factor.iloc[-1],
        "evaluation_min_close": minimum * frame.price_factor.iloc[-1],
    }


def read_session(archive, day, expected):
    folder = archive / "sessions" / str(day)
    raw = (folder / "manifest.json").read_bytes()
    if market.sha(raw) != expected:
        raise market.MarketError(f"Changed C market manifest {day}")
    manifest = json.loads(raw)
    bars, names = [(folder / f).read_bytes() for f in ("bars.parquet", "members.csv")]
    if any(
        market.sha(x) != manifest["sha256"][f]
        for x, f in [(bars, "bars.parquet"), (names, "members.csv")]
    ):
        raise market.MarketError(f"Changed daily price or membership evidence {day}")
    frames = pd.read_parquet(
        io.BytesIO(bars), columns=["day", "symbol", "high", "low", "close", "volume"]
    )
    names = market.read_members(names)
    if frames.day.max() != day or names["isin"].duplicated().any():
        raise market.MarketError(f"Wrong daily inputs for C {day}")
    return frames, names


def close_features(bars, members, *, as_of=None):
    if as_of is not None:
        bars = bars.loc[bars.day.le(as_of)]
    prices = stage3_market._panels(bars).sort_index()
    close, low, high, volume = [
        prices[key].reindex(columns=members.symbol) for key in ["close", "low", "high", "volume"]
    ]
    valid = close.tail(60).count().eq(60)
    low60, high60 = low.tail(60).min(), high.tail(60).max()
    width = high60 - low60
    price = close.iloc[-1]
    position = (price - low60) / width.where(width > 0)
    previous = close.ffill().shift()
    distances = np.fmax(high - low, np.fmax((high - previous).abs(), (low - previous).abs()))
    atr = distances.ewm(alpha=1 / 14, adjust=False, min_periods=14, ignore_na=True).mean().iloc[-1]
    turnover = (close * volume).tail(20)
    result = members.set_index("symbol").copy()
    result["close"], result["low60"], result["high60"] = price, low60, high60
    result["position60"], result["rsi2"], result["atr14"] = (
        position,
        stage3_market.wilder_rsi2(close),
        atr,
    )
    result["turnover"] = turnover.median().where(turnover.count().eq(20))
    observed = valid & width.gt(0) & result.rsi2.notna() & atr.gt(0)
    return result[observed & position.le(0.15) & result.rsi2.lt(10)].reset_index(), int(
        (~observed).sum()
    )


def daily_worker(args):
    archive, tagged = args
    result, coverage = [], []
    for d, digest in tagged:
        bars, names = read_session(archive, d, digest)
        frame, missing = close_features(bars, names, as_of=d)
        result.extend(frame.assign(day=str(d)).to_dict("records"))
        coverage.append(
            {
                "day": str(d),
                "members": len(names),
                "daily_unobservable": missing,
                "raw_setups": len(frame),
            }
        )
    return result, coverage


def monthly_table(data, archive, calendar, selections, manifests):
    tape = range_history.history(data, archive, calendar)
    tape["cash_yield"], sources, issues, events = range_history.cash_yields(data, tape, calendar)
    for field in ["low", "high", "close"]:
        tape["adj_" + field] = tape[field] / tape.price_factor
    groups = {key: g.set_index("day").sort_index() for key, g in tape.groupby("chain", sort=False)}
    lookup = tape.set_index(["day", "isin"]).chain
    result, crosschecked = [], 0
    for d in selections:
        bars, members = read_session(archive, d, manifests[str(d)])
        recent = bars[
            bars.day.isin(calendar[calendar.index(d) - 19 : calendar.index(d) + 1])
        ].copy()
        turnover = (recent.close * recent.volume).groupby(recent.symbol).agg(["median", "count"])
        month_bars = {s: g.set_index("day") for s, g in bars.groupby("symbol")}
        expected = pd.Index(calendar[calendar.index(d) - 499 : calendar.index(d) + 1], name="day")
        for sec in members.itertuples():
            chain = lookup.get((d, sec.isin))
            frame = groups[chain].loc[expected[0] : d] if chain is not None else pd.DataFrame()
            native = month_bars.get(sec.symbol)
            value = (
                turnover.at[sec.symbol, "median"]
                if sec.symbol in turnover.index and turnover.at[sec.symbol, "count"] == 20
                else np.nan
            )
            record = classify(frame, expected, value)
            # The certified vintage is authoritative. An independent long-history
            # reconstruction must agree on every available overlapping OHLC.
            if len(frame) == 500 and native is not None:
                overlap = native.index.intersection(frame.index).sort_values()
                native_days = pd.Index(
                    calendar[max(0, calendar.index(d) - 399) : calendar.index(d) + 1]
                )
                if not native_days.isin(overlap).all():
                    record.update(cohort="unknown", reason="certified_series_discontinuity")
                elif not all(
                    np.allclose(
                        frame.loc[overlap, "adj_" + field] * frame.price_factor.iloc[-1],
                        native.loc[overlap, field],
                        rtol=1e-9,
                        atol=1e-7,
                    )
                    for field in ("low", "high", "close")
                ):
                    record.update(cohort="unknown", reason="certified_adjustment_disagreement")
                else:
                    crosschecked += 1
            result.append(
                dict(day=str(d), isin=sec.isin, symbol=sec.symbol, turnover=value, **record)
            )
    return pd.DataFrame(result), sources, issues, events, crosschecked


def assign_monthly(raw, coverage, monthly, observed_days, transitions):
    selected = pd.DataFrame({"day": list(map(str, observed_days))})
    selected["selection_day"] = selected.day.where(selected.day.isin(monthly.day)).ffill()
    entries = pd.DataFrame(raw).merge(selected, on="day", validate="many_to_one")
    entries["selection_isin"] = entries["isin"]
    for tr in sorted(transitions, key=lambda tr: tr["first_printed_new_isin"], reverse=True):
        # The old and new ISIN first become a proven continuing unit series
        # at the first published new-ISIN close. Never transport merger cohorts.
        if tr["mode"] not in ("joined_on_new_print", "verified_shared"):
            continue
        issued = tr["first_printed_new_isin"]
        eligible = (
            entries.selection_isin.eq(tr["new_isin"])
            & entries.selection_day.lt(issued)
            & entries.day.ge(issued)
        )
        entries.loc[eligible, "selection_isin"] = tr["old_isin"]
    labels = monthly[["day", "isin", "cohort", "reason"]].rename(
        columns={"day": "selection_day", "isin": "selection_isin"}
    )
    entries = entries.merge(
        labels, on=["selection_day", "selection_isin"], how="left", validate="many_to_one"
    )
    entries["reason"] = entries.reason.fillna("not_member_at_month_end")
    entries["cohort"] = entries.cohort.fillna("unknown")
    entries["monthly_cohort"] = entries.cohort
    known = entries.cohort.isin(["range", "other"])
    missing = known & entries.turnover.isna()
    illiquid = known & entries.turnover.lt(MIN_TURNOVER)
    entries.loc[missing, ["cohort", "reason"]] = ["unknown", "daily_liquidity_unknown"]
    entries.loc[illiquid, ["cohort", "reason"]] = ["ineligible", "daily_illiquid"]
    coverage = pd.DataFrame(coverage)
    hist = entries.groupby(["day", "cohort"]).size().unstack(fill_value=0)
    coverage = coverage.merge(hist, on="day", how="left").fillna(0)
    return entries, coverage


def generate(data=Path("data"), *, workers=3):
    archive = data / MARKET_VIEW / "market"
    preflight = json.loads((archive.parent / "preflight.json").read_bytes())
    if preflight["snapshot_sha256"] != SNAPSHOT or not preflight["complete"]:
        raise market.MarketError("Range Bouncers requires the preregistered seven-release archive")
    manifests = preflight["session_manifests"]
    calendar = sorted(map(date.fromisoformat, manifests))
    windows = walk_windows(calendar, 3, 1, None)
    # Supply the last training close for callers explicitly seeding January orders.
    anchor = date.fromisoformat(windows[0][2][-1])
    observed_days = calendar[calendar.index(anchor) :]
    # A truncated terminal month is not an exchange month end.
    selections = [
        d
        for d, following in itertools.pairwise(calendar)
        if d >= anchor and d.month != following.month
    ]
    out = data / "range-bouncers"
    out.mkdir(parents=True, exist_ok=True)
    parameters = [
        (archive, [(d, manifests[str(d)]) for d in observed_days if d.year == y])
        for y in sorted({d.year for d in observed_days})
    ]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        tasks = [pool.submit(daily_worker, args) for args in parameters]
        monthly, sources, issues, events, checked = monthly_table(
            data, archive, calendar, selections, manifests
        )
        raw, coverage = [], []
        for task in tasks:
            r, c = task.result()
            raw.extend(r)
            coverage.extend(c)
    transitions_path = data / "baseline/prices-and-membership.json"
    transitions = json.loads(transitions_path.read_bytes())["split_identifier_transitions"]
    entries, coverage = assign_monthly(raw, coverage, monthly, observed_days, transitions)
    products = {}
    for name, frame in [("monthly", monthly), ("signals", entries), ("coverage", coverage)]:
        file = out / (name + ".parquet")
        with market.staging(out) as tmp:
            path = tmp / file.name
            frame.to_parquet(path, index=False)
            path.replace(file)
        products[file.name] = {"sha256": market.sha(file.read_bytes()), "rows": len(frame)}
    write_json(
        out / "dividend-evidence.json",
        {"official_archives": sources, "unresolved_ex_dates": issues},
    )
    write_json(out / "cash-events.json", {"version": "range-dated-cash-v1", "events": events})
    proof = {
        "version": "range-bouncers-signals-v1",
        "snapshot_sha256": SNAPSHOT,
        "preregistration": "8a67849",
        "cash_events_sha256": market.sha((out / "cash-events.json").read_bytes()),
        "split_lineage_evidence_sha256": market.sha(transitions_path.read_bytes()),
        "start": str(anchor),
        "end": str(calendar[-1]),
        "sessions": len(observed_days),
        "month_ends": len(selections),
        "monthly_histories_matching_400_published_sessions": checked,
        "dividend_evidence_sha256": market.sha((out / "dividend-evidence.json").read_bytes()),
        "annual_bhavcopy_derivatives": {
            p.name: market.sha(p.read_bytes())
            for p in sorted((data / "sources/adjusted").glob("*.parquet"))
        },
        "monthly_reasons": monthly.reason.value_counts().to_dict(),
        "products": products,
    }
    write_json(out / "manifest.json", proof)
    return proof


if __name__ == "__main__":
    print(generate(), flush=True)
