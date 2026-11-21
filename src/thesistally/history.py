"""Immutable exchange source files and point-in-time coverage for historical research."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx
import numpy as np
import pandas as pd

from . import market


def calendar(root, start, end):
    """Observed trading sessions from an independent Nifty 50 benchmark series."""
    folder = root / "sources" / "calendar"
    folder.mkdir(parents=True, exist_ok=True)
    if end > market.today() or end < start:
        raise market.MarketError("Choose an ordered range through the latest completed session.")
    for path in sorted(folder.glob("kite-nifty50-*_*.csv")):
        left, right = path.stem.removeprefix("kite-nifty50-").split("_")
        if left <= str(start) and right >= str(end):
            frame = pd.read_csv(path)
            break
    else:
        with market.Feed() as feed:
            frame = feed.history(256265, start, end)
        if frame.empty:
            raise market.MarketError("Empty Nifty 50 trading calendar.")
        path = folder / f"kite-nifty50-{start}_{end}.csv"
        # Today's benchmark candle may still be unpublished. Never cache an incomplete tail.
        last = min(end, pd.Timestamp(frame.day.iloc[-1]).date())
        path = folder / f"kite-nifty50-{start}_{last}.csv"
        with market.staging(folder) as tmp:
            frame.to_csv(tmp / "calendar.csv", index=False)
            (tmp / "calendar.csv").replace(path)
    days = [date.fromisoformat(str(x)[:10]) for x in frame.day]
    if days != sorted(set(days)):
        raise market.MarketError("Duplicate or unordered benchmark sessions.")
    return [d for d in days if start <= d <= end]


def ranges(days, calendar_days):
    """Contiguous intervals in exchange sessions, including special weekend sessions."""
    selected = set(days)
    spans = []
    active = False
    for day in calendar_days:
        if day in selected:
            if active:
                spans[-1]["to"] = str(day)
            else:
                spans.append({"from": str(day), "to": str(day)})
            active = True
        else:
            active = False
    return spans


def source_urls(day):
    if day < date(2024, 7, 8):
        stamp = day.strftime("%d%b%Y").upper()
        bhav = f"historical/EQUITIES/{day:%Y}/{stamp[2:5]}/cm{stamp}bhav.csv.zip"
    else:
        bhav = f"cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    return {"bhavcopy.zip": bhav, "indices.csv": f"indices/ind_close_all_{day:%d%m%Y}.csv"}


def extract_bhav(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if len(files) != 1:
            raise market.MarketError("Expected exactly one equity bhavcopy in the archive.")
        return archive.read(files[0])


def normalize_bhav(raw, day):
    frame = market.csv_table(raw)
    if "TIMESTAMP" in frame:
        frame = frame.rename(
            columns={
                "SYMBOL": "TckrSymb",
                "SERIES": "SctySrs",
                "OPEN": "OpnPric",
                "HIGH": "HghPric",
                "LOW": "LwPric",
                "CLOSE": "ClsPric",
                "TOTTRDQTY": "TtlTradgVol",
                "PREVCLOSE": "PrvsClsgPric",
            }
        )
        if not set(frame["TIMESTAMP"].str.strip().str.upper()) <= {
            day.strftime("%d-%b-%Y").upper(),
            day.strftime("%d-%b-%y").upper(),
        }:
            raise market.MarketError(f"Legacy bhavcopy date mismatch on {day}.")
        frame["TradDt"] = str(day)
    if set(frame.TradDt) != {str(day)} or "ISIN" not in frame:
        raise market.MarketError(f"Bhavcopy date or identifiers missing on {day}.")
    return frame


def fetch_file(client, relative, check):
    messages = []
    urls = [
        f"https://{host}/content/{relative}"
        for host in ("nsearchives.nseindia.com", "archives.nseindia.com")
    ]
    if relative.startswith("indices/"):
        urls.append("https://www.niftyindices.com/Daily_Snapshot/" + relative.split("/")[-1])
    for url in urls:
        try:
            response = client.get(url)
            response.raise_for_status()
            check(response.content)
            return response.content, url
        except (httpx.HTTPError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            messages.append(f"{url} {type(exc).__name__}")
    raise market.MarketError(f"{relative} unavailable ({', '.join(messages)}).")


def stage_day(root, day, client):
    dest = root / "sources" / "nse-daily" / str(day)
    if dest.exists():
        info = json.loads((dest / "sources.json").read_text())
        if any(market.sha((dest / k).read_bytes()) != v["sha256"] for k, v in info.items()):
            raise market.MarketError(f"Source digest mismatch on {day}.")
        return

    def check_bhav(raw):
        normalize_bhav(extract_bhav(raw), day)

    def check_index(raw):
        rows = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        if {r["Index Date"].strip().replace("/", "-") for r in rows} != {day.strftime("%d-%m-%Y")}:
            raise market.MarketError(f"Index date mismatch on {day}.")

    with market.staging(dest.parent) as tmp:
        meta = {}
        for name, relative in source_urls(day).items():
            try:
                raw, url = fetch_file(
                    client, relative, check_bhav if name.endswith(".zip") else check_index
                )
                meta[name] = {"url": url}
            except market.MarketError:
                if name != "indices.csv":
                    raise
                raw, sources = official_indices(client, day)
                meta[name] = {"sources": sources}
            (tmp / name).write_bytes(raw)
            meta[name]["sha256"] = market.sha(raw)
        (tmp / "sources.json").write_text(json.dumps(meta, indent=2))
        try:
            tmp.rename(dest)
        except OSError:
            if not dest.is_dir():
                raise


def stage_history(root, days, workers=4, progress=print):
    pending = [d for d in days if not (root / "sessions" / str(d) / "manifest.json").exists()]
    errors = {}
    with httpx.Client(
        timeout=12, follow_redirects=True, headers={"User-Agent": "ThesisTally/1.0 (NSE research)"}
    ) as client:

        def one(day):
            try:
                stage_day(root, day, client)
            except (ValueError, OSError) as exc:
                return day, str(exc)
            return day, None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, (day, issue) in enumerate(pool.map(one, pending), 1):
                if issue:
                    errors[str(day)] = issue
                if i == 1 or i % 100 == 0:
                    progress(
                        f"Exchange sources {i}/{len(pending)} processed, {len(errors)} unavailable"
                    )
    return errors


def available_membership(root, day):
    return any(
        p.name[:10] <= str(day) <= p.name[-10:]
        for p in (root / "universes").glob("????-??-??_????-??-??")
    )


def coverage(root, days):
    complete = set(market.sessions(root))
    return {
        "calendar": "NSE cash sessions verified with dated Nifty 50 observations",
        "expected_sessions": len(days),
        "archived_sessions": sum(d in complete for d in days),
        "missing_sessions": ranges([d for d in days if d not in complete], days),
        "missing_membership": ranges([d for d in days if not available_membership(root, d)], days),
    }


def reference_sources(root, start, end, workers=4):
    """Cache the dated primary sources for membership, adjustments and results announcements."""
    from urllib.parse import urlencode

    ref = root / "sources" / "reference"
    ref.mkdir(parents=True, exist_ok=True)
    jobs = [("press-release-register.html", "https://www.niftyindices.com/media", "html")]
    for period in pd.period_range(pd.Timestamp(start) - pd.offsets.MonthBegin(1), end, freq="M"):
        stamp = period.strftime("%b%Y")
        jobs.append(
            (
                f"weights-{stamp}.zip",
                f"https://www.niftyindices.com/Indices_-_Market_Capitalisation_and_Weightage/indices_data{stamp}.zip",
                "zip",
            )
        )
    for year in range(start.year, end.year + 1):
        first, last = date(year, 1, 1), min(date(year, 12, 31), end)
        for label, endpoint in (
            ("actions", "corporates-corporateActions"),
            ("meetings", "corporate-board-meetings"),
        ):
            query = urlencode(
                {
                    "index": "equities",
                    "from_date": first.strftime("%d-%m-%Y"),
                    "to_date": last.strftime("%d-%m-%Y"),
                }
            )
            jobs.append(
                (
                    f"{label}-{first}_{last}.json",
                    f"https://www.nseindia.in/api/{endpoint}?{query}",
                    "json",
                )
            )

    def one(job):
        name, url, fmt = job
        dest = ref / name
        manifest = ref / (name + ".source.json")
        if (
            manifest.exists()
            and dest.exists()
            and market.sha(dest.read_bytes()) == json.loads(manifest.read_text())["sha256"]
        ):
            return None
        try:
            response = client.get(url)
            response.raise_for_status()
            raw = response.content
            if fmt == "json" and not isinstance(response.json(), list):
                raise ValueError("Expected a JSON list.")
            if fmt == "zip":
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    if not z.namelist():
                        raise ValueError("Empty weights archive.")
            if fmt == "html" and b"/Press_Release/" not in raw:
                raise ValueError("Missing notice register.")
            with market.staging(ref) as tmp:
                (tmp / name).write_bytes(raw)
                (tmp / name).replace(dest)
                (tmp / "source").write_text(json.dumps({"url": url, "sha256": market.sha(raw)}))
                (tmp / "source").replace(manifest)
        except (httpx.HTTPError, ValueError, zipfile.BadZipFile) as exc:
            return (name, type(exc).__name__)

    with (
        httpx.Client(
            timeout=22, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
        ) as client,
        ThreadPoolExecutor(max_workers=workers) as pool,
    ):
        return dict(x for x in pool.map(one, jobs) if x)


def kite_checks(root, start, end):
    """Collect independent adjusted candles keyed by historical security identity."""
    members = pd.read_parquet(root / "universes" / "reconstructed.parquet", columns=["isin"])
    wanted = set(members["isin"])
    mapping = {}
    sources = sorted((root / "sources" / "nse-daily").glob("????-??-??"), reverse=True)
    for p in sources:
        if p.name < "2024-07-08":
            break
        # One master per month plus both ends covers former listings where available.
        if p.name[8:] not in {"01", "02", "03", "15"} and p != sources[0]:
            continue
        b = normalize_bhav(
            extract_bhav((p / "bhavcopy.zip").read_bytes()), date.fromisoformat(p.name)
        )
        for row in b[b.ISIN.isin(wanted) & (b.SctySrs == "EQ")].itertuples(index=False):
            mapping.setdefault(row.ISIN, (int(row.FinInstrmId) << 8) | 1)
    # Keep credentials out of archived source records.
    folder = root / "sources" / "kite-history" / f"{start}_{end}"
    folder.mkdir(parents=True, exist_ok=True)
    issues = {}
    with market.Feed() as feed:
        for i, (isin, token) in enumerate(sorted(mapping.items())):
            path = folder / (isin + ".parquet")
            if path.is_file():
                continue
            try:
                data = feed.history(token, start, end)
                if data.empty:
                    issues[isin] = "no candles"
                    continue
                data["day"] = pd.to_datetime(data.day, utc=True).dt.tz_convert(market.IST).dt.date
                data["instrument_token"] = token
                with market.staging(folder) as temp:
                    data.to_parquet(temp / "history", index=False)
                    (temp / "history").replace(path)
            except market.MarketError as exc:
                issues[isin] = str(exc)
                if "session or data entitlement" in str(exc):
                    break
    status = {
        "from": str(start),
        "to": str(end),
        "unmapped_isins": sorted(wanted - set(mapping)),
        "issues": issues,
    }
    (folder / "coverage.json").write_text(json.dumps(status, indent=2))
    return status


def official_indices(client, day):
    """Recover missing daily index files from the exchange's per-index history endpoint."""
    url = "https://www.nseindia.in/api/historicalOR/indicesHistory"
    names = [
        "NIFTY 50",
        "NIFTY 200",
        "NIFTY BANK",
        "NIFTY IT",
        "NIFTY AUTO",
        "NIFTY FMCG",
        "NIFTY PHARMA",
        "NIFTY METAL",
        "NIFTY REALTY",
        "NIFTY MEDIA",
        "NIFTY PSU BANK",
        "NIFTY FINANCIAL SERVICES",
        "NIFTY ENERGY",
    ]
    stamp = day.strftime("%d-%m-%Y")
    rows = []
    sources = {}
    for name in names:
        res = client.get(url, params={"indexType": name, "from": stamp, "to": stamp})
        res.raise_for_status()
        data = res.json()["data"]
        for item in data:
            if pd.Timestamp(item["EOD_TIMESTAMP"]).date() != day:
                continue
            rows.append(
                {
                    "Index Name": name,
                    "Index Date": stamp,
                    **{
                        label: item["EOD_" + field + "_INDEX_VAL"]
                        for label, field in zip(
                            [
                                "Open Index Value",
                                "High Index Value",
                                "Low Index Value",
                                "Closing Index Value",
                            ],
                            ["OPEN", "HIGH", "LOW", "CLOSE"],
                        )
                    },
                    "Volume": item["HIT_TRADED_QTY"],
                    "Turnover (Rs. Cr.)": item["HIT_TURN_OVER"],
                }
            )
            sources[name] = {"url": str(res.url), "sha256": market.sha(res.content)}
    if not {"NIFTY 50", "NIFTY 200"} <= set(sources):
        raise market.MarketError(f"No official benchmark histories for {day}.")
    return pd.DataFrame(rows).to_csv(index=False).encode(), sources


