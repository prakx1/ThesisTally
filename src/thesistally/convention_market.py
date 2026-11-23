"""Derive a separate dated market view from the certified Stage 2 archive."""

import csv
import gzip
import io
import json
import math
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import httpx
import pandas as pd

from . import baseline_actions, baseline_data, baseline_static_bands, history, market
from .conventions import VERSION
from .historical_sessions import FIELDS, bridge_delayed_split_identifiers, reference_rows
from .pilot_data import checked_link, write_json


def final_bar(data, d, isin):
    archive = data / "sources/nse-daily" / str(d)
    if not archive.exists():
        archive = data / "baseline/evidence/sources/nse-daily" / str(d)
    meta = json.loads((archive / "sources.json").read_bytes())["bhavcopy.zip"]
    raw = (archive / "bhavcopy.zip").read_bytes()
    if market.sha(raw) != meta["sha256"]:
        raise market.MarketError(f"Changed official final {d}")
    frame = history.normalize_bhav(history.extract_bhav(raw), d)
    rows = frame[frame.ISIN.eq(isin) & frame.SctySrs.str.strip().isin(["EQ", "BE", "BZ"])]
    if len(rows) != 1:
        return None, meta
    row = rows.iloc[0]
    return row, meta


def master(folder, d, isin, symbol, series):
    path = folder / "security.csv.gz"
    if not path.exists():
        return None
    if (folder / "sources.json").is_file():
        meta = json.loads((folder / "sources.json").read_bytes())["files"]["security.csv.gz"]
    else:
        meta = json.loads((folder / "context.json").read_bytes())["sources"]["security.csv.gz"]
    raw = path.read_bytes()
    if market.sha(raw) != meta["sha256"] or f"_{d:%d%m%Y}." not in meta["url"]:
        raise market.MarketError(f"Wrong dated opening band {folder}")
    for r in csv.DictReader(io.StringIO(gzip.decompress(raw).decode("utf-8-sig"))):
        if (r["ISIN"], r["TckrSymb"], r["SctySrs"].strip()) == (isin, symbol, series):
            match = re.fullmatch(r"([\d.]+)-([\d.]+)", r["PricRg"])
            if match:
                return {"floor": float(match[1]), "ceiling": float(match[2]), **meta}
    return None


def flag_release(data, src, auditdir, issue, prior, following):
    d, isin, symbol = date.fromisoformat(issue["day"]), issue["isin"], issue["symbol"]
    if not following:
        return None
    before = src / "sources/exchange-controls" / str(d)
    after = src / "sources/exchange-controls" / str(following)
    if not (before / "security.csv.gz").is_file():
        before = src / "context" / str(d)
    if not (after / "security.csv.gz").is_file():
        after = src / "context" / str(following)
    if not (before / "security.csv.gz").exists() or not (after / "security.csv.gz").exists():
        return None
    row, source = final_bar(data, d, isin)
    if row is None or not float(row.TtlTradgVol):
        return None
    series = row.SctySrs.strip()
    opening = master(before, prior, isin, symbol, series)
    end = master(after, d, isin, symbol, series)
    return verified_close(issue, prior, row, source, opening, end, auditdir)


def verified_close(issue, prior, row, source, opening, end, auditdir):
    d, isin, symbol = date.fromisoformat(issue["day"]), issue["isin"], issue["symbol"]
    if not opening or not end or not float(row.TtlTradgVol):
        return None
    close, op, low, high = map(float, (row.ClsPric, row.OpnPric, row.LwPric, row.HghPric))
    if not (
        str(row.TckrSymb) == symbol
        and close == issue["close"]
        and float(row.PrvsClsgPric) == issue["prior_close"]
        and low <= min(close, op) <= max(close, op) <= high
        and opening["floor"] <= op <= opening["ceiling"]
    ):
        return None
    if opening["floor"] <= low and high <= opening["ceiling"]:
        method = "entire_session_within_published_range"
        label = None
    else:
        # A flexed cash equity can trade beyond the opening range. Independently
        # verify that the exchange classified it dynamic and reset its EOD master
        # to the raw final close. Never substitute the next day's observed prices.
        name = f"sec_list_{prior:%d%m%Y}.csv"
        label = {"url": f"https://nsearchives.nseindia.in/content/equities/{name}"}
        p = auditdir / name
        if not p.exists():
            response = httpx.get(label["url"], timeout=30, follow_redirects=True)
            if response.status_code == 404 or not response.content.strip():
                return None
            response.raise_for_status()
            with market.staging(auditdir) as tmp:
                (tmp / name).write_bytes(response.content)
                (tmp / name).replace(p)
        raw = p.read_bytes()
        if baseline_static_bands.daily_labels(raw).get(symbol) != "No Band":
            return None
        label.update(sha256=market.sha(raw), classification="No Band")
        if abs(end["floor"] - close * 0.90) > 0.11 or abs(end["ceiling"] - close * 1.10) > 0.11:
            return None
        method = "published_dynamic_class_and_same_date_eod_band_reference"
    return {
        "day": str(d),
        "isin": isin,
        "symbol": symbol,
        "open": op,
        "close": close,
        "prior_close": issue["prior_close"],
        "ohlc": [op, high, low, close],
        "source": source,
        "opening_band": opening,
        "eod_master": end,
        "dynamic_list": label,
        "method": method,
        "convention_version": VERSION,
    }


