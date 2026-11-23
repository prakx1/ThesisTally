"""Point-in-time descriptors for historical discovery opportunities."""

import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import discovery_cap, discovery_guard, market

TERCILES = ("low", "middle", "high", "unknown")
DOMAINS = {
    "size_tercile": TERCILES,
    "volatility_tercile": TERCILES,
    "liquidity_tercile": TERCILES,
    "vix_bucket": ("<15", "15–22", ">22", "unknown"),
    "breadth_bucket": ("<40%", "40–60%", ">60%", "unknown"),
    "stop_distance_bucket": ("<5%", "5–<7%", "≥7%", "unknown"),
}
MARKETS = {"pullback": "baseline", "b10": "stage3", "b5": "stage3"}


def terciles(values):
    """Rank available members at the close. Ties stay together on midpoint ranks."""
    observed = values.replace([np.inf, -np.inf], np.nan).dropna()
    buckets = pd.Series("unknown", index=values.index, dtype=object)
    if len(observed) < 3 or len(observed) * 10 < len(values) * 9:
        return buckets
    rank = (observed.rank(method="average") - 0.5) / len(observed)
    buckets.loc[rank.index] = np.select(
        [rank.lt(1 / 3), rank.lt(2 / 3)], ["low", "middle"], default="high"
    )
    return buckets


def full_cap(observed, day):
    # A migrated or revised old file can reappear after a newer one. Select
    # by original measurement date among the versions already usable that day.
    return max(
        (s for s in observed if day - timedelta(days=370) <= s[0] < day and s[1] <= day),
        key=lambda s: s[0],
        default=None,
    )


def market_descriptors(prices, volume, members, cap_sheet):
    names = members.set_index("symbol")["isin"]
    if names.duplicated().any() or names.index.duplicated().any():
        raise ValueError("Duplicate PIT membership")
    close = prices.reindex(columns=names.index).where(lambda x: x > 0)
    volume = volume.reindex(index=close.index, columns=close.columns).where(lambda x: x >= 0)
    # A missing equity observation cannot become a zero price change. The day index
    # is the union of all exchange observations, including the Nifty benchmark.
    changes = np.log(close).diff().tail(20)
    vol = changes.std(ddof=1) * np.sqrt(252)
    vol = vol.where(changes.count().eq(20))
    turnover = (volume * close).tail(20)
    liq = turnover.median().where(turnover.count().eq(20))
    caps = names.map(cap_sheet[3] if cap_sheet else {}).astype(float)
    return pd.DataFrame(
        {
            "isin": names,
            "size_crore": caps,
            "size_tercile": terciles(caps),
            "volatility_20": vol,
            "volatility_tercile": terciles(vol),
            "liquidity_20_inr": liq,
            "liquidity_tercile": terciles(liq),
            "cap_period": str(cap_sheet[0]) if cap_sheet else None,
            "cap_available": str(cap_sheet[1]) if cap_sheet else None,
            "cap_sha256": cap_sheet[2] if cap_sheet else None,
        }
    ).reset_index()


def day_descriptors(task):
    path, vintage, iso, cap_sheet, selected = task
    day = date.fromisoformat(iso)
    folder = path / vintage / "market/sessions" / iso
    manifest_raw = (folder / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    if manifest["day"] != iso:
        raise ValueError("Unexpected market date")
    sources = {}
    for name in ("bars.parquet", "members.csv"):
        raw = (folder / name).read_bytes()
        if market.sha(raw) != manifest["sha256"][name]:
            raise ValueError(f"Changed daily market artifact {iso} {name}")
        sources[name] = raw
    bars = pd.read_parquet(
        io.BytesIO(sources["bars.parquet"]),
        columns=["day", "symbol", "close", "volume"],
        filters=[("day", ">=", day - timedelta(days=60))],
    )
    if bars.empty or bars.day.max() != day:
        raise ValueError(f"Invalid as-of panel {iso}")
    p = bars.pivot(index="day", columns="symbol", values=["close", "volume"]).sort_index().tail(21)
    members = market.read_members(sources["members.csv"])
    desc = market_descriptors(p["close"], p["volume"], members, cap_sheet)
    desc = desc.loc[desc["isin"].isin(selected)].copy()
    if set(desc["isin"]) != set(selected):
        raise ValueError(f"Discovery signal missing from dated Nifty 200 members {iso}")
    return desc.assign(
        day=iso, discovery_market=vintage, market_manifest_sha256=market.sha(manifest_raw)
    )


def fixed_buckets(trades):
    frame = trades.copy()
    vix = pd.to_numeric(frame["vix"], errors="coerce")
    breadth = pd.to_numeric(frame["breadth"], errors="coerce")
    stop = pd.to_numeric(frame["stop_fraction"], errors="coerce")
    frame["vix_bucket"] = np.select(
        [vix.lt(15), vix.le(22), vix.gt(22)], DOMAINS["vix_bucket"][:3], default="unknown"
    )
    frame["breadth_bucket"] = np.select(
        [breadth.lt(0.4), breadth.le(0.6), breadth.gt(0.6)],
        DOMAINS["breadth_bucket"][:3],
        default="unknown",
    )
    # Use the planned stop at signal close for a filter; tomorrow's opening fill is unavailable.
    frame["stop_distance_bucket"] = np.select(
        [stop.lt(0.05), stop.lt(0.07), stop.ge(0.07)],
        DOMAINS["stop_distance_bucket"][:3],
        default="unknown",
    )
    return frame


def enrich(trades, source_data, *, cache_root=None, workers=3):
    """Never inspect outcomes. Preserve every input opportunity and its original row order."""
    source = Path(source_data)
    directory = (
        Path(cache_root) if cache_root is not None else discovery_guard.REPO / "data/discovery"
    )
    sheets = discovery_cap.load(directory)
    frame = fixed_buckets(trades)
    frame["discovery_market"] = frame["strategy"].map(MARKETS)
    if frame["discovery_market"].isna().any():
        raise ValueError("Market archive for strategy unspecified")
    requests = []
    for (vintage, day), group in frame.groupby(["discovery_market", "day"]):
        requests.append(
            (source, vintage, day, full_cap(sheets, date.fromisoformat(day)), set(group["isin"]))
        )
    if not requests:
        return frame.assign(**{k: pd.Series(dtype=str) for k in DOMAINS if k not in frame})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        metadata = pd.concat(pool.map(day_descriptors, requests), ignore_index=True)
    enriched = frame.merge(
        metadata,
        on=["discovery_market", "day", "isin", "symbol"],
        how="left",
        sort=False,
        validate="many_to_one",
    )
    if enriched["market_manifest_sha256"].isna().any():
        raise ValueError("Discovery outcome failed its dated symbol/ISIN join")
    return enriched