def collect_vix(root, start, end):
    folder = root / "sources" / "reference"
    folder.mkdir(parents=True, exist_ok=True)

    def one(p):
        left = max(p.start_time.date(), start)
        right = min(p.end_time.date(), end)
        path = folder / f"vix-{left}_{right}.json"
        meta = path.with_suffix(".json.source.json")
        if (
            path.exists()
            and meta.exists()
            and market.sha(path.read_bytes()) == json.loads(meta.read_text())["sha256"]
        ):
            return
        response = client.get(
            "https://www.nseindia.in/api/historicalOR/vixhistory",
            params={"from": left.strftime("%d-%m-%Y"), "to": right.strftime("%d-%m-%Y")},
        )
        response.raise_for_status()
        if not isinstance(response.json()["data"], list):
            raise market.MarketError(f"Bad VIX history for {p}.")
        with market.staging(folder) as tmp:
            (tmp / "vix").write_bytes(response.content)
            (tmp / "vix").replace(path)
            (tmp / "meta").write_text(
                json.dumps({"url": str(response.url), "sha256": market.sha(response.content)})
            )
            (tmp / "meta").replace(meta)

    with (
        httpx.Client(
            timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
        ) as client,
        ThreadPoolExecutor(max_workers=4) as pool,
    ):
        list(pool.map(one, pd.period_range(start, end, freq="M")))


