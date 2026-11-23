"""Dated, public NSE static opening-band evidence before the public rupee-limit master."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import lru_cache
from pathlib import Path

import httpx

from . import market
from .baseline_controls import FIRST_MASTER
from .legacy_surveillance import Cache, EmptyResponse
from .pilot_data import write_json


def receipt(folder, prior):
    name = f"sec_list_{prior:%d%m%Y}.csv"
    url = f"https://nsearchives.nseindia.in/content/equities/{name}"
    file = folder / "raw" / name
    missing = folder / "missing" / (name + ".json")
    if file.is_file() and file.with_suffix(".csv.source.json").is_file():
        meta = json.loads(file.with_suffix(".csv.source.json").read_bytes())
        if meta["url"] != url or meta["sha256"] != market.sha(file.read_bytes()):
            raise market.MarketError(f"Historical band source checksum failed on {prior}")
        return {**meta, "status": 200}, file
    if missing.is_file():
        evidence = json.loads(missing.read_bytes())
        if evidence["url"] != url or evidence["status"] not in (404, "empty_http_200"):
            raise market.MarketError(f"Unverified band-file gap on {prior}")
        return evidence, None
    return None, file


def collect(root, sessions):
    folder = root / "baseline" / "legacy-surveillance"
    cache = Cache(folder)
    view = root / "baseline" / "market"

    def fetch(day):
        prior = date.fromisoformat(
            json.loads((view / "sessions" / str(day) / "manifest.json").read_bytes())["prior_day"]
        )
        if prior >= FIRST_MASTER:
            return None
        proof, _ = receipt(folder, prior)
        if proof:
            return str(prior), proof
        name = f"sec_list_{prior:%d%m%Y}.csv"
        url = f"https://nsearchives.nseindia.in/content/equities/{name}"
        try:
            cache.get("raw/" + name, url)
        except (httpx.HTTPStatusError, EmptyResponse) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code != 404:
                raise
            absent = {
                "url": url,
                "status": 404 if isinstance(exc, httpx.HTTPStatusError) else "empty_http_200",
            }
            write_json(folder / "missing" / (name + ".json"), absent)
        proof, _ = receipt(folder, prior)
        if not proof:
            raise market.MarketError(f"Band archive inventory missing on {prior}")
        return str(prior), proof

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            rows = dict(item for item in pool.map(fetch, sessions) if item)
    finally:
        cache.client.close()
    dest = root / "baseline" / "static-bands"
    dest.mkdir(exist_ok=True)
    write_json(dest / "inventory.json", rows)
    return rows


DOCUMENTS = {
    "CMTR62174.pdf": (
        "https://nsearchives.nseindia.in/content/circulars/CMTR62174.pdf",
        "073c209b8f9d181841afc15e77a053156fe1502f9ed012547e44dc639507f1a0",
    ),
    "secondary-market-faq.pdf": (
        "https://nsearchives.nseindia.in/web/sites/default/files/2023-02/Basic%20FAQs%20on%20Secondary%20Market.pdf",
        "1be029fe7195bfb6af594a3994ee45e0bf8e6e8cb7f80e46dafd12ce69cfcdb9",
    ),
}


def exact_limits(close, label):
    """Require exact mathematical limits on the pre-June-2024 5-paise tick grid."""
    from decimal import Decimal

    if label.strip() not in {"2", "5", "10", "20"}:
        return None
    close = Decimal(str(close))
    if not close.is_finite() or close <= 0 or close % Decimal("0.05"):
        return None
    move = close * Decimal(label) / 100
    low, high = close - move, close + move
    if any(p % Decimal("0.05") for p in (low, high)):
        return None
    return [float(low), float(high)]


def daily_labels(raw):
    import csv
    import io

    # The trading symbol and band are ASCII; some vintage company names use Windows punctuation.
    table = raw.decode("utf-8-sig") if raw.startswith(b"\xef\xbb\xbf") else raw.decode("latin-1")
    labels = {}
    for row in csv.DictReader(io.StringIO(table)):
        if row["Series"].strip() == "EQ":
            symbol = row["Symbol"].strip()
            value = row["Band"].strip()
            if symbol in labels and labels[symbol] != value:
                raise market.MarketError(f"Conflicting EQ bands in the dated NSE list for {symbol}")
            labels[symbol] = value
    return labels


def action_days(root):
    from collections import defaultdict

    from .baseline_earnings import issuer
    from .historical_sessions import reference_rows

    affected = defaultdict(set)
    events, _, _, _ = reference_rows(root)
    for (day, symbol), rows in events.items():
        affect = affected[day]
        affect.add(symbol)
        for row in rows:
            isin = row.get("isin")
            if isin:
                affect.add(isin)
                if key := issuer(isin):
                    affect.add(key)
    return affected


def prepare(root, sessions):
    import csv
    import io

    from .baseline_earnings import issuer

    root = Path(root)
    view = root / "baseline" / "market"
    dest = view / "sources" / "static-bands"
    dest.mkdir(parents=True, exist_ok=True)
    proof_dir = root / "baseline" / "evidence" / "static-bands"
    proof_dir.mkdir(parents=True, exist_ok=True)
    for name, (url, digest) in DOCUMENTS.items():
        p = proof_dir / name
        if not p.exists():
            response = httpx.get(url, timeout=40, follow_redirects=True)
            response.raise_for_status()
            if market.sha(response.content) != digest:
                raise market.MarketError(f"Different NSE band rule reference at {url}")
            with market.staging(proof_dir) as tmp:
                (tmp / name).write_bytes(response.content)
                (tmp / name).replace(p)
        if market.sha(p.read_bytes()) != digest:
            raise market.MarketError(f"Stage 2 band rule evidence changed: {name}")

    from . import legacy_band_reviews

    inventory = collect(root, sessions)
    review_proof = legacy_band_reviews.prepare(root, sessions)
    if review_proof.get("complete") is not True:
        raise market.MarketError("Incomplete vintage percentage change inventory")
    changes = json.loads((root / "baseline/price-band-reviews/coverage.json").read_bytes())["days"]
    source = root / "baseline" / "legacy-surveillance"
    affected = action_days(root)
    for term in json.loads(
        (Path(__file__).with_name("revisions") / "historical-split-evidence.json").read_bytes()
    ):
        affected[date.fromisoformat(term["effective"])].update((term["symbol"], term["old_isin"]))
    manifests = {}
    counts = Counter()
    special = set()
    for calendar in Path(__file__).with_name("calendars").glob("*.json"):
        official = json.loads(calendar.read_bytes())
        special.update(official.get("special_sessions", []))
        special.update(official.get("holidays", []))
    for daystr in sessions:
        day = date.fromisoformat(daystr)
        folder = view / "sessions" / daystr
        prior = date.fromisoformat(json.loads((folder / "manifest.json").read_bytes())["prior_day"])
        if (
            prior >= FIRST_MASTER
            or max(day.weekday(), prior.weekday()) >= 5
            or daystr in special
            or str(prior) in special
        ):
            continue
        previous = view / "sessions" / str(prior)
        entry = inventory[str(prior)]
        labels = {}
        if entry["status"] == 200:
            counts["published_percentage_sessions"] += 1
            _, path = receipt(source, prior)
            labels = daily_labels(path.read_bytes())
        else:
            counts["unavailable_full_list_sessions"] += 1
        changes_today = legacy_band_reviews.labels(root, day) if daystr in changes else {}
        if not changes_today and not labels:
            continue
        if not previous.exists():
            continue
        with (previous / "members.csv").open(newline="") as file:
            previous_members = {
                r["ISIN Code"]: r["Symbol"]
                for r in csv.DictReader(file)
                if r["Series"].strip() == "EQ"
            }
        with (folder / "members.csv").open(newline="") as file:
            members = {
                r["ISIN Code"]: r["Symbol"]
                for r in csv.DictReader(file)
                if r["Series"].strip() == "EQ"
            }
        prior_raw = (previous / "bhavcopy.csv").read_bytes()
        current = {}
        for r in csv.DictReader(io.StringIO(prior_raw.decode("utf-8-sig"))):
            sym, isin = r["TckrSymb"].strip(), r["ISIN"].strip()
            if (
                r["SctySrs"].strip() != "EQ"
                or members.get(isin) != sym
                or previous_members.get(isin) != sym
            ):
                continue
            if int(float(r["TtlTradgVol"])) <= 0:
                continue
            review = changes_today.get(sym)
            label = labels.get(sym, "")
            if review is not None:
                if review["isin"] != isin or review["published"] > str(prior):
                    raise market.MarketError(f"Invalid dated band review for {sym} on {day}")
                if label and label != str(review["percent"]):
                    raise market.MarketError(
                        f"Full list and band revision disagree for {sym} on {day}"
                    )
                label = str(review["percent"])
            if label not in {"2", "5", "10", "20"}:
                counts["unknown_or_dynamic"] += 1
                continue
            if {sym, isin, issuer(isin)} & affected[day]:
                counts["excluded_ex_date"] += 1
                continue
            bounds = exact_limits(r["ClsPric"], label)
            if bounds is None:
                counts["nonintegral_tick"] += 1
                continue
            counts["from_full_list" if sym in labels else "from_dated_review_only"] += 1
            current[sym] = {
                "isin": isin,
                "floor": bounds[0],
                "ceiling": bounds[1],
                "prior_close": float(r["ClsPric"]),
                "percent": int(label),
                "review_sources": review["sources"] if review else [],
            }
        if not current:
            continue
        # Falsification only. No same-day observation supplies an opening bound.
        with (folder / "bhavcopy.csv").open(newline="") as file:
            observed = {r["ISIN"]: r for r in csv.DictReader(file) if r["SctySrs"].strip() == "EQ"}
        for sym, row in current.items():
            obs = observed.get(row["isin"])
            if (
                not obs
                or obs["TckrSymb"].strip() != sym
                or float(obs["LwPric"]) < row["floor"]
                or float(obs["HghPric"]) > row["ceiling"]
            ):
                raise market.MarketError(
                    f"Historical range falsifies static-band evidence: {day} {sym}"
                )
        content = {
            "day": daystr,
            "prior": str(prior),
            "list": entry,
            "prior_bhav_sha256": market.sha(prior_raw),
            "bounds": current,
        }
        output = dest / (daystr + ".json")
        write_json(output, content)
        manifests[daystr] = market.sha(output.read_bytes())
        counts["restored_member_observations"] += len(current)
    proof = {
        "complete": True,
        "days": manifests,
        "counts": dict(counts),
        "documents": DOCUMENTS,
        "dated_review_evidence": review_proof,
        "inventory_sha256": market.sha(
            (root / "baseline/static-bands/inventory.json").read_bytes()
        ),
        "method": "Previous-session numeric EQ band and traded close. Exact 5-paise endpoints only. Continuous dated constituent identity; exclude special sessions and issuer or symbol ex-dates. Never infer dynamic limits.",
    }
    write_json(dest / "coverage.json", proof)
    return {k: v for k, v in proof.items() if k != "days"} | {
        "sessions_restored": len(manifests),
        "coverage_sha256": market.sha((dest / "coverage.json").read_bytes()),
    }


@lru_cache(maxsize=3)
def _index(path, stamp, size):
    return json.loads(path.read_bytes())["days"]


def overlay(root, day):
    path = root / "sources/static-bands/coverage.json"
    if not path.is_file():
        return {}
    stat = path.stat()
    expected = _index(path, stat.st_mtime_ns, stat.st_size).get(str(day))
    if expected is None:
        return {}
    raw = (path.parent / (str(day) + ".json")).read_bytes()
    if market.sha(raw) != expected:
        raise market.MarketError(f"Unverified static band on {day}")
    return json.loads(raw)["bounds"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    args = parser.parse_args()
    archive = args.root / "baseline" / "market" / "sessions"
    dates = sorted(p.name for p in archive.iterdir() if p.is_dir() and p.name < "2024-02-07")
    result = collect(args.root, dates)
    print(dict(Counter(row["status"] for row in result.values())))
