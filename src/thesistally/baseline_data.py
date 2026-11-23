"""Verify the full historical market input independently of the Stage 1 pilot."""

import argparse
import json
from datetime import date
from pathlib import Path

import httpx

from . import market, pilot_data

# NSE's August 2021 index review is an image-only PDF. Page 1 specifies the
# effective date; the broad Nifty 200 replacement table is on printed page 14.
INDEX_REVIEW = "https://www.niftyindices.com/Press_Release/ind_prs23082021.pdf"
INDEX_REVIEW_HASH = "c9b9feef248ca89f7d8c8f12009e3d5c409eb4d6c793eee061fb93bf0af1231e"


def collect_index_review(root):
    ref = root / "sources" / "reference"
    inventory = json.loads((ref / "notice-inventory.json").read_bytes())
    record = next(row for row in inventory if row["url"] == INDEX_REVIEW)
    if record["issued"] != "2021-08-23":
        raise market.MarketError("Unexpected publication date for the 2021 index review")
    folder = ref / "notices"
    key = market.sha(INDEX_REVIEW.encode())[:16]
    pdf = folder / (key + ".pdf")
    if not pdf.exists():
        response = httpx.get(
            INDEX_REVIEW, timeout=45, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
        )
        response.raise_for_status()
        raw = response.content
        if market.sha(raw) != INDEX_REVIEW_HASH:
            raise market.MarketError("Changed 2021 index review")
        with market.staging(folder) as tmp:
            (tmp / "notice").write_bytes(raw)
            (tmp / "notice").replace(pdf)
    if market.sha(pdf.read_bytes()) != INDEX_REVIEW_HASH:
        raise market.MarketError("2021 index review checksum mismatch")
    pilot_data.write_json(
        folder / (key + ".json"),
        {
            "issued": record["issued"],
            "title": record["title"],
            "url": INDEX_REVIEW,
            "sha256": INDEX_REVIEW_HASH,
            "mentions_index": True,
            "transcription_pages": [1, 14],
        },
    )