def audit(root, days):
    """Audit the requested calendar range and the independent provider comparison."""
    result = coverage(root, days)
    included = set(days)
    gaps = root / "universes" / "membership-gaps.json"
    if gaps.exists():
        mem = pd.read_parquet(root / "universes" / "reconstructed.parquet", columns=["day"])
        result["missing_membership"] = ranges(set(days) - set(mem.day), days)
    issues = []
    for d in sorted(set(market.sessions(root)) & included):
        try:
            market.verify(root, d)
            from .exchange import read_context

            read_context(root, d, verify_sources=True)
        except (market.MarketError, OSError, ValueError) as exc:
            issues.append({"day": str(d), "reason": str(exc)})
    result["integrity_errors"] = issues
    q = root / "sources" / "adjusted" / "quarantine.json"
    result["quarantine"] = (
        [r for r in json.loads(q.read_text()) if date.fromisoformat(r["day"]) in included]
        if q.exists()
        else []
    )
    result["complete"] = bool(days) and not any(
        result[k] for k in ["missing_sessions", "missing_membership", "integrity_errors"]
    )
    return result


def crosscheck(root):
    data = root / "sources" / "adjusted"
    files = sorted(data.glob("????.parquet"))
    if not files:
        return {"compared": 0, "status": "adjusted tape unavailable"}
    cols = ["isin", "day", *market.PRICES]
    prices = pd.concat([pd.read_parquet(p, columns=cols) for p in files], ignore_index=True)
    tables = dict(tuple(prices.groupby("isin")))
    provider = {}
    for path in sorted((root / "sources" / "kite-history").glob("*/*.parquet")):
        provider[path.stem] = path
    results = []
    total = 0
    for isin, path in provider.items():
        if isin not in tables:
            continue
        k = pd.read_parquet(path).drop_duplicates()
        ambiguous = k.day.duplicated(keep=False)
        for day in k.loc[ambiguous, "day"].unique():
            results.append({"isin": isin, "day": str(day), "reason": "conflicting Kite candles"})
        k = k.loc[~ambiguous]
        pair = tables[isin].merge(k, on="day", suffixes=("_nse", "_kite"), validate="one_to_one")
        scale = pair.close_nse / pair.close_kite
        mismatch = pd.Series(False, index=pair.index)
        for col in ("open", "high", "low"):
            official = pair[col + "_nse"]
            mismatch |= (pair[col + "_kite"] * scale - official).abs() > np.maximum(
                0.10, official * 0.001
            )
        for row in pair[mismatch].itertuples():
            results.append(
                {
                    "isin": isin,
                    "day": str(row.day),
                    "kite_close": row.close_kite,
                    "nse_close": row.close_nse,
                }
            )
        total += len(pair)
    report = {
        "compared": total,
        "method": "NSE OHLC compared to independently adjusted Kite candles on the session's close scale",
        "unavailable_isins": sorted(set(tables) - set(provider) - set(market.BENCHMARKS)),
        "differences": results,
    }
    output = root / "sources" / "kite-comparison.json"
    with market.staging(output.parent) as tmp:
        p = tmp / "result"
        p.write_text(json.dumps(report, indent=2))
        p.replace(output)
    return {
        "compared": total,
        "mismatches": len(results),
        "unavailable_isins": len(report["unavailable_isins"]),
    }


