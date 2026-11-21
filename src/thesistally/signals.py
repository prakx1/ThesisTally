"""Pure end-of-day detectors. All slices end at the completed scan session."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .exchange import decode, read_context
from .market import BENCHMARKS, MarketError, read_members, sessions, sha, verify


def rules(path: Path | None = None):
    raw = path.read_bytes() if path else files(__package__).joinpath("rules.yaml").read_bytes()
    cfg = yaml.safe_load(raw)
    cfg["config_hash"] = sha(raw)
    cfg["revision"] = f"{cfg['version']}-{sha(raw)[:12]}"
    if cfg["exits"]["target_r"] < 2 or not 0 < cfg["risk"]["fraction"] <= 1:
        raise MarketError("Rules require target >= 2R and a positive risk fraction <= 1.")
    mode = cfg["filters"].get("earnings_blackout_mode", "calendar_days")
    if mode not in {"calendar_days", "holding_period"}:
        raise MarketError("Unknown earnings blackout mode.")
    return cfg


def ema(close, period):
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


def features(raw, cfg):
    t, m, flt = cfg["trend"], cfg["momentum"], cfg["filters"]
    f = raw.sort_values("day").set_index("day").copy()
    for col in ("fast", "medium", "slow"):
        f[col] = ema(f.close, t[col])
    prev = f.close.shift()
    true_range = pd.concat(
        [f.high - f.low, (f.high - prev).abs(), (f.low - prev).abs()], axis=1
    ).max(axis=1)
    f["atr"] = true_range.ewm(alpha=1 / t["atr"], adjust=False, min_periods=t["atr"]).mean()
    f["turnover"] = (f.close * f.volume).rolling(flt["median_turnover_days"]).median()
    f["strength"] = f.close.pct_change(m["strength_days"], fill_method=None)
    f["trail"] = f.low.rolling(cfg["exits"]["trailing_low_days"]).min()
    return f


@dataclass(frozen=True)
class Candidate:
    symbol: str
    isin: str
    sector: str
    strategy: str
    entry: float
    stop: float
    target: float
    features: dict


def setups(f, percentile, cfg):
    t, m, v = cfg["trend"], cfg["momentum"], cfg["vcp"]
    last, prev = f.iloc[-1], f.iloc[-2]
    if pd.isna(last.atr) or last.atr <= 0:
        return
    if (
        last.close > last.medium > last.slow
        and abs(prev.low - prev.fast) <= t["proximity_atr"] * prev.atr
        and last.close > prev.high
    ):
        yield "trend_pullback", min(prev.low, last.low)
    highs, lows, volumes = (f[field].to_numpy()[:-1] for field in ("high", "low", "volume"))
    if len(highs) >= m["lookback"] and percentile >= m["strength_percentile"]:
        min_base = highs[-m["lookback"] :].max() * (1 - m["near_high"])
        for length in range(m["base_max"], m["base_min"] - 1, -1):
            stop = lows[-length:].min()
            if (
                stop >= min_base
                and last.close > highs[-length:].max()
                and last.volume >= volumes[-length:].mean() * m["volume_multiple"]
            ):
                yield "high_momentum", stop
                break
    n = v["contraction_days"]
    for count in range(v["max_contractions"], v["min_contractions"] - 1, -1):
        size = n * count
        if len(highs) < size:
            continue
        peaks = highs[-size:].reshape(count, n).max(axis=1)
        if last.close <= peaks.max() or last.volume < volumes[-size:].mean() * v["volume_multiple"]:
            continue
        floors = lows[-size:].reshape(count, n).min(axis=1)
        widths = (peaks - floors) / peaks
        averages = volumes[-size:].reshape(count, n).mean(axis=1)
        if (np.diff(widths) < 0).all() and (np.diff(averages) < 0).all():
            yield "vcp_breakout", floors[-1]
            break


def feature_frames(bars, cfg):
    """Compute each security's indicators along its own observed bars in one matrix."""
    ordered = bars.sort_values(["symbol", "day"]).reset_index(drop=True)
    column, labels = pd.factorize(ordered.symbol, sort=False)
    row = ordered.groupby("symbol", sort=False).cumcount().to_numpy()
    shape = (row.max() + 1, len(labels))

    def matrix(name):
        arr = np.full(shape, np.nan)
        arr[row, column] = ordered[name]
        return pd.DataFrame(arr)

    close, low, high, volume = (matrix(c) for c in ("close", "low", "high", "volume"))
    t = cfg["trend"]
    for name in ("fast", "medium", "slow"):
        ordered[name] = ema(close, t[name]).to_numpy()[row, column]
    previous = close.shift()
    tr = pd.DataFrame(np.fmax(np.fmax(high - low, (high - previous).abs()), (low - previous).abs()))
    atr = tr.ewm(alpha=1 / t["atr"], adjust=False, min_periods=t["atr"]).mean()
    turnover = (close * volume).rolling(cfg["filters"]["median_turnover_days"]).median()
    strength = close.pct_change(cfg["momentum"]["strength_days"], fill_method=None)
    trail = low.rolling(cfg["exits"]["trailing_low_days"]).min()
    for name, array in (
        ("atr", atr),
        ("turnover", turnover),
        ("strength", strength),
        ("trail", trail),
    ):
        ordered[name] = array.to_numpy()[row, column]
    return {sym: frame.set_index("day") for sym, frame in ordered.groupby("symbol", sort=False)}