def rights_terms(subject, face):
    text = subject.strip()
    if not re.match(r"^rights\b", text, re.IGNORECASE):
        return []
    legs = []
    for leg in re.split(r"\s+/\s+(?=\d+\s*:)", text):
        ratio = re.search(r"(\d+)\s*:\s*(\d+)", leg)
        if not ratio or min(int(ratio[1]), int(ratio[2])) <= 0:
            return []
        amount = r"(?:rs\.?|re\.?|₹)\s*([\d,]+(?:\.\d+)?)"
        premium = re.search(r"(?:premium|prem)\s*(?:of\s+)?" + amount, leg, re.IGNORECASE)
        full = re.search(r"(?:issue\s+price(?:\s+of)?|at|@)\s*" + amount, leg, re.IGNORECASE)
        if full:
            price = float(full[1].replace(",", ""))
        elif premium and face not in (None, "", "-"):
            price = float(premium[1].replace(",", "")) + float(face)
        elif re.search(r"\bat\s+par\b", leg, re.IGNORECASE) and face not in (None, "", "-"):
            price = float(face)
        else:
            return []
        legs.append({"new": int(ratio[1]), "old": int(ratio[2]), "price": price})
    return legs


def rights_issue(day, isin, subject, face, prior_close, source):
    legs = rights_terms(subject, face)
    if not legs or not math.isfinite(prior_close) or prior_close <= 0:
        return None
    offered = sum(leg["new"] / leg["old"] for leg in legs)
    payment = sum(leg["price"] * leg["new"] / leg["old"] for leg in legs)
    terp = (prior_close + payment) / (1 + offered)
    return {
        "day": str(day),
        "isin": isin,
        "subjects": [subject],
        "terms": legs,
        "source_url": source["url"],
        "sha256": source["sha256"],
        "prior_close": float(prior_close),
        "terp": float(terp),
        "factor": float(min(1.0, terp / prior_close)),
    }


def corrected_tape(data, src, view, days):
    tape = pd.concat(
        [pd.read_parquet(p) for p in sorted((data / "sources/adjusted").glob("*.parquet"))],
        ignore_index=True,
    )
    bridge_delayed_split_identifiers(tape)
    actions = dict(reference_rows(data)[0])
    ledger = json.loads((src / "sources/adjusted/quarantine.json").read_bytes())
    baseline_actions.restore_action_aliases(data, tape, view, ledger, actions)
    baseline_data.restore_dated_splits(tape, view, ledger, actions)
    members = pd.read_parquet(data / "universes/reconstructed.parquet")
    baseline_data.index_identifier_history(tape, members, actions, days)
    return tape, members