def official_calendar(root, start, end):
    """Use monthly exchange observations to avoid the historical endpoint's row cap."""
    folder = root / "sources" / "calendar" / "nse"
    folder.mkdir(parents=True, exist_ok=True)

    def one(period):
        left = max(period.start_time.date(), start)
        right = min(period.end_time.date(), end)
        path = folder / f"{left}_{right}.json"
        if not path.exists():
            url = "https://www.nseindia.in/api/historicalOR/indicesHistory"
            resp = client.get(
                url,
                params={
                    "indexType": "NIFTY 50",
                    "from": left.strftime("%d-%m-%Y"),
                    "to": right.strftime("%d-%m-%Y"),
                },
            )
            resp.raise_for_status()
            observations = resp.json()["data"]
            if not isinstance(observations, list):
                raise market.MarketError(f"No exchange calendar for {period}")
            payload = {
                "source_url": str(resp.url),
                "rows": observations,
                "sha256": market.sha(resp.content),
            }
            with market.staging(folder) as temp:
                (temp / "source").write_bytes(resp.content)
                (temp / "source").replace(path.with_suffix(".raw"))
                (temp / "meta").write_text(json.dumps(payload))
                (temp / "meta").replace(path)
        meta = json.loads(path.read_text())
        raw = path.with_suffix(".raw").read_bytes()
        if market.sha(raw) != meta["sha256"]:
            raise market.MarketError(f"Calendar digest mismatch for {period}")
        return [pd.Timestamp(r["EOD_TIMESTAMP"]).date() for r in json.loads(raw)["data"]]

    with (
        httpx.Client(
            timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
        ) as client,
        ThreadPoolExecutor(max_workers=4) as pool,
    ):
        days = [d for group in pool.map(one, pd.period_range(start, end, freq="M")) for d in group]
    if len(set(days)) != len(days) or any(d < start or d > end for d in days):
        raise market.MarketError("Duplicate or misdated exchange calendar observations.")
    return sorted(days)


