"""Shared as-of-date score, dip features and market gate for the two registered strategies."""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import exchange, market, signals

LOOKBACKS = (126, 252)


def regression_scores(close, lookback):
    if lookback not in LOOKBACKS:
        raise ValueError(f"Unregistered slope horizon: {lookback}")
    result = pd.Series(np.nan, index=close.columns)
    if len(close) < lookback:
        return result
    values = close.iloc[-lookback:].to_numpy(dtype=float)
    usable = (np.isfinite(values) & (values > 0)).all(axis=0)
    logs = np.log(values[:, usable])
    x = np.arange(lookback) - (lookback - 1) / 2
    centered = logs - logs.mean(axis=0)
    cross = (centered * x[:, None]).sum(axis=0)
    xx = x @ x
    yy = (centered * centered).sum(axis=0)
    fit = np.divide(cross**2, xx * yy, out=np.zeros_like(cross), where=yy > 0).clip(0, 1)
    result.loc[usable] = np.expm1(252 * cross / xx) * fit
    return result


def wilder_rsi2(close):
    previous = close.ffill().shift()
    daily = close - previous
    count = daily.notna().cumsum()

    def smoothed(changes):
        seed = changes.where(count <= 2).cumsum() / 2
        values = changes.where(count > 2, seed.where(count == 2))
        return values.ewm(alpha=0.5, adjust=False, ignore_na=True).mean().iloc[-1]

    gain, loss = smoothed(daily.clip(lower=0)), smoothed((-daily).clip(lower=0))
    return (100 * gain / (gain + loss)).mask((gain == 0) & (loss == 0), 50)


def _panels(bars):
    return bars.pivot(
        index="day", columns="symbol", values=["close", "high", "low", "volume"]
    ).sort_index()


def _ranks(panels, members, lookbacks, context):
    close = panels["close"].reindex(columns=members.symbol)
    high, low = (panels[key].reindex(columns=close.columns) for key in ("high", "low"))
    prev = close.ffill().shift()
    true_range = np.fmax(high - low, np.fmax((high - prev).abs(), (low - prev).abs()))
    # Align the price series to the exchange calendar. A hole in a required
    # regression or jump observation cannot be treated as a flat day.
    jumps = close.iloc[-91:].pct_change(fill_method=None).iloc[1:].abs()
    jump_ok = (jumps.count() == 90) & (jumps.max() <= 0.15 + 1e-12)

    # Ignore missing observations when advancing EMA and Wilder's old state,
    # following the existing engine's per-security dated feature convention.
    def ema(n):
        return close.ewm(span=n, adjust=False, min_periods=n, ignore_na=True).mean().iloc[-1]

    table = members.set_index("symbol").copy()
    table["close"] = close.iloc[-1]
    table["ema100"] = ema(100)
    table["ema200"] = ema(200)
    table["rsi2"] = wilder_rsi2(close)
    table["atr14"] = (
        true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14, ignore_na=True).mean().iloc[-1]
    )
    recent_highs = high.iloc[-11:-1]
    table["prior_high10"] = recent_highs.max().where(recent_highs.count() == 10)
    value = (close * panels["volume"].reindex(columns=close.columns)).iloc[-20:]
    table["turnover"] = value.median().where(value.count() == 20)
    liquid = table.turnover >= signals.rules(revision="r2")["filters"]["min_turnover_inr"]
    eligible = liquid & (table.close >= table.ema200) & jump_ok
    if context is not None:
        eligible &= pd.Series({s: not context[s].get("quarantined", False) for s in table.index})
    result = {}
    for n in lookbacks:
        score = regression_scores(close, n)
        names = table[eligible & np.isfinite(score)].copy()
        names["score"] = score.reindex(names.index)
        names = names.reset_index().sort_values(
            ["score", "isin"], ascending=[False, True], ignore_index=True
        )
        names["rank"] = np.arange(1, len(names) + 1)
        result[n] = names
    return result


def ranked(bars, members, lookback=126, context=None, *, as_of=None):
    if as_of is not None:
        bars = bars[bars.day <= as_of]
    return _ranks(_panels(bars), members, (lookback,), context)[lookback]


def session(root, day, lookbacks=LOOKBACKS):
    """Return the shared closing allocation gate and registered member rankings."""
    market.verify(root, day)
    folder = root / "sessions" / str(day)
    bars = pd.read_parquet(folder / "bars.parquet")
    members = market.read_members((folder / "members.csv").read_bytes())
    if bars.day.max() != day or set(bars.symbol) != set(members.symbol) | set(market.BENCHMARKS):
        raise market.MarketError(f"Incomplete score inputs for {day}")
    panels = _panels(bars)
    prices = panels["close"]
    averages = (
        prices.ewm(span=50, min_periods=50, adjust=False, ignore_na=True)
        .mean()
        .iloc[-1][members.symbol]
        .dropna()
    )
    latest = prices.ffill().iloc[-1]
    breadth = float((latest[averages.index] > averages).mean()) if len(averages) else 0.0
    slow = signals.ema(prices["NIFTY 50"].dropna(), 200).iloc[-1]
    cfg = signals.rules(revision="r2")
    gate = signals.allocation_gate(
        latest["NIFTY 50"], slow, latest["INDIA VIX"], breadth, len(averages), cfg
    )
    if not lookbacks:
        return gate["enabled"], {}
    context = exchange.read_context(root, day, verify_sources=True)
    flags = exchange.price_quarantines(root, day)
    for row in members.itertuples():
        exchange.apply_quarantine(context[row.symbol], flags, row.isin)
    return gate["enabled"], _ranks(panels, members, lookbacks, context)
