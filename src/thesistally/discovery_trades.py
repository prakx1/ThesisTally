"""Read the audited historical equal-risk journals on their original observation clocks."""

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd

from .baseline_metrics import read_calls
from .baseline_qualification import validation_config

# Published before discovery. Pullback refers to the original Stage 2 r2 baseline.
# B uses the subsequent official entry-band convention and its per-book data gate.
STUDIES = {
    "pullback": ("baseline/runs/e923bed5c9d9448d7340", "stage2-baseline-e923bed5c9d9448d7340"),
    "b10": ("stage3/runs/dip-e3687b17ddfbbdf17642", "stage3-dip-e3687b17ddfbbdf17642"),
    "b5": ("stage3/runs/dip-e3687b17ddfbbdf17642", "stage3-dip-e3687b17ddfbbdf17642"),
}
BOOKS = {
    "pullback": ("r2-equal-risk", "trend_pullback"),
    "b10": ("dip-rsi10-equal-risk", "dip_in_steady_risers"),
    "b5": ("dip-rsi5-equal-risk", "dip_in_steady_risers"),
}
PINS = Path(__file__).with_name("validation") / "discovery-historical-books.json"


def sha_file(path):
    with path.open("rb") as src:
        return hashlib.file_digest(src, "sha256").hexdigest()


def checked(path, expected):
    if sha_file(path) != expected:
        raise ValueError(f"Historical discovery source has changed: {path.name}")


def signal_levels(call, first_rebase, variant):
    """Restore the original close decision even if the next open changed the stored stop."""
    features = json.loads(call["features"])
    entry, stop = call["entry"], call["stop"]
    if first_rebase:
        if "original_features" in first_rebase:
            features = first_rebase["original_features"]
            entry, stop = first_rebase["original_entry"], first_rebase["original_stop"]
        else:
            features = json.loads(first_rebase["features"])
            entry, stop = first_rebase["entry"], first_rebase["stop"]
    if variant != "pullback":
        # The B journal replaces calls.stop with fill - 3*ATR at purchase. Its
        # detector stored the original closing plan in the features snapshot.
        stop = features["planned_stop"]
    return features, entry, stop