def prepare(data, *, close_evidence=None):
    if close_evidence is not None and (
        data.parent.name != "views" or data.name != market.sha(close_evidence.read_bytes())[:20]
    ):
        raise market.MarketError("New close evidence requires its own fingerprinted archive")
    src = data / "baseline/market"
    base = data / "stage3"
    view = base / "market"
    proof = json.loads((data / "baseline/preflight.json").read_bytes())
    from .baseline_replay import certify_inputs

    days, _ = certify_inputs(src, data / "baseline/preflight.json")
    days = list(days)
    position = {d: i for i, d in enumerate(days)}
    (view / "sources/adjusted").mkdir(parents=True, exist_ok=True)
    for p in src.iterdir():
        if p.name not in ("sessions", "sources"):
            checked_link(p, view / p.name)
    for p in (src / "sources").iterdir():
        if p.name != "adjusted":
            checked_link(p, view / "sources" / p.name)
    for p in (src / "sources/adjusted").glob("*.parquet"):
        checked_link(p, view / "sources/adjusted" / p.name)
    (base / "evidence").mkdir(exist_ok=True)
    checked_link(data / "baseline/evidence/splits", base / "evidence/splits")
    tape, members = corrected_tape(data, src, view, days)
    ledger = json.loads((src / "sources/adjusted/quarantine.json").read_bytes())
    verified = {}
    evidence_hash = None
    if close_evidence is not None:
        from .close_study import accepted_closes

        evidence_hash = market.sha(close_evidence.read_bytes())
        verified = accepted_closes(close_evidence, ledger)
        write_json(
            view / "sources/independent-closes.json", json.loads(close_evidence.read_bytes())
        )
    events = defaultdict(dict)
    changes = defaultdict(set)
    for issue in ledger:
        d = date.fromisoformat(issue["day"])
        isin = issue.get("isin")
        if (
            issue["reason"] != "unexplained_move_over_20pct"
            or d not in position
            or not market.is_strict_move(issue["close"], issue["prior_close"])
        ):
            continue
        i = position[d]
        release = flag_release(
            data,
            src,
            base / "evidence",
            issue,
            days[i - 1],
            days[i + 1] if i + 1 < len(days) else None,
        )
        if independent := verified.get((str(d), isin)):
            bar, primary = final_bar(data, d, isin)
            if bar is None or (
                bar.TckrSymb != issue["symbol"]
                or float(bar.ClsPric) != issue["close"]
                or float(bar.PrvsClsgPric) != issue["prior_close"]
                or float(bar.TtlTradgVol) <= 0
            ):
                raise market.MarketError(f"Independent release conflicts with final {d} {isin}")
            release = {
                "method": "corroborated_close_no_ex_action",
                "evidence_sha256": evidence_hash,
                "day": str(d),
                "isin": isin,
                "symbol": issue["symbol"],
                "close": independent["close"],
                "prior_close": independent["prior_close"],
                "source": primary,
                "convention_version": "independent-close-v1",
            }
        if release:
            events[str(d)][isin] = {"symbol": issue["symbol"], "quarantine_release": release}
            changes[isin].add(d)
            tape.loc[tape.day.eq(d) & tape["isin"].eq(isin), "unexplained_jump"] = False
    rights_adjustments = []
    unresolved = []
    traded = set(members["isin"])
    for p in sorted((data / "sources/reference").glob("actions-????-??-??_????-??-??.json")):
        raw = p.read_bytes()
        meta = json.loads(p.with_suffix(".json.source.json").read_bytes())
        if market.sha(raw) != meta["sha256"]:
            raise market.MarketError(f"Changed rights source {p}")
        for r in json.loads(raw):
            sub = r.get("subject", "")
            isin = r.get("isin")
            sym = r.get("symbol")
            if (
                "rights" not in sub.lower()
                or r.get("series") != "EQ"
                or r.get("exDate") in (None, "-")
            ):
                continue
            d = datetime.strptime(r["exDate"], "%d-%b-%Y").date()
            if d not in position:
                continue
            group = tape[tape.day.eq(d) & (tape["isin"].eq(isin) | tape.symbol.eq(sym))]
            if len(group) != 1 or group.iloc[0]["isin"] not in traded:
                continue
            now = group.iloc[0]
            isin = now["isin"]
            sym = now.symbol
            earlier = tape[tape["isin"].eq(isin) & tape.day.lt(d)].sort_values("day")
            old = earlier.iloc[-1] if len(earlier) else None
            parsed = (
                rights_issue(d, isin, sub, r.get("faceVal"), old.close, meta)
                if old is not None
                else None
            )
            if not parsed:
                unresolved.append([str(d), sym, sub])
                continue
            terp, factor = parsed["terp"], parsed["factor"]
            item = events[str(d)].setdefault(isin, {"symbol": sym})
            if "rights_issue" in item:
                raise market.MarketError(f"Duplicate rights issue {d} {sym}")
            item["rights_issue"] = parsed
            observed = now.price_factor / old.price_factor
            if old.chain != now.chain or not math.isclose(observed, factor, rel_tol=1e-10):
                tail = tape.chain.eq(now.chain) & tape.day.ge(d)
                tape.loc[tail, "chain"] = old.chain
                tape.loc[tail, "price_factor"] *= factor / observed
                if old.share_factor != now.share_factor:
                    tape.loc[tail, "share_factor"] *= old.share_factor / now.share_factor
                    tape.loc[tail, "adjusted_volume"] = (
                        tape.loc[tail, "volume"] / tape.loc[tail, "share_factor"]
                    )
                for field in market.PRICES:
                    tape.loc[tail, "adjusted_" + field] = (
                        tape.loc[tail, field] / tape.loc[tail, "price_factor"]
                    )
                changes[isin].add(d)
                rights_adjustments.append([str(d), sym])
            residual = market.is_strict_move(now.close, terp)
            if not residual:
                tape.loc[now.name, "unexplained_jump"] = False

    def keep(row):
        rec = events.get(row["day"], {}).get(row.get("isin"), {})
        if row["reason"] == "unexplained_move_over_20pct" and rec.get("quarantine_release"):
            return False
        return not (
            row["reason"] == "unresolved_action"
            and rec.get("rights_issue")
            and row.get("subject", "").strip()
            in {s.strip() for s in rec["rights_issue"]["subjects"]}
        )

    write_json(view / "sources/adjusted/quarantine.json", [r for r in ledger if keep(r)])
    write_json(
        view / "sources/stage3-events.json",
        {"version": VERSION, "days": dict(events), "unresolved_rights": unresolved},
    )
    # Restore complete, ex-date-rebased lookbacks only on dates affected by a lifted cut.
    # The baseline original never acquires links or files from this view.
    affected = defaultdict(set)
    for isin, dates in changes.items():
        for d in dates:
            chain = tape.loc[tape["isin"].eq(isin) & tape.day.eq(d), "chain"].iloc[0]
            for day in days[position[d] : position[d] + 400]:
                affected[day].add(chain)
    member_days = dict(tuple(members.groupby("day")))
    chains = {
        key: g.set_index("day").sort_index()
        for key, g in tape[
            tape.chain.isin(set().union(*affected.values()) if affected else set())
        ].groupby("chain")
    }
    stamp = []
    for d in days:
        orig = src / "sessions" / str(d)
        dest = view / "sessions" / str(d)
        group = member_days[d]
        names = dict(zip(group["isin"], group.symbol))
        frame = None
        manifest = json.loads((orig / "manifest.json").read_bytes())
        for chain in affected[d]:
            history_frame = chains[chain]
            if d not in history_frame.index:
                continue
            current = history_frame.loc[d]
            sym = names.get(current["isin"])
            if not sym:
                continue
            subset = history_frame.loc[date.fromisoformat(manifest["requested_start"]) : d]
            cut = (
                subset[subset.unexplained_jump].index.max()
                if subset.unexplained_jump.any()
                else None
            )
            if cut is not None:
                subset = subset[(subset.index > cut) | (subset.index == d)]
            restored = pd.DataFrame({"day": subset.index, "symbol": sym})
            for field in FIELDS:
                scale = "share_factor" if field == "volume" else "price_factor"
                restored[field] = subset["adjusted_" + field].to_numpy() * current[scale]
            if frame is None:
                frame = pd.read_parquet(orig / "bars.parquet")
            frame = pd.concat([frame[frame.symbol.ne(sym)], restored], ignore_index=True)
        if frame is None:
            checked_link(orig, dest)
        else:
            if dest.is_symlink():
                # Remove only our derived link before any write, never its archived target.
                if dest.resolve() != orig.resolve():
                    raise market.MarketError(f"Unexpected parent session link {dest}")
                dest.unlink()
            dest.mkdir(parents=True, exist_ok=True)
            for name in ["bhavcopy.csv", "indices.csv", "members.csv"]:
                checked_link(orig / name, dest / name)
            with market.staging(dest) as tmp:
                frame.sort_values(["day", "symbol"]).to_parquet(tmp / "bars.parquet", index=False)
                (tmp / "bars.parquet").replace(dest / "bars.parquet")
            manifest["sha256"]["bars.parquet"] = market.sha((dest / "bars.parquet").read_bytes())
            manifest["candles"] = len(frame)
            manifest["convention_version"] = VERSION
            write_json(dest / "manifest.json", manifest)
        stamp.append(market.sha((dest / "manifest.json").read_bytes()))
    stamp.extend(
        [
            market.sha((view / "sources/stage3-events.json").read_bytes()),
            market.sha((view / "sources/adjusted/quarantine.json").read_bytes()),
            proof["snapshot_sha256"],
        ]
    )
    release_count = sum("quarantine_release" in i for row in events.values() for i in row.values())
    result = {
        "complete": True,
        "convention_version": VERSION,
        "parent_snapshot_sha256": proof["snapshot_sha256"],
        "snapshot_sha256": market.sha(json.dumps(stamp).encode()),
        "sessions": len(days),
        "releases": release_count,
        "rights_issues": sum("rights_issue" in i for row in events.values() for i in row.values()),
        "unresolved_rights": unresolved,
        "rights_price_repairs": rights_adjustments,
        "session_manifests": dict(zip(map(str, days), stamp[: len(days)])),
        "event_registry_sha256": stamp[-3],
        "quarantine_sha256": stamp[-2],
    }
    if evidence_hash:
        result.update(
            close_convention="independent-close-v1",
            independent_close_count=len(verified),
            independent_close_input_sha256=evidence_hash,
            independent_close_receipt_sha256=market.sha(
                (view / "sources/independent-closes.json").read_bytes()
            ),
        )
        result["snapshot_sha256"] = market.sha(
            json.dumps([*stamp, evidence_hash, result["independent_close_receipt_sha256"]]).encode()
        )
    write_json(base / "preflight.json", result)
    print({k: v for k, v in result.items() if k != "session_manifests"}, flush=True)


