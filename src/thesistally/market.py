"""Read-only Kite and NSE adapters and dated, reconciled market snapshots."""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import httpx
import numpy as np
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
ARCHIVE = "https://nsearchives.nseindia.com/content"
UNIVERSE_URL = ARCHIVE + "/indices/ind_nifty200list.csv"
KITE = "https://api.kite.trade"
BENCHMARKS = ("NIFTY 50", "NIFTY 200", "INDIA VIX")
PRICES = ["open", "high", "low", "close"]
TOLERANCE = 0.005001  # half a paisa, tolerate only decimal representation/rounding


class MarketError(ValueError):
    pass


def today() -> date:
    return datetime.now(IST).date()


def credentials() -> dict:
    if os.getenv("KITE_API_KEY") and os.getenv("KITE_ACCESS_TOKEN"):
        return {
            "api_key": os.environ["KITE_API_KEY"],
            "access_token": os.environ["KITE_ACCESS_TOKEN"],
        }
    path = Path(os.environ.get("RADAR_CREDENTIALS", ".radar-credentials")).expanduser()
    try:
        kite = json.loads(path.read_text())["kite"]
        if not all(
            isinstance(kite.get(k), str) and kite[k].strip() for k in ("api_key", "access_token")
        ):
            raise ValueError()
        return kite
    except (KeyError, ValueError, OSError, TypeError, AttributeError):
        raise MarketError(
            "Set RADAR_CREDENTIALS to the local JSON handoff with a Kite session."
        ) from None


class Feed:
    def __init__(self, timeout=20):
        self.http = httpx.Client(timeout=timeout, headers={"User-Agent": "ThesisTally/0.1"})
        self.auth = {}
        self.next_history = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.close()

    def get(self, url, *, kite=False, params=None, attempts=4) -> httpx.Response:
        if kite and not self.auth:
            cfg = credentials()
            self.auth = {
                "X-Kite-Version": "3",
                "Authorization": f"token {cfg['api_key']}:{cfg['access_token']}",
            }
        for attempt in range(attempts):
            try:
                response = self.http.get(url, headers=self.auth if kite else {}, params=params)
            except httpx.TransportError as exc:
                if attempt + 1 == attempts:
                    raise MarketError(f"{url} unavailable ({type(exc).__name__}).") from None
            else:
                if response.status_code == 200:
                    return response
                if kite and response.status_code in (401, 403):
                    raise MarketError(
                        "Kite rejected the session or data entitlement. Refresh the local handoff."
                    )
                if response.status_code not in (429, 500, 502, 503, 504) or attempt + 1 == attempts:
                    raise MarketError(f"HTTP {response.status_code} from {url}")
            time.sleep(2**attempt)
        raise AssertionError("unreachable")

    def session(self):
        response = self.get(KITE + "/user/profile", kite=True)
        if response.json().get("status") != "success":
            raise MarketError("Kite did not confirm the session.")

    def instruments(self) -> bytes:
        data = self.get(KITE + "/instruments/NSE", kite=True).content
        return gzip.decompress(data) if data.startswith(b"\x1f\x8b") else data

    def history(self, token: int, start: date, end: date) -> pd.DataFrame:
        rows = []
        while start <= end:
            last = min(start + timedelta(days=1499), end)
            time.sleep(max(0, self.next_history - time.monotonic()))
            self.next_history = time.monotonic() + 0.4
            resp = self.get(
                f"{KITE}/instruments/historical/{token}/day",
                kite=True,
                params={
                    "from": f"{start} 00:00:00",
                    "to": f"{last} 23:59:59",
                },
            )
            rows.extend(resp.json()["data"]["candles"])
            start = last + timedelta(days=1)
        return pd.DataFrame(rows, columns=["day", *PRICES, "volume"])

    def bhavcopy(self, day: date) -> bytes:
        name = f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv"
        try:
            raw = self.get(f"{ARCHIVE}/cm/{name}.zip").content
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                return archive.read(name)
        except (KeyError, zipfile.BadZipFile) as exc:
            raise MarketError(f"Invalid NSE archive for {day}.") from exc