def signal_rank(variant, features):
    """Q1 is the strongest quintile on the archived signal ranking."""
    if variant == "pullback":
        rs = features.get("rs_percentile")
        if rs is None:
            return "unknown"
        if not 0 <= rs <= 1:
            raise ValueError("Invalid archived relative-strength percentile")
        return "Q" + str(6 - max(1, math.ceil(5 * rs)))
    rank = features.get("steady_rank")
    if rank is None:
        return "unknown"
    if not 1 <= rank <= 40 or int(rank) != rank:
        raise ValueError("Invalid archived top-40 rank")
    return "Q" + str(1 + (int(rank) - 1) // 8)


def signal_fields(call, original, study):
    regime = json.loads(call["regime"])
    feat, entry, stop = signal_levels(call, original, study)
    sector = call["sector"] or "unknown"
    if sector.strip().casefold() in ("unknown", "na", "n/a", "-"):
        sector = "unknown"
    atr = feat.get("atr14", feat.get("atr"))
    return {
        "stock": call["isin"],
        "isin": call["isin"],
        "symbol": call["symbol"],
        "sector": sector,
        "gate": "on" if regime["enabled"] else "off",
        "vix": regime["vix"],
        "breadth": regime["breadth"],
        "signal_close": entry,
        "planned_stop": stop,
        "stop_fraction": (entry - stop) / entry,
        "signal_rank_quintile": signal_rank(study, feat),
        "rank_score": feat.get("rs_percentile") if study == "pullback" else feat.get("steady_rank"),
        "atr_fraction": atr / entry if atr is not None else None,
        "median_turnover": feat.get("median_turnover"),
    }


def ledger_rows(path, study, window, expected_hash, complete, allowed, cfg_hash):
    run, name = BOOKS[study]
    checked(path, expected_hash)
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError(f"Historical journal is still being written: {path}")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        db.row_factory = sqlite3.Row
        cfg = json.loads(
            db.execute(
                "SELECT config FROM rule_configs JOIN runs ON rule_configs.revision=runs.rules "
                "WHERE runs.id=?",
                (run,),
            ).fetchone()[0]
        )
        if cfg["config_hash"] != cfg_hash:
            raise ValueError(f"Historical configuration mismatch: {run}")
        days = [r[0] for r in db.execute("SELECT day FROM scans WHERE run=? ORDER BY day", (run,))]
        if days != window["sessions"][: len(days)] or (complete and days != window["sessions"]):
            raise ValueError("Journal sessions disagree with the signed replay schedule")
        all_calls = read_calls(db, run, window["name"], days, cfg)
        source = {
            r["id"]: r
            for r in db.execute(
                "SELECT id,isin,symbol,sector,features,entry,stop,regime FROM calls WHERE run=? AND strategy=?",
                (run, name),
            )
        }
        settled = {}
        originals = {}
        for event in db.execute(
            "SELECT e.call_id,e.day,e.kind,e.detail FROM events e JOIN calls c ON e.call_id=c.id "
            "WHERE c.run=? AND c.strategy=? AND e.kind IN "
            "('closed','planned_levels_before_rights','corporate_action') ORDER BY e.id",
            (run, name),
        ):
            if event["kind"] == "closed":
                settled[event["call_id"]] = event["day"]
                continue
            leg = json.loads(event["detail"])
            if (
                event["kind"] == "planned_levels_before_rights"
                or leg.get("stage") == "bonus_pending_rebased"
            ):
                originals.setdefault(event["call_id"], leg)
    index = {day: i for i, day in enumerate(days)}
    output = []
    for trade in all_calls:
        if (
            trade["id"] not in source
            or trade["allocation"] != "unconstrained"
            or trade["status"] != "closed"
        ):
            continue
        cid, day = trade["id"], trade["day"]
        call = source[cid]
        end_index = index[day] + cfg["exits"]["max_days"]
        horizon_day = days[end_index] if end_index < len(days) else None
        if trade["mature"] != (horizon_day is not None) or settled[cid] < trade["buy_day"]:
            raise ValueError("Inconsistent historical settlement or maturity clock")
        output.append(
            {
                "strategy": study,
                "window": window["name"],
                "call_id": cid,
                "trade_id": f"{study}/{window['name']}/{cid}",
                "day": day,
                "year": int(day[:4]),
                "buy_day": trade["buy_day"],
                "exit_day": trade["exit_day"],
                "closed_day": settled[cid],
                "full_horizon_day": horizon_day,
                "mature": trade["mature"],
                "status": trade["status"],
                "net_r": trade["net_r"],
                "gross_r": trade["gross_r"],
                "net_inr": trade["net_inr"],
                **signal_fields(call, originals.get(cid), study),
                "coverage_eligible": allowed,
            }
        )
    return output


def build(source_data: Path, strategy=None):
    """Return closed calls and per-strategy window metadata without writing to source or destination."""
    root = Path(source_data).resolve()
    pins = json.loads(PINS.read_bytes())
    if strategy is not None and strategy not in BOOKS:
        raise ValueError(f"Unknown historical adapter {strategy}")
    all_rows, schedules, provenance = [], {}, {}
    floor = validation_config()["min_per_counted_window"]
    for key in BOOKS if strategy is None else (strategy,):
        run, name = BOOKS[key]
        directory, machine_report = STUDIES[key]
        folder = root / directory
        receipt, report_path = (
            folder / "replay.json",
            root / "backtests" / (machine_report + ".json"),
        )
        pin = pins[machine_report]
        checked(receipt, pin["replay_sha256"])
        checked(report_path, pin["report_sha256"])
        replay = json.loads(receipt.read_bytes())
        report = json.loads(report_path.read_bytes())
        if key == "pullback":
            metrics = report["revisions"]["r2"]["books"]["equal_risk"]["strategies"][name]
            hashes = report["journal_sha256"]
            cfg_hash = report["provenance"]["configs"]["r2"]["config_hash"]
        else:
            variant = run.removesuffix("-equal-risk")
            metrics = report["variants"][variant]["equal_risk"]
            hashes = pin["journal_sha256"]
            cfg_hash = report["proof"]["configs"][run]["sha256"]
        schedules[key] = {}
        rows = []
        counted_windows = []
        for window in replay["windows"]:
            win = window["name"]
            complete = window["books"][run]["complete"]
            allowed = (
                key == "pullback"
                or not metrics["coverage_by_window"][win]["entry_coverage"]["coverage_excluded"]
            )
            journal_path = folder / window["journal"]
            result = ledger_rows(
                journal_path, key, window, hashes[win], complete, allowed, cfg_hash
            )
            mature_count = sum(row["mature"] for row in result)
            counted = complete and allowed and mature_count >= floor
            if counted:
                counted_windows.append(win)
            for row in result:
                row["counted"] = bool(counted and row["mature"])
            rows.extend(result)
            source_coverage = (
                metrics["coverage_by_window"][win]["entry_coverage"] if key != "pullback" else {}
            )
            schedules[key][win] = {
                "start": window["test"][0],
                "end": window["test"][-1],
                "sessions": window["sessions"],
                "complete": complete,
                "coverage_eligible": allowed,
                "blocked_data": source_coverage.get("blocked_data"),
                "planned_entries": source_coverage.get("planned_entries"),
                "journal_sha256": hashes[win],
                "source_run": run,
                "source_study": directory,
                "source_config_sha256": cfg_hash,
                "source_report_sha256": pin["report_sha256"],
                "mature_count": mature_count,
            }
        q = metrics["qualification"]
        pool = [r for r in rows if r["counted"]]
        if (
            counted_windows != q["counted_windows"]
            or len(pool) != q["mature_closed_in_counted_windows"]
            or not math.isclose(
                sum(r["net_r"] for r in pool) / len(pool),
                q["criteria"]["mean_r"]["value"],
                abs_tol=1e-10,
            )
        ):
            raise ValueError(f"Discovery did not recover the published qualification sample: {key}")
        provenance[key] = {
            "machine_report": str(report_path.relative_to(root)),
            "machine_report_sha256": pin["report_sha256"],
            "replay_manifest": str(receipt.relative_to(root)),
            "replay_manifest_sha256": pin["replay_sha256"],
            "input_market_sha256": (
                replay["snapshot_sha256"]
                if key == "pullback"
                else report["proof"]["snapshot_sha256"]
            ),
            "rule_config_sha256": cfg_hash,
            "archived_journal_sha256": hashes,
            "original_closed": len(rows),
            "original_mature": sum(row["mature"] for row in rows),
            "original_counted": len(pool),
        }
        all_rows.extend(rows)
    frame = pd.DataFrame(all_rows)
    if frame.trade_id.duplicated().any() or not frame.net_r.map(math.isfinite).all():
        raise ValueError("Invalid historical discovery trade table")
    frame.attrs["provenance"] = provenance
    return frame, schedules


def build_plans(source_data: Path):
    """Reconcile each B plan disposition, including setups missing from the equal-risk calls."""
    from .dip_coverage import UNKNOWN_FILTERS, opening_disposition

    root = Path(source_data).resolve()
    directory, machine_report = STUDIES["b10"]
    folder = root / directory
    pins = json.loads(PINS.read_bytes())[machine_report]
    report_path = root / "backtests" / (machine_report + ".json")
    checked(report_path, pins["report_sha256"])
    checked(folder / "replay.json", pins["replay_sha256"])
    report = json.loads(report_path.read_bytes())
    windows = json.loads((folder / "replay.json").read_bytes())["windows"]
    output = []
    for w in windows:
        win = w["name"]
        journal = folder / w["journal"]
        checked(journal, pins["journal_sha256"][win])
        if (
            journal.with_name(journal.name + "-wal").exists()
            and journal.with_name(journal.name + "-wal").stat().st_size
        ):
            raise ValueError("Refusing an active historical journal")
        with closing(sqlite3.connect(journal.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            db.row_factory = sqlite3.Row
            for key in ("b10", "b5"):
                run, _ = BOOKS[key]
                variant = run.removesuffix("-equal-risk")
                funded = variant + "-portfolio"
                # The equal-risk book stores no calls for vetoed candidates. Its parallel
                # portfolio journal stored every signal, even when allocation was denied.
                # Read the closing snapshot there; all dispositions refer to the equal book.
                rows = db.execute(
                    """
                    SELECT d.day,d.isin,d.symbol,d.eligibility_reason,d.independent_veto,
                           eq.buy,eq.status,eq.allocation,p.id AS snapshot_id,p.sector,p.entry,p.stop,p.features,p.regime,
                           (SELECT detail FROM events e WHERE e.call_id=eq.id AND e.kind=eq.status
                            ORDER BY e.id DESC LIMIT 1) reason
                    FROM dip_setup_census d
                    LEFT JOIN calls eq ON (eq.run,eq.day,eq.isin)=(d.run,d.day,d.isin)
                    LEFT JOIN calls p ON p.run=? AND p.day=d.day AND p.isin=d.isin
                    WHERE d.run=? ORDER BY d.day,d.isin
                """,
                    (funded, run),
                )
                rebases = {}
                for r in db.execute(
                    """
                    SELECT e.call_id,e.kind,e.detail FROM events e JOIN calls c ON c.id=e.call_id
                    WHERE c.run=? AND e.kind IN ('planned_levels_before_rights','corporate_action')
                    ORDER BY e.id""",
                    (funded,),
                ):
                    details = json.loads(r["detail"])
                    if (
                        r["kind"] == "planned_levels_before_rights"
                        or details.get("stage") == "bonus_pending_rebased"
                    ):
                        rebases.setdefault(r["call_id"], details)
                group = []
                for r in rows:
                    if r["day"] == w["test"][-1]:
                        continue
                    veto = r["eligibility_reason"]
                    if veto in UNKNOWN_FILTERS and r["independent_veto"] is None:
                        blocked, disposition = True, veto
                    elif veto:
                        continue
                    elif r["allocation"] != "unconstrained":
                        raise ValueError("B plan census lacks an eligible equal-risk order.")
                    elif r["buy"] is not None:
                        blocked, disposition = False, "filled"
                    elif r["status"] == "confirmed":
                        continue
                    else:
                        category, disposition = opening_disposition(r["reason"])
                        blocked = category == "data"
                    if r["snapshot_id"] is None:
                        raise ValueError("B plan lacks the contemporaneous full-census snapshot.")
                    group.append(
                        {
                            "strategy": key,
                            "window": win,
                            "day": r["day"],
                            "year": int(r["day"][:4]),
                            "trade_id": f"plan/{key}/{win}/{r['day']}/{r['isin']}",
                            **signal_fields(r, rebases.get(r["snapshot_id"]), key),
                            "blocked_data": blocked,
                            "disposition": disposition,
                        }
                    )
                expected = report["variants"][variant]["equal_risk"]["coverage_by_window"][win][
                    "entry_coverage"
                ]
                if (len(group), sum(r["blocked_data"] for r in group)) != (
                    expected["planned_entries"],
                    expected["blocked_data"],
                ):
                    raise ValueError(
                        f"{key}/{win} subgroup inputs disagree with published coverage"
                    )
                output.extend(group)
    frame = pd.DataFrame(output)
    if frame.trade_id.duplicated().any():
        raise ValueError("Repeated independent B plan")
    return frame