def verified_calendar(root, start, end):
    """Reconcile independent calendar feeds and verify special sessions individually."""
    folder = root / "sources" / "calendar"
    provider = set(calendar(root, start, end))
    official = set(official_calendar(root, start, end))
    special = []
    from .constituents import load_daily

    for day in sorted(provider - official):
        try:
            prices, indices = load_daily(root, day)
        except (KeyError, OSError, ValueError) as exc:
            raise market.MarketError(f"Unconfirmed session {day} in provider calendar") from exc
        if prices.empty or "NIFTY 50" not in indices.index:
            raise market.MarketError(f"Missing official prices for special session {day}.")
        special.append(str(day))
    dates = sorted(official | provider)
    report = {
        "from": str(start),
        "to": str(end),
        "sessions": len(dates),
        "monthly_nse_count": len(official),
        "provider_count": len(provider),
        "special_sessions_confirmed_by_dated_nse_finals": special,
        "provider_missing_dates": [str(d) for d in sorted(official - provider)],
    }
    path = folder / f"reconciliation-{start}_{end}.json"
    with market.staging(folder) as tmp:
        out = tmp / "report"
        out.write_text(json.dumps(report, indent=2) + "\n")
        if not path.exists() or path.read_bytes() != out.read_bytes():
            out.replace(path)
    return dates