def csv_table(data: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig")


def read_members(data: bytes) -> pd.DataFrame:
    frame = csv_table(data).rename(
        columns={"Symbol": "symbol", "Industry": "sector", "Series": "series", "ISIN Code": "isin"}
    )
    cols = ["symbol", "sector", "series", "isin"]
    if not set(cols) <= set(frame):
        raise MarketError("Membership requires Symbol, Industry, Series and ISIN Code columns.")
    frame = frame[cols].apply(lambda s: s.str.strip())
    # An index can temporarily include a demerged entity in addition to its regular members.
    if len(frame) < 200 or frame.symbol.duplicated().any() or frame["isin"].duplicated().any():
        raise MarketError(f"Incomplete or duplicate Nifty 200 membership ({len(frame)} rows).")
    if (frame == "").any().any() or not frame["isin"].str.fullmatch(r"IN[A-Z0-9]{10}").all():
        raise MarketError("Empty membership field or invalid ISIN.")
    return frame.sort_values("symbol").reset_index(drop=True)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@contextmanager
def staging(parent: Path):
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".building-", dir=parent) as tmp:
        yield Path(tmp)


def save_members(root: Path, data: bytes, start: date, end: date, source: str):
    if not source.strip() or start > end or end > today():
        raise MarketError("Supply a source and a completed, ordered membership date range.")
    members = read_members(data)
    directory = root / "universes"
    dest = directory / f"{start}_{end}"
    with staging(directory) as tmp, (directory / ".import.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        (tmp / "members.csv").write_bytes(data)
        meta = {
            "start": str(start),
            "end": str(end),
            "source": source,
            "sha256": sha(data),
            "members": len(members),
        }
        (tmp / "source.json").write_text(json.dumps(meta, indent=2) + "\n")
        for other in directory.glob("????-??-??_????-??-??"):
            left, right = map(date.fromisoformat, other.name.split("_"))
            if start <= right and end >= left:
                if other == dest and (other / "members.csv").read_bytes() == data:
                    return dest
                # Coverage may overlap when the actual constituents are identical.
                existing = read_members((other / "members.csv").read_bytes())
                if not members.equals(existing):
                    raise MarketError(f"Membership conflicts with {other.name}.")
        if not dest.exists():
            tmp.rename(dest)
    return dest


def membership(root: Path, day: date):
    for path in sorted((root / "universes").glob("????-??-??_????-??-??"), reverse=True):
        start, end = path.name.split("_")
        if start <= str(day) <= end:
            raw = (path / "members.csv").read_bytes()
            meta = json.loads((path / "source.json").read_text())
            if sha(raw) != meta["sha256"]:
                raise MarketError(f"Membership checksum failed for {path.name}.")
            return read_members(raw), path
    raise MarketError(
        f"No dated Nifty 200 membership covers {day}. Import a sourced historical list."
    )


def check_bars(frame: pd.DataFrame, symbol: str, start: date, end: date):
    if frame.empty:
        raise MarketError(f"No Kite daily bars for {symbol}.")
    frame["day"] = pd.to_datetime(frame.day, utc=True).dt.tz_convert(IST).dt.date
    values = frame[[*PRICES, "volume"]].apply(pd.to_numeric, errors="raise")
    if (
        not np.isfinite(values).all().all()
        or (values[PRICES] <= 0).any().any()
        or (values.volume < 0).any()
        or (values.volume % 1 != 0).any()
        or (values.high < values[["open", "low", "close"]].max(axis=1)).any()
        or (values.low > values[["open", "high", "close"]].min(axis=1)).any()
    ):
        raise MarketError(f"Invalid OHLCV for {symbol}.")
    if (
        frame.day.duplicated().any()
        or not frame.day.is_monotonic_increasing
        or frame.day.min() < start
        or frame.day.max() != end
    ):
        raise MarketError(f"Duplicate, stale or out-of-range candles for {symbol} through {end}.")
    frame[[*PRICES, "volume"]] = values
    frame["symbol"] = symbol
    return frame


def references(raw: bytes, day: date, members: pd.DataFrame):
    table = csv_table(raw)
    if set(table.TradDt) != {str(day)}:
        raise MarketError(f"Bhavcopy trade date does not match {day}.")
    table["series"] = table.SctySrs.str.strip()
    selected = members.merge(
        table,
        left_on=["symbol", "series", "isin"],
        right_on=["TckrSymb", "series", "ISIN"],
        how="left",
        validate="one_to_one",
    )
    missing = selected.loc[selected.ClsPric.isna(), "symbol"].tolist()
    if missing:
        raise MarketError(f"Missing bhavcopy rows for {', '.join(missing)}.")
    return selected.set_index("symbol")


def reconcile(closes: pd.Series, reference: pd.DataFrame):
    prices = pd.to_numeric(reference.ClsPric)
    diff = (prices - closes.reindex(prices.index)).abs()
    bad = diff[~np.isfinite(diff) | (diff > TOLERANCE)]
    if not bad.empty:
        raise MarketError("NSE close reconciliation failed for " + ", ".join(bad.index))
    return float(diff.max())


def settle(bars: pd.DataFrame, day: date, ref: pd.DataFrame, raw_indices: bytes):
    """Preserve provider revisions and replace only the completed session with NSE final data."""
    indices = csv_table(raw_indices)
    if set(indices["Index Date"]) != {day.strftime("%d-%m-%Y")}:
        raise MarketError("Index close archive has the wrong date.")
    indices.index = indices["Index Name"].str.upper()
    official = ref[["OpnPric", "HghPric", "LwPric", "ClsPric", "TtlTradgVol"]].copy()
    official.columns = [*PRICES, "volume"]
    benchmarks = indices.loc[
        list(BENCHMARKS),
        ["Open Index Value", "High Index Value", "Low Index Value", "Closing Index Value"],
    ].copy()
    benchmarks.columns = PRICES
    benchmarks["volume"] = 0
    official = pd.concat([official, benchmarks]).apply(pd.to_numeric)
    current = bars.loc[bars.day == day].set_index("symbol")
    if set(current.index) != set(official.index):
        raise MarketError("Kite and NSE instrument sets differ.")
    official = official.loc[current.index]
    audit = current[PRICES + ["volume"]].add_prefix("kite_").join(official.add_prefix("nse_"))
    audit["close_difference"] = audit.kite_close - audit.nse_close
    settled = current.copy()
    settled[official.columns] = official
    if (
        not np.isfinite(official).all().all()
        or (official[PRICES] <= 0).any().any()
        or (official.high < official[["open", "close", "low"]].max(axis=1)).any()
        or (official.low > official[["open", "close", "high"]].min(axis=1)).any()
        or (official.volume < 0).any()
    ):
        raise MarketError("Invalid OHLCV in exchange final.")
    return pd.concat([bars[bars.day != day], settled.reset_index()], ignore_index=True), audit


def sync(root: Path, feed: Feed, day: date, start: date, progress=print, cache=None):
    now = datetime.now(IST)
    if start > day or day > now.date() or (day == now.date() and now.time() < clock_time(15, 45)):
        raise MarketError(
            "Sync requires a completed NSE session after 15:45 IST and start <= session."
        )
    dest = root / "sessions" / str(day)
    if dest.exists():
        meta = verify(root, day)
        if date.fromisoformat(meta["requested_start"]) > start:
            raise MarketError(
                "This immutable snapshot has shorter history. Use a separate RADAR_DATA for a longer backfill."
            )
        return meta
    raw_bhav = feed.bhavcopy(day)
    if day == now.date():
        raw_members = feed.get(UNIVERSE_URL).content
        save_members(root, raw_members, day, day, UNIVERSE_URL)
    members, universe_dir = membership(root, day)
    ref = references(raw_bhav, day, members)
    raw_instruments = cache["instruments"] if cache else feed.instruments()
    instruments = csv_table(raw_instruments)
    indices = instruments[instruments.segment == "INDICES"].set_index("tradingsymbol")
    tokens = {s: int(indices.at[s, "instrument_token"]) for s in BENCHMARKS}
    if day == now.date():
        equity = instruments[instruments.segment == "NSE"].set_index("tradingsymbol")
        for s, row in ref.iterrows():
            if s not in equity.index or int(equity.at[s, "exchange_token"]) != int(row.FinInstrmId):
                raise MarketError(f"Kite/NSE instrument mapping mismatch for {s}.")
            tokens[s] = int(equity.at[s, "instrument_token"])
    else:
        # NSE cash exchange code is 1. Use the dated exchange security ID to
        # include former constituents, then independently reconcile the close.
        tokens.update({s: (int(row.FinInstrmId) << 8) | 1 for s, row in ref.iterrows()})

    with staging(dest.parent) as tmp:
        frames = []
        for i, (symbol, token) in enumerate(tokens.items(), 1):
            if cache and cache["tokens"].get(symbol) == token and symbol in cache["bars"]:
                all_bars = cache["bars"][symbol]
                chunk = all_bars.loc[
                    all_bars.day.between(start, day), ["day", *PRICES, "volume"]
                ].copy()
            else:
                chunk = feed.history(token, start, day)
            bars = check_bars(chunk, symbol, start, day)
            frames.append(bars)
            if i % 25 == 0:
                progress(f"Fetched {i}/{len(tokens)} instruments")
        bars = pd.concat(frames, ignore_index=True)
        previous_days = bars.loc[(bars.symbol == "NIFTY 50") & (bars.day < day), "day"]
        if previous_days.empty:
            raise MarketError("Include the previous session in the requested warm-up.")
        prior = previous_days.max()
        raw_prior = feed.bhavcopy(prior)
        # Reconcile every comparable security by ISIN. A new listing can
        # legitimately have no row on the prior session.
        prior_ref = csv_table(raw_prior)
        if set(prior_ref.TradDt) != {str(prior)}:
            raise MarketError("Prior bhavcopy has the wrong session date.")
        elig = prior_ref[prior_ref.SctySrs.str.strip().isin(members.series)]
        prior_ref = members.merge(
            elig, left_on=["isin", "series"], right_on=["ISIN", "SctySrs"], validate="one_to_one"
        ).set_index("symbol")
        if prior_ref.empty:
            raise MarketError("No comparable prior-session securities.")
        prior_close = bars[bars.day == prior].set_index("symbol").close
        original = bars.loc[bars.day == day].set_index("symbol")[PRICES + ["volume"]].copy()
        expected_prior = pd.to_numeric(prior_ref.ClsPric)
        prior_diff = (prior_close.reindex(prior_ref.index) - expected_prior).abs()
        revised = prior_diff[prior_diff > TOLERANCE].index
        historical = day != today()
        if historical:
            checks = [(prior, prior_ref), (day, ref)]
        elif len(revised):
            # A documented ex-date can change the previous session's entire
            # candle overnight. Require both a matching notice and OHLC scaling.
            asof = day.strftime("%d-%m-%Y")
            actions = feed.get(
                "https://www.nseindia.in/api/corporates-corporateActions",
                params={"index": "equities", "from_date": asof, "to_date": asof},
            )
            (tmp / "adjustments.json").write_bytes(actions.content)
            explained = {
                a["symbol"]
                for a in actions.json()
                if a["exDate"] == day.strftime("%d-%b-%Y")
                and re.search(r"dividend|bonus|split|rights|demerger", a["subject"], re.IGNORECASE)
            }
            if set(revised) - explained:
                raise MarketError(
                    "Unexplained prior NSE close change: "
                    + ", ".join(sorted(set(revised) - explained))
                )
            checks = [(prior, prior_ref.loc[revised])]
        else:
            checks = []
        for session, reference in checks:
            provider = bars[bars.day == session].set_index("symbol").reindex(reference.index)
            scale = pd.to_numeric(reference.ClsPric) / provider.close
            for col, raw_col in zip(PRICES, ("OpnPric", "HghPric", "LwPric", "ClsPric")):
                expected = pd.to_numeric(reference[raw_col])
                difference = (provider[col] * scale - expected).abs()
                bad = ~np.isfinite(difference) | (difference > np.maximum(0.10, expected * 0.001))
                if bad.any():
                    raise MarketError(
                        f"Historical OHLC disagrees with NSE on {session}: "
                        + ", ".join(bad[bad].index)
                    )
            if session == prior:
                prior_close = prior_close.copy()
                prior_close.loc[provider.index] = provider.close * scale
            else:
                bars[PRICES] = bars[PRICES].mul(bars.symbol.map(scale).fillna(1), axis=0)
        delta = reconcile(prior_close, prior_ref)
        raw_indices = feed.get(f"{ARCHIVE}/indices/ind_close_all_{day:%d%m%Y}.csv").content
        bars, audit = settle(bars, day, ref, raw_indices)
        audit = audit.join(original.add_prefix("source_"))
        audit["warmup_price_factor"] = audit.kite_close / audit.source_close
        audit.to_csv(tmp / "reconciliation.csv")
        (tmp / "prior-bhavcopy.csv").write_bytes(raw_prior)
        (tmp / "indices.csv").write_bytes(raw_indices)
        bars.to_parquet(tmp / "bars.parquet", index=False)
        (tmp / "bhavcopy.csv").write_bytes(raw_bhav)
        (tmp / "instruments.csv").write_bytes(raw_instruments)
        shutil.copyfile(universe_dir / "members.csv", tmp / "members.csv")
        (tmp / "tokens.json").write_text(json.dumps(tokens, indent=2))
        meta = {
            "day": str(day),
            "requested_start": str(start),
            "fetched_at": datetime.now(IST).isoformat(),
            "history_snapshot": cache["day"] if cache else None,
            "universe": universe_dir.name,
            "source": json.loads((universe_dir / "source.json").read_text())["source"],
            "instruments": len(tokens),
            "members": len(members),
            "candles": len(bars),
            "verified_closes": len(ref),
            "prior_day": str(prior),
            "prior_verified_closes": len(prior_ref),
            "prior_scaled_closes": len(revised),
            "prior_max_close_difference": delta,
            "provider_close_revisions": int(
                (audit.loc[ref.index, "close_difference"].abs() > TOLERANCE).sum()
            ),
            "price_basis": "Kite adjusted warm-up with NSE final session OHLCV",
            "sha256": {p.name: sha(p.read_bytes()) for p in tmp.iterdir()},
        }
        (tmp / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n")
        try:
            tmp.rename(dest)
        except OSError:
            if not dest.is_dir():
                raise
            return verify(root, day)
    return meta


def verify(root: Path, day: date):
    folder = root / "sessions" / str(day)
    if not (folder / "manifest.json").is_file():
        raise MarketError(f"No reconciled market snapshot for {day}. Run radar sync first.")
    meta = json.loads((folder / "manifest.json").read_text())
    standard_files = (
        "bars.parquet",
        "bhavcopy.csv",
        "instruments.csv",
        "members.csv",
        "tokens.json",
        "prior-bhavcopy.csv",
        "indices.csv",
        "reconciliation.csv",
    )
    files = (
        ("bars.parquet", "bhavcopy.csv", "members.csv", "indices.csv")
        if meta.get("format") == "exchange-history-v2"
        else standard_files
    )
    for name in files:
        path = folder / name
        if not path.is_file() or sha(path.read_bytes()) != meta["sha256"].get(name):
            raise MarketError(f"Checksum failed for {name} in {day}.")
    ref = references(
        (folder / "bhavcopy.csv").read_bytes(),
        day,
        read_members((folder / "members.csv").read_bytes()),
    )
    with duckdb.connect() as db:
        daily = (
            db.execute(
                "SELECT symbol,open,high,low,close,volume FROM read_parquet(?) WHERE day=?",
                [str(folder / "bars.parquet"), day],
            )
            .df()
            .set_index("symbol")
        )
    reconcile(daily.close, ref)
    equities = daily.reindex(ref.index)
    official = ref[["OpnPric", "HghPric", "LwPric", "ClsPric", "TtlTradgVol"]].apply(pd.to_numeric)
    official.columns = [*PRICES, "volume"]
    if not np.allclose(equities[official.columns], official, rtol=1e-12, atol=1e-8):
        raise MarketError(f"Daily OHLCV differs from official exchange prices on {day}.")
    if (
        (equities.high + 1e-9 < equities[["open", "low", "close"]].max(axis=1)).any()
        or (equities.low - 1e-9 > equities[["open", "high", "close"]].min(axis=1)).any()
        or (equities.volume < 0).any()
    ):
        raise MarketError(f"Invalid daily high, low or volume on {day}.")
    return meta


def sessions(root: Path):
    return sorted(
        date.fromisoformat(p.name)
        for p in (root / "sessions").glob("????-??-??")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name) and (p / "manifest.json").is_file()
    )


def backfill(root, feed, start, end, anchor, progress=print):
    """Reuse only price history. Membership, exchange identities and closes are dated independently."""
    if not start <= end <= anchor:
        raise MarketError("Backfill requires start <= end <= the source price snapshot.")
    meta = verify(root, anchor)
    folder = root / "sessions" / str(anchor)
    history = pd.read_parquet(folder / "bars.parquet")
    groups = {s: f for s, f in history.groupby("symbol")}
    dates = sorted(d for d in groups["NIFTY 50"].day if start <= d <= end)
    if not dates or date.fromisoformat(meta["requested_start"]) > start:
        raise MarketError("The source snapshot does not cover this backfill range.")
    for d in dates:
        membership(root, d)
    cache = {
        "bars": groups,
        "tokens": json.loads((folder / "tokens.json").read_text()),
        "day": str(anchor),
        "instruments": (folder / "instruments.csv").read_bytes(),
    }
    from .exchange import collect

    for d in dates:
        info = sync(
            root, feed, d, date.fromisoformat(meta["requested_start"]), lambda _: None, cache
        )
        members, _ = membership(root, d)
        collect(root, feed, d, date.fromisoformat(info["prior_day"]), members)
        progress(f"{d}  {info['verified_closes']} NSE closes")
    return dates