def scan_frame(bars, members, cfg, context, *, as_of=None):
    if as_of is not None:
        bars = bars.loc[bars.day <= as_of]
    fs = feature_frames(bars, cfg)
    for s in members.symbol:
        fs[s].attrs["exchange"] = context[s]
    index = fs["NIFTY 50"].iloc[-1]
    vix = fs["INDIA VIX"].iloc[-1]
    eligible = {s: fs[s].iloc[-1] for s in members.symbol if pd.notna(fs[s].iloc[-1].medium)}
    breadth = sum(r.close > r.medium for r in eligible.values()) / len(eligible) if eligible else 0
    regime = {
        "nifty_close": float(index.close),
        "nifty_ema": None if pd.isna(index.slow) else float(index.slow),
        "breadth": breadth,
        "breadth_count": len(eligible),
        "vix": float(vix.close),
    }
    regime["enabled"] = bool(
        index.close > index.slow
        and breadth >= cfg["filters"]["min_breadth"]
        and vix.close <= cfg["filters"]["max_vix"]
    )
    ranks = pd.Series({s: r.strength for s, r in eligible.items()}, dtype=float).rank(pct=True)
    candidates = []
    for row in members.itertuples(index=False):
        f = fs[row.symbol]
        last = f.iloc[-1]
        if (
            len(f) < 2
            or pd.isna(last.turnover)
            or last.turnover < cfg["filters"]["min_turnover_inr"]
        ):
            continue
        rs = ranks.get(row.symbol, float("nan"))
        for strategy, stop in setups(f, rs, cfg):
            if not 0 < stop < last.close:
                continue
            snap = {
                "atr": float(last.atr),
                "median_turnover": float(last.turnover),
                "rs_percentile": None if pd.isna(rs) else float(rs),
                "volume": int(last.volume),
            }
            market_data = context[row.symbol]
            snap["quarantined"] = market_data.get("quarantined", False)
            snap["surveillance"] = market_data["surveillance"]
            snap["earnings_in_days"] = market_data["earnings_in_days"]
            snap["earnings_coverage"] = market_data.get("earnings_coverage", "known")
            snap["holding_blackout_days"] = market_data.get("holding_blackout_days")
            snap["meeting_coverage_days"] = market_data.get("meeting_coverage_days", 30)
            entry = float(last.close)
            candidates.append(
                Candidate(
                    row.symbol,
                    row.isin,
                    row.sector,
                    strategy,
                    entry,
                    float(stop),
                    entry + (entry - stop) * cfg["exits"]["target_r"],
                    snap,
                )
            )
    return regime, candidates, fs