def certify(data_root):
    """Verify both the immutable parent and all changed daily data before simulation."""
    from .baseline_replay import certify_inputs

    data_root = Path(data_root)
    base = data_root / "stage3"
    src = data_root / "baseline/market"
    view = base / "market"
    days, parent = certify_inputs(src, data_root / "baseline/preflight.json")
    receipt = json.loads((base / "preflight.json").read_bytes())
    if (
        not receipt["complete"]
        or receipt["convention_version"] != VERSION
        or receipt["parent_snapshot_sha256"] != parent["snapshot_sha256"]
    ):
        raise market.MarketError("Invalid parent snapshot or Stage 3 convention")
    hashes = []
    aliases = Path("sources/adjusted/action-aliases.json")
    if market.sha((view / aliases).read_bytes()) != market.sha((src / aliases).read_bytes()):
        raise market.MarketError(
            "Stage 3 corporate-action aliases differ from the certified source"
        )
    for folder in [src, src / "sources"]:
        for original in folder.iterdir():
            if (
                original.name not in ("sessions", "sources", "adjusted")
                and (view / original.relative_to(src)).resolve() != original.resolve()
            ):
                raise market.MarketError(f"Stage 3 archive link differs from {original}")
    for day in days:
        folder = view / "sessions" / str(day)
        digest = market.sha((folder / "manifest.json").read_bytes())
        if digest != receipt["session_manifests"].get(str(day)):
            raise market.MarketError(f"Changed Stage 3 daily manifest {day}")
        if not folder.is_symlink():
            market.verify(view, day)
        elif folder.resolve() != (src / "sessions" / str(day)).resolve():
            raise market.MarketError(f"Unexpected base session {day}")
        hashes.append(digest)
    for name, key in [
        ("stage3-events.json", "event_registry_sha256"),
        ("adjusted/quarantine.json", "quarantine_sha256"),
    ]:
        actual = market.sha((view / "sources" / name).read_bytes())
        if actual != receipt[key]:
            raise market.MarketError(f"Changed convention input {name}")
        hashes.append(actual)
    hashes.append(parent["snapshot_sha256"])
    if "independent_close_receipt_sha256" in receipt:
        disk = market.sha((view / "sources/independent-closes.json").read_bytes())
        if disk != receipt["independent_close_receipt_sha256"]:
            raise market.MarketError("Changed independent closing price evidence")
        hashes.extend([receipt["independent_close_input_sha256"], disk])
    if market.sha(json.dumps(hashes).encode()) != receipt["snapshot_sha256"]:
        raise market.MarketError("Stage 3 market snapshot mismatch")
    return receipt


if __name__ == "__main__":
    prepare(Path("data").resolve())