def split_terms(subjects):
    """Read the quoted face values, including abbreviated historical NSE wording."""
    import re
    from fractions import Fraction

    split = re.compile(
        r"(?:split|splt).*?(?:from\s*|frm\s*)?(?:rs\.?|re\.?)\s*(\d+(?:\.\d+)?)"
        r".*?\bto\s*(?:rs\.?|re\.?)\s*(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    ratios = {Fraction(a) / Fraction(b) for s in subjects for a, b in split.findall(s)}
    if len(ratios) != 1:
        return None
    ratio = ratios.pop()
    for x, y in {
        term
        for s in subjects
        for term in re.findall(r"bonus\s+(\d+)\s*:\s*(\d+)", s, re.IGNORECASE)
    }:
        ratio *= 1 + Fraction(int(x), int(y))
    return ratio


def restore_dated_splits(tape, view, ledger, actions):
    """Set ex-date units from contemporaneous circulars, even on a tape ending on that date."""
    from .historical_sessions import FIELDS

    dated = json.loads(
        (Path(__file__).with_name("revisions") / "historical-split-evidence.json").read_bytes()
    )
    flags = {(r["day"], r.get("isin")): r for r in ledger}
    restored = []
    folder = view.parent / "evidence" / "splits"
    folder.mkdir(parents=True, exist_ok=True)
    for term in dated:
        day = date.fromisoformat(term["effective"])
        name = term["symbol"]
        history = tape[tape["isin"].eq(term["old_isin"]) & tape.day.le(day)].sort_values("day")
        if history.empty or history.day.iloc[-1] != day:
            continue
        if term["old_shares"] <= 0 or term["new_shares"] <= 0 or len(history) < 2:
            raise market.MarketError(f"Invalid dated split record for {name} on {day}")
        for source in term["sources"]:
            if source["issued"] > str(day):
                raise market.MarketError(f"Late split circular for {name} on {day}")
            path = folder / source["url"].rsplit("/", 1)[1]
            if not path.is_file():
                response = httpx.get(
                    source["url"],
                    timeout=45,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                response.raise_for_status()
                if market.sha(response.content) != source["sha256"]:
                    raise market.MarketError(f"Changed circular for {name} on {day}")
                with market.staging(folder) as tmp:
                    (tmp / "pdf").write_bytes(response.content)
                    (tmp / "pdf").replace(path)
            if market.sha(path.read_bytes()) != source["sha256"]:
                raise market.MarketError(f"Corrupt circular for {name} on {day}")
        predecessor, affected = history.iloc[-2], history.iloc[-1]
        if affected.symbol != name:
            raise market.MarketError(f"Unexpected historical symbol for {name} on {day}")
        ix = affected.name
        ratio = term["new_shares"] / term["old_shares"]
        tape.loc[ix, ["chain", "price_factor", "share_factor", "unexplained_jump"]] = [
            predecessor.chain,
            predecessor.price_factor / ratio,
            predecessor.share_factor * ratio,
            False,
        ]
        for field in FIELDS:
            factor = "share_factor" if field == "volume" else "price_factor"
            tape.loc[ix, "adjusted_" + field] = affected[field] / tape.loc[ix, factor]
        subject = term["subject"]
        if split_terms([subject]) != ratio:
            raise market.MarketError(f"Share ratio disagrees with face values for {name} on {day}")
        # Keep a separately documented valuation blocker for a holder. Quoted
        # successor units on the ex-date do not prove delivery to that holder.
        row = flags[str(day), term["old_isin"]]
        row.update(reason="unresolved_action", subject=subject, source=term["sources"][0]["url"])
        actions[day, name] = [
            *actions.get((day, name), []),
            {"subject": subject, "source": row["source"]},
        ]
        restored.append(term)
    return restored


def index_identifier_history(tape, members, actions, days):
    """Reconcile splits in their published units on the first observed new ISIN.

    Some older bhavcopies print the new units under the old ISIN on the
    ex-date. Join the successor only after it prints. Retain a fresh series
    where the archived ex-date factors or a compulsory election are unresolved.
    """
    from collections import defaultdict

    import numpy as np

    from .historical_sessions import FIELDS

    named = defaultdict(list)
    for (day, symbol), rows in actions.items():
        for row in rows:
            named[day, symbol].append(row)
            if row.get("isin"):
                named[day, row["isin"]].append(row)
    universe = set(members["isin"])
    ordered = tape.sort_values(["symbol", "day"])
    by_name = ordered.groupby("symbol", sort=False)
    prior = by_name["isin"].shift()
    previous = by_name.day.shift()
    before = by_name.day.shift(2)
    switches = ordered[
        ordered.day.between(days[0], days[-1])
        & prior.notna()
        & prior.ne(ordered["isin"])
        & (prior.isin(universe) | ordered["isin"].isin(universe))
    ].sort_values("day")
    rows = {(s, d): i for i, s, d in zip(ordered.index, ordered.symbol, ordered.day)}
    evidence = []
    active = set(zip(members.day, members.symbol))
    for c in switches.itertuples():
        old_id, old_day = prior.loc[c.Index], previous.loc[c.Index]
        prior_idx = rows[c.symbol, old_day]
        old, new = tape.loc[prior_idx], tape.loc[c.Index]
        day_records = [
            (d, {r["subject"] for key in (c.symbol, c.isin, old_id) for r in named[d, key]})
            for d in (c.day, old_day)
        ]
        found = [
            (d, subjects, ratio) for d, subjects in day_records if (ratio := split_terms(subjects))
        ]
        if len(found) > 1:
            raise market.MarketError(f"Ambiguous ex-date for {c.symbol} on {c.day}")
        item = {
            "symbol": c.symbol,
            "old_isin": old_id,
            "new_isin": c.isin,
            "first_printed_new_isin": str(c.day),
            "last_printed_old_isin": str(old_day),
            "old_quote_source": pilot_data.exchange_url(old_day, "bhavcopy.zip"),
            "new_quote_source": pilot_data.exchange_url(c.day, "bhavcopy.zip"),
        }
        if not found:
            if old.chain == new.chain:
                raise market.MarketError(
                    f"Shared price history without dated terms: {c.symbol} {c.day}"
                )
            item.update(
                effective=None,
                mode="separate_chains",
                reason="No dated split terms established",
                active_on_switch=(c.day, c.symbol) in active,
            )
            evidence.append(item)
            continue
        ex, subjects, ratio = found[0]
        item.update(
            effective=str(ex),
            new_shares=ratio.numerator,
            old_shares=ratio.denominator,
            subjects=sorted(subjects),
            active_on_ex_date=(ex, c.symbol) in active,
        )
        if ex == c.day:
            parent = old
            wanted_price = parent.price_factor / float(ratio)
            wanted_shares = parent.share_factor * float(ratio)
        else:
            before_row = tape.loc[rows[c.symbol, before.loc[c.Index]]]
            if not (
                np.isclose(old.price_factor / before_row.price_factor, 1 / float(ratio))
                and np.isclose(old.share_factor / before_row.share_factor, float(ratio))
                and old.chain == before_row.chain
            ):
                if old.chain == new.chain:
                    raise market.MarketError(f"Unproved price continuity on {ex} for {c.symbol}")
                item.update(
                    mode="separate_chains",
                    reason="First new ISIN follows an unrebased ex-date; prior history excluded",
                )
                evidence.append(item)
                continue
            parent = old
            wanted_price, wanted_shares = parent.price_factor, parent.share_factor
        if old.chain == new.chain:
            if not (
                np.isclose(new.price_factor, wanted_price)
                and np.isclose(new.share_factor, wanted_shares)
            ):
                raise market.MarketError(f"Existing split factors disagree on {ex} for {c.symbol}")
            item["mode"] = "verified_shared"
        else:
            later = tape.chain.eq(new.chain) & tape.day.ge(c.day)
            tape.loc[later, "chain"] = parent.chain
            tape.loc[later, "price_factor"] *= wanted_price / new.price_factor
            tape.loc[later, "share_factor"] *= wanted_shares / new.share_factor
            for key in FIELDS:
                factor = "share_factor" if key == "volume" else "price_factor"
                tape.loc[later, "adjusted_" + key] = tape.loc[later, key] / tape.loc[later, factor]
            item["mode"] = "joined_on_new_print"
        evidence.append(item)
    return evidence


def publish_membership(root, view):
    """Advance the canonical citations only from a recognized, archived predecessor."""
    import pandas as pd

    path = root / "universes" / "nifty200_membership.csv"
    original = pilot_data.interval_archive(root)
    approved = json.loads((root / "pilot" / "preflight.json").read_bytes())["canonical_membership"]
    validated = view / "universes" / path.name
    if pilot_data.identity_spans(pd.read_csv(validated)) != pilot_data.identity_spans(
        pd.read_csv(original)
    ):
        raise market.MarketError("Full preflight changed a previously published ISIN interval")
    raw = validated.read_bytes()
    prior = path.read_bytes()
    if prior != raw:
        checksum = market.sha(prior)
        if checksum not in {original.stem, approved["published_sha256"]}:
            raise market.MarketError("Canonical membership changed outside the certified archives")
        backup = original.parent / (checksum + ".csv")
        if not backup.exists():
            with market.staging(backup.parent) as tmp:
                (tmp / "original").write_bytes(prior)
                (tmp / "original").replace(backup)
        if market.sha(backup.read_bytes()) != checksum:
            raise market.MarketError("Failed to preserve canonical predecessor")
        with market.staging(path.parent) as tmp:
            (tmp / "full").write_bytes(raw)
            (tmp / "full").replace(path)
    return {
        "published_sha256": market.sha(raw),
        "original_sha256": original.stem,
        "stage1_sha256": approved["published_sha256"],
        "preserved_all_identity_intervals": True,
    }


def restore_benchmark_warmup(root, tape):
    """Recover the dated index OHLC from NSE when an older cache omitted the historic name."""
    import pandas as pd

    from . import constituents

    missing = sorted(
        set(tape[tape.symbol.eq("NIFTY 200")].day) - set(tape[tape.symbol.eq("NIFTY 50")].day)
    )
    if not missing:
        return tape, []
    restored = []
    for day in missing:
        _, indices = constituents.load_daily(root, day)
        quote = indices.loc["NIFTY 50"]
        if not isinstance(quote, pd.Series):
            raise market.MarketError(f"Ambiguous historical Nifty 50 benchmark on {day}")
        values = {
            field: float(quote[source + " Index Value"])
            for field, source in zip(market.PRICES, ["Open", "High", "Low", "Closing"])
        }
        if not (
            0
            < values["low"]
            <= min(values["open"], values["close"])
            <= max(values["open"], values["close"])
            <= values["high"]
        ):
            raise market.MarketError(f"Invalid historical Nifty 50 benchmark on {day}")
        values["volume"] = 0
        restored.append(
            dict(
                day=day,
                symbol="NIFTY 50",
                isin="NIFTY 50",
                chain="NIFTY 50",
                price_factor=1.0,
                share_factor=1.0,
                unexplained_jump=False,
                **values,
                **{"adjusted_" + k: v for k, v in values.items()},
            )
        )
    combined = pd.concat([tape, pd.DataFrame(restored)], ignore_index=True)
    bench = combined[combined.symbol.eq("NIFTY 50")].sort_values("day")
    combined.loc[bench.index, "prior_close"] = bench.close.shift().to_numpy()
    return combined, missing


def compare_index_editions(original, revised):
    previous = market.csv_table(original).set_index("Index Name", verify_integrity=True)
    latest = market.csv_table(revised).set_index("Index Name", verify_integrity=True)
    if not set(previous.index) <= set(latest.index) or not previous.equals(
        latest.reindex(previous.index)
    ):
        raise market.MarketError("Changed or missing observations in revised NSE index final")
    return sorted(set(latest.index) - set(previous.index))


def audit_primary_archives(root, sessions):
    """Compare every input bhavcopy and benchmark file to its dated NSE archive."""
    import hashlib

    from . import history

    evidence = root / "baseline" / "evidence"
    digest = hashlib.sha256()
    totals = {"existing_archives": 0, "live_session_archives": 0}
    index_editions = []
    with httpx.Client(
        timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
    ) as client:
        for stamp in sessions:
            day = date.fromisoformat(stamp)
            session = root / "sessions" / stamp
            manifest = json.loads((session / "manifest.json").read_bytes())
            latest_layout = "source_files" not in manifest
            if latest_layout:
                # Live snapshots kept the original CSV inside each NSE ZIP. Cache the
                # ZIP separately so the untouched snapshot can be independently checked.
                history.stage_day(evidence, day, client)
                source_root = evidence
                totals["live_session_archives"] += 1
            else:
                source_root = root
                totals["existing_archives"] += 1
            archive = source_root / "sources" / "nse-daily" / stamp
            raw = (archive / "sources.json").read_bytes()
            files = json.loads(raw)
            if not latest_layout and manifest["source_files"] != files:
                raise market.MarketError(
                    f"Conflict between session and source manifests on {stamp}"
                )
            indices = (archive / "indices.csv").read_bytes()
            zipped = (archive / "bhavcopy.zip").read_bytes()
            if (
                market.sha(indices) != files["indices.csv"]["sha256"]
                or market.sha(zipped) != files["bhavcopy.zip"]["sha256"]
            ):
                raise market.MarketError(f"Official archive checksum differs on {stamp}")
            if market.sha(indices) != manifest["sha256"]["indices.csv"]:
                original = (session / "indices.csv").read_bytes()
                if not latest_layout or market.sha(original) != manifest["sha256"]["indices.csv"]:
                    raise market.MarketError(f"Stage 2 index input differs on {stamp}")
                additions = compare_index_editions(original, indices)
                index_editions.append(
                    {
                        "day": stamp,
                        "original_sha256": market.sha(original),
                        "revised_sha256": market.sha(indices),
                        "additional_index_rows": additions,
                    }
                )
            quote = history.extract_bhav(zipped)
            if not latest_layout:
                quote = history.normalize_bhav(quote, day).to_csv(index=False).encode()
            if market.sha(quote) != manifest["sha256"]["bhavcopy.csv"]:
                raise market.MarketError(
                    f"Official bhavcopy differs from the replay input on {stamp}"
                )
            digest.update(stamp.encode() + raw)
    return {
        "sessions": len(sessions),
        "source_manifests_sha256": digest.hexdigest(),
        "index_editions": index_editions,
        **totals,
    }


def audit_warmup(root, start, end):
    """Verify the official warm-up sources directly, including dates before daily snapshots."""
    import hashlib

    from . import history

    days, calendar = pilot_data.calendar_dates(root, start, end, include_raw=True)
    digest = hashlib.sha256()
    for day in days:
        directory = root / "sources" / "nse-daily" / str(day)
        manifest = (directory / "sources.json").read_bytes()
        files = json.loads(manifest)
        for name in ("bhavcopy.zip", "indices.csv"):
            raw = (directory / name).read_bytes()
            if market.sha(raw) != files[name]["sha256"]:
                raise market.MarketError(f"Corrupt warm-up {name} on {day}")
            if name == "bhavcopy.zip":
                history.normalize_bhav(history.extract_bhav(raw), day)
        digest.update(str(day).encode() + manifest)
    return {
        "start": str(start),
        "end": str(end),
        "sessions": len(days),
        "source_manifests_sha256": digest.hexdigest(),
        "calendar": calendar,
    }


def control_coverage(path, days):
    raw = path.read_bytes() if path.is_file() else b"{}"
    verified = json.loads(raw)
    if verified.get("complete") is not True:
        raise market.MarketError(f"Dated control collection is incomplete at {path}")
    expected = [str(d) for d in days if verified["from"] <= str(d) <= verified["to"]]
    records = {r["day"]: r for r in verified["days"]}
    if (
        list(records) != expected
        or len(expected) != len(verified["days"])
        or len(expected) != verified["sessions"]
    ):
        raise market.MarketError(f"Missing, duplicated or misdated control sessions at {path}")
    return records, market.sha(raw)


def legacy_control_view(base, view, day, record, verified_files, existing=None):
    """Bind the eligible, dated ISINs and their evidence to the standard per-day overlay."""
    from . import exchange

    root = base / "legacy-surveillance"
    source = root / str(day)
    manifest = source / "sources.json"
    if not manifest.is_file() or market.sha(manifest.read_bytes()) != record["sources_sha256"]:
        raise market.MarketError(f"Legacy surveillance manifest differs from its receipt on {day}")
    details = json.loads(manifest.read_bytes())
    original = (source / "symbols.json").read_bytes()
    if market.sha(original) != details["symbols_hash"]:
        raise market.MarketError(f"Invalid legacy surveillance classifications for {day}")
    previous = json.loads((view / "sessions" / str(day) / "manifest.json").read_bytes())[
        "prior_day"
    ]
    if details["prior"] != previous:
        raise market.MarketError(f"Legacy surveillance exchange calendar differs on {day}")
    files = {}
    linked = {}
    for name, item in details["files"].items():
        path = (source / name).resolve()
        if not path.is_file() and (root / name).is_file():
            path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise market.MarketError(f"Invalid legacy evidence file {name} on {day}")
        if path not in verified_files:
            verified_files[path] = market.sha(path.read_bytes())
        checksum = verified_files[path]
        if checksum != item["sha256"]:
            raise market.MarketError(f"Corrupt legacy surveillance evidence {name} on {day}")
        filename = checksum + ".source"
        files[filename] = {"url": item["url"], "sha256": checksum}
        linked[filename] = path
    symbols = json.loads(original)
    if not symbols:
        return None
    if not linked:
        raise market.MarketError(f"Legacy classifications without any source evidence on {day}")
    members = market.read_members((view / "sessions" / str(day) / "members.csv").read_bytes())
    isins = dict(zip(members.symbol, members["isin"]))
    # The base context may already hold stronger dated controls. This overlay
    # supplies only previously unknown surveillance. It never sets price bands.
    raw_context = json.loads(exchange.context_file(view, day).read_bytes())["symbols"]
    values = {}
    if existing and (existing / "sources.json").is_file():
        existing_manifest = json.loads((existing / "sources.json").read_bytes())
        data = (existing / "symbols.json").read_bytes()
        if (
            market.sha(data) != existing_manifest["symbols_hash"]
            or existing_manifest["prior"] != previous
        ):
            raise market.MarketError(f"Unexpected controls for overlap date {day}")
        values = json.loads(data)
        for name, item in existing_manifest["files"].items():
            files[name] = item
            linked[name] = existing / name
    supplemented = 0
    for symbol, entry in symbols.items():
        if symbol not in isins or isins[symbol] != entry["isin"]:
            raise market.MarketError(
                f"Legacy surveillance security does not match membership on {day}: {symbol}"
            )
        classification = entry["surveillance"]
        if classification == "unknown":
            continue
        if type(classification) is not bool or (entry.get("floor"), entry.get("ceiling")) != (
            None,
            None,
        ):
            raise market.MarketError(
                f"Invalid legacy surveillance observation for {symbol} on {day}"
            )
        defaults = raw_context[symbol].get("surveillance", "unknown")
        current = values.get(symbol, {}).get("surveillance", defaults)
        if current != "unknown" and current != classification:
            raise market.MarketError(
                f"Conflicting surveillance classifications for {symbol} on {day}"
            )
        if current == "unknown":
            values.setdefault(symbol, {})["surveillance"] = classification
            supplemented += 1
    if not supplemented:
        return None
    folder = base / "legacy-control-view" / str(day)
    folder.mkdir(parents=True, exist_ok=True)
    for name, p in linked.items():
        pilot_data.checked_link(p, folder / name)
    pilot_data.write_json(folder / "symbols.json", values)
    pilot_data.write_json(
        folder / "sources.json",
        {
            "prior": previous,
            "files": files,
            "symbols_hash": market.sha((folder / "symbols.json").read_bytes()),
            "source_kind": "dated_surveillance_annexures",
            "legacy_manifest_sha256": record["sources_sha256"],
        },
    )
    return folder


def finalize_controls(root, result):
    from collections import defaultdict

    from . import exchange

    target = root / "baseline"
    view = target / "market"
    staged = target / "earlier-controls"
    coverage = staged / "coverage.json"
    days = list(map(date.fromisoformat, result["sessions"]))
    records, earlier_digest = control_coverage(coverage, days)
    legacy_records, legacy_digest = control_coverage(
        target / "legacy-surveillance" / "coverage.json", days
    )
    for stamp, record in records.items():
        certificate = staged / stamp / "sources.json"
        if (
            not certificate.is_file()
            or market.sha(certificate.read_bytes()) != record["sources_sha256"]
        ):
            raise market.MarketError(f"Earlier control archive differs from its receipt on {stamp}")
    verified_legacy_files = {}
    legacy_overlays = 0
    canonical = root / "sources" / "exchange-controls"
    if (
        pilot_data.snapshot_hash(
            view, days, control_root=canonical, include_earnings=False, include_static_bands=False
        )
        != result["snapshot_sha256"]
    ):
        raise market.MarketError(
            "Verified price, membership or context inputs changed before control audit"
        )
    from . import baseline_static_bands

    static_bands = baseline_static_bands.prepare(root, result["sessions"])
    if static_bands.get("complete") is not True:
        raise market.MarketError("Historical static opening band evidence is incomplete")
    directory = view / "sources" / "exchange-controls"
    if directory.is_symlink():
        if directory.resolve() != canonical.resolve():
            raise market.MarketError("Unexpected control archive in the baseline view")
        directory.unlink()  # detach the baseline's reference; retain all original directories
    directory.mkdir(exist_ok=True)
    counts = defaultdict(list)
    early_observations = 0
    for day in days:
        old = canonical / str(day)
        new = staged / str(day)
        if str(day) not in records and (new / "sources.json").exists():
            raise market.MarketError(f"Uncertified early control files on {day}")
        if (new / "sources.json").is_file():
            if (old / "sources.json").is_file() and (new / "sources.json").read_bytes() != (
                old / "sources.json"
            ).read_bytes():
                raise market.MarketError(f"Conflicting dated control sources on {day}")
            controls = new
            early_observations += 1
        else:
            controls = old
        if str(day) in legacy_records:
            historical = legacy_control_view(
                target, view, day, legacy_records[str(day)], verified_legacy_files, controls
            )
            if historical is not None:
                controls = historical
                legacy_overlays += 1
        link = directory / str(day)
        if (controls / "sources.json").is_file():
            pilot_data.checked_link(controls, link)
            manifest = json.loads((controls / "sources.json").read_bytes())
            previous = json.loads((view / "sessions" / str(day) / "manifest.json").read_bytes())[
                "prior_day"
            ]
            if manifest["prior"] != previous:
                raise market.MarketError(f"Opening price band is not from preceding session {day}")
            values = json.loads((controls / "symbols.json").read_bytes())
            for v in values.values():
                if v["surveillance"] not in (True, False, "unknown") or (
                    not manifest["files"].get("surveillance.csv")
                    and manifest.get("source_kind") != "dated_surveillance_annexures"
                    and v["surveillance"] != "unknown"
                ):
                    raise market.MarketError(f"Missing source for surveillance on {day}")
                if (v.get("floor") is None) != (v.get("ceiling") is None) or (
                    v.get("floor") is not None and not manifest["files"].get("security.csv.gz")
                ):
                    raise market.MarketError(f"Missing source for opening band on {day}")
        elif link.exists():
            raise market.MarketError(f"Unaccounted dated controls at {link}")
        data = exchange.read_context(view, day, verify_sources=True)
        for label, key in [
            ("surveillance", "surveillance"),
            ("opening_band", "floor"),
            ("earnings_calendar", "earnings_coverage"),
        ]:
            total = sum(
                c.get(key, "known" if key == "earnings_coverage" else None) in (None, "unknown")
                for c in data.values()
            )
            if total:
                counts[label].append({"day": str(day), "members": total})
    return {
        **result,
        "unknown_controls": counts,
        "static_opening_bands": static_bands,
        "earlier_controls": {
            "complete": True,
            "coverage_sha256": earlier_digest,
            "dated_earlier_manifests": early_observations,
        },
        "legacy_surveillance": {
            "complete": True,
            "coverage_sha256": legacy_digest,
            "verified_sessions": len(legacy_records),
            "dated_nonempty_overlays": legacy_overlays,
            "verified_source_files": len(verified_legacy_files),
        },
        "snapshot_sha256": pilot_data.snapshot_hash(view, days),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--finalize",
        action="store_true",
        help="Resume from the certified price and membership receipt",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    start = date(2014, 1, 1)
    dest = root / "baseline"
    dest.mkdir(exist_ok=True)
    check = dest / "preflight.json"
    try:
        core = dest / "prices-and-membership.json"
        if args.finalize:
            result = json.loads(core.read_bytes())
            if (
                not result["complete"]
                or result["start"] != str(start)
                or result["end"] != str(args.end)
            ):
                raise market.MarketError("Price and membership receipt covers a different interval")
        else:
            collect_index_review(root)
            result = pilot_data.prepare(root, start, args.end, full_history=True)
            pilot_data.write_json(core, result)
        from . import baseline_earnings

        result["earnings_by_isin"] = baseline_earnings.prepare(root, result["sessions"])
        result = finalize_controls(root, result)
        result["primary_archives"] = audit_primary_archives(root, result["sessions"])
        anchor_day = date.fromisoformat(str(result["membership"]["anchor"]))
        name = f"weights-{anchor_day:%b%Y}.zip"
        archive = root / "sources" / "reference" / name
        source = json.loads(archive.with_suffix(".zip.source.json").read_bytes())
        if market.sha(archive.read_bytes()) != source["sha256"]:
            raise market.MarketError(f"Corrupt official starting constituent archive {name}")
        result["membership"]["anchor_archive"] = source
        result["warmup_sources"] = audit_warmup(
            root, date.fromisoformat(str(result["warmup_calendar"]["start"])), start
        )
        result["canonical_membership"] = publish_membership(root, dest / "market")
    except Exception as exc:
        pilot_data.write_json(
            check, {"complete": False, "start": start, "end": args.end, "error": str(exc)}
        )
        raise
    pilot_data.write_json(check, result)
    print(f"Verified {len(result['sessions'])} official sessions in {check}", flush=True)


if __name__ == "__main__":
    main()