def load_session(root, day, cfg, following=()):
    verify(root, day)
    folder = root / "sessions" / str(day)
    bars = pd.read_parquet(folder / "bars.parquet")
    members = read_members((folder / "members.csv").read_bytes())
    if set(bars.symbol) != set(members.symbol) | set(BENCHMARKS):
        raise MarketError("Snapshot does not cover all scan inputs.")
    if bars.day.max() != day:
        raise MarketError("Snapshot contains an incorrect session.")
    from .trading_calendar import holding_horizon

    context = read_context(root, day, verify_sources=True)
    horizon = holding_horizon(root, day, cfg["exits"]["max_days"])
    for info in context.values():
        info["holding_blackout_days"] = horizon
    regime, candidates, fs = scan_frame(bars, members, cfg, context, as_of=day)
    attach_following(root, day, cfg, following, fs, dict(zip(members["isin"], members.symbol)))
    return regime, candidates, fs


def attach_following(root, day, cfg, following, frames, by_isin):
    from . import corporate

    register = corporate.actions(root)
    for call in following:
        term = register.get(call["isin"])
        if term and str(day) >= term["suspended"]:
            # A successor can itself undergo a later scheme.
            visited = {term["predecessor_isin"]}
            while term.get("successor_isin") in register:
                successor = register[term["successor_isin"]]
                if str(day) < successor["suspended"]:
                    break
                if successor["predecessor_isin"] in visited:
                    raise MarketError("Circular compulsory-action register.")
                visited.add(successor["predecessor_isin"])
                term = successor
            frame = corporate.action_frame(root, day, term, frames, by_isin)
            frames.setdefault(call["symbol"], frame)
            frames[call["isin"]] = frame
            continue
        current = by_isin.get(call["isin"])
        if current:
            if call["symbol"] != current:
                frames[call["symbol"]] = frames[current]
        else:
            try:
                frames[call["symbol"]] = follow_removed(root, day, cfg, call)
            except MissingSecurity:
                frames[call["symbol"]] = pd.DataFrame(index=[day])
                frames[call["symbol"]].attrs["exchange"] = {
                    "unavailable": "No traded price for the original ISIN"
                }


class MissingSecurity(MarketError):
    pass


def follow_removed(root, day, cfg, call):
    """Continue marking an existing position after it leaves the scan universe."""
    detected = date.fromisoformat(call["day"])
    folder = root / "sessions" / str(detected)
    verify(root, detected)
    base = pd.read_parquet(folder / "bars.parquet", filters=[("symbol", "=", call["symbol"])])
    from .corporate import quote

    tail = []
    member = None
    for d in sessions(root):
        if not detected < d <= day:
            continue
        bar = quote(root, d, call["isin"])
        if bar is None:
            raise MissingSecurity(f"Missing tradable equity for open call {call['symbol']} on {d}.")
        member = {"isin": call["isin"], "series": "EQ", "symbol": bar["symbol"]}
        tail.append({**bar, "day": d, "symbol": call["symbol"]})
    if member is None:
        raise MarketError("No follow-up session after detection.")
    data = features(pd.concat([base, pd.DataFrame(tail)], ignore_index=True), cfg)
    ctx = root / "context" / str(day)
    read_context(root, day, verify_sources=True)
    if not (ctx / "security.csv.gz").exists():
        from .historical_sessions import context_for, reference_rows

        actions, meetings, coverage, _ = reference_rows(root)
        data.attrs["exchange"] = context_for(
            day, pd.DataFrame([member]), meetings, actions, coverage, set()
        )[member["symbol"]]
        master = root / "sources" / "exchange-controls" / str(day) / "security.csv.gz"
        if master.is_file():
            from .exchange import decode_controls

            data.attrs["exchange"].update(
                decode_controls(pd.DataFrame([member]), None, master.read_bytes())[member["symbol"]]
            )
        return data
    info = decode(
        day,
        pd.DataFrame([member]),
        (ctx / "surveillance.csv").read_bytes(),
        (ctx / "security.csv.gz").read_bytes(),
        json.loads((ctx / "meetings.json").read_text()),
        json.loads((ctx / "actions.json").read_text()),
    )
    data.attrs["exchange"] = info[member["symbol"]]
    return data
