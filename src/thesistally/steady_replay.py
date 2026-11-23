"""Run the two registered monthly books, or observe one new forward session."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import conventions, exchange, market, stage3_market, steady_actions
from .evaluation import walk_windows
from .historical_sessions import context_for, reference_rows
from .pilot_data import write_json
from .signals import rules
from .steady_metrics import measure
from .steady_momentum import (
    REGISTRY,
    SETTINGS,
    UnpricedHolding,
    advance,
    last_state,
    open_journal,
    seed,
)
from .trading_calendar import holding_horizon

VARIANTS = list(SETTINGS["lookbacks"])
LOOKBACKS = tuple(SETTINGS["lookbacks"].values())


def execution_config():
    config = rules(revision=SETTINGS["base_revision"])
    if config["config_hash"] != SETTINGS["base_sha256"]:
        raise market.MarketError(
            "Monthly strategy execution configuration differs from preregistration"
        )
    return {**config, "entry_band_convention": conventions.ENTRY_BAND_VERSION}


def is_month_end(root, day):
    delta = holding_horizon(root, day, 1)
    if delta is None:
        raise market.MarketError(f"Missing published exchange calendar for A rebalance on {day}")
    return (day + timedelta(days=delta)).month != day.month


def _refs(root, day, members):
    """Get entitlements and prior-opening controls on securities no longer in the universe."""
    from .baseline_actions import apply_to_following

    live = exchange.context_file(root, day).parent
    if str(day) >= conventions.FORWARD_EFFECTIVE and (live / "actions.json").is_file():
        # The daily announcement archive covers the exchange; re-evaluate it for a
        # held former constituent. Checked by opening_data's verify_sources call.
        master = live / "security.csv.gz"
        flags = live / "surveillance.csv"
        rows = exchange.decode(
            day,
            members,
            flags.read_bytes() if flags.exists() else None,
            master.read_bytes() if master.exists() else None,
            json.loads((live / "meetings.json").read_bytes()),
            json.loads((live / "actions.json").read_bytes()),
        )
        return {symbol: exchange.settle_notices(info) for symbol, info in rows.items()}

    events, meetings, availability, _ = reference_rows(root)
    raw = context_for(day, members, meetings, events, availability, set())
    for row in members.itertuples():
        raw[row.symbol] = exchange.settle_notices(raw[row.symbol])
        apply_to_following(root, day, {"isin": row.isin, "symbol": row.symbol}, raw[row.symbol])
    for directory in (root / "context" / str(day), root / "sources/exchange-controls" / str(day)):
        source = directory / "security.csv.gz"
        if source.is_file():
            controls = exchange.decode_controls(members, None, source.read_bytes())
            for sym, record in controls.items():
                raw[sym].update(record)
            break
    return raw


def opening_data(root, day, wanted):
    if not wanted:
        return {}
    folder = root / "sessions" / str(day)
    context = exchange.read_context(root, day, verify_sources=True)
    dated_members = market.read_members((folder / "members.csv").read_bytes())
    members = dict(zip(dated_members["isin"], dated_members.symbol))
    flags = exchange.price_quarantines(root, day)
    releases = conventions.dated(root, day)
    rows = {}
    with (folder / "bhavcopy.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            isin = r["ISIN"].strip()
            series = r["SctySrs"].strip()
            symbol = r["TckrSymb"].strip()
            if isin not in wanted or series not in ("EQ", "BE", "BZ"):
                continue
            vol = int(float(r["TtlTradgVol"]))
            if isin in rows:
                if not vol:
                    continue
                if rows[isin]["volume"] > 0:
                    raise market.MarketError(f"Multiple traded share quotes on {day} {isin}")
            rows[isin] = {
                "isin": isin,
                "symbol": symbol,
                "series": series,
                "open": float(r["OpnPric"] or 0),
                "close": float(r["ClsPric"] or 0),
                "volume": vol,
            }
    off = pd.DataFrame([r for s, r in rows.items() if s not in members])
    additional = {} if off.empty else _refs(root, day, off)
    for isin, r in rows.items():
        if isin in members:
            if members[isin] != r["symbol"]:
                raise market.MarketError(
                    f"Equity identity differs from dated membership on {day} {isin}"
                )
            info = context[r["symbol"]].copy()
        else:
            info = additional[r["symbol"]].copy()
        exchange.apply_quarantine(info, flags, isin)
        conventions.apply(info, releases.get(isin, {}))
        r.update(conventions.cash_dividend_notices(info))
    return rows


def needed(accounts, day, terms):
    ids = set()
    for a in accounts:
        ids.update(a.positions)
        ids.update(a.orders)
        ids.update(b["isin"] for b in a.bonuses)
        for isin in a.positions:
            term = terms.get(isin)
            if term and day >= term["suspended"] and term.get("successor_isin"):
                ids.add(term["successor_isin"])
    return ids


def step(root, db, key, days, prior, definition):
    close_verified = (root / "sources/independent-closes.json").is_file()
    terms = (
        steady_actions.terms(root, revised=True, close_verified=True)
        if close_verified
        else steady_actions.terms(root, revised=True)
    )
    cfg = execution_config()
    if close_verified:
        cfg["allow_short_dividend"] = True
    previous = prior
    if not days:
        return {}
    initial_gate, initial_rank = stage3_market.session(
        root, prior, LOOKBACKS if is_month_end(root, prior) else ()
    )
    ids = {lb: f"A{lb}-{key}" for lb in LOOKBACKS}
    for lb, run in ids.items():
        seed(db, run, prior, initial_rank.get(lb), initial_gate, {**definition, "lookback": lb})
    blocked = {}
    for day in days:
        _advance_day(root, db, day, previous, ids, terms, cfg, blocked)
        previous = day
    return blocked


def _advance_day(root, db, day, prior, ids, terms, cfg, blocked):
    books = {}
    for lb, run in ids.items():
        if run in blocked:
            continue
        latest, state = last_state(db, run)
        if str(day) <= latest:
            continue
        books[lb] = state
    if not books:
        return
    month_end = is_month_end(root, day)
    gate, rank = stage3_market.session(root, day, tuple(books) if month_end else ())
    quotes = opening_data(root, day, needed(books.values(), str(day), terms))
    if cfg.get("allow_short_dividend"):
        quotes = {
            k: conventions.cash_dividend_notices(v, allow_short_dividend=True)
            for k, v in quotes.items()
        }
    for lb in books:
        try:
            advance(db, ids[lb], day, prior, rank.get(lb), gate, quotes, cfg, terms)
        except UnpricedHolding as exc:
            blocked[ids[lb]] = exc.info
            print(ids[lb], "valuation gap", exc.info, flush=True)


def read_benchmark(path):
    with path.open(newline="") as fp:
        return {r["day"]: float(r["close"]) for r in csv.DictReader(fp)}


def source_hash():
    names = [
        "steady_replay.py",
        "steady_metrics.py",
        "steady_actions.py",
        "revisions/stage3-holder-actions.json",
        "revisions/stage3-entry-holder-actions.json",
        "steady_momentum.py",
        "stage3_market.py",
        "conventions.py",
        "journal.py",
        "exchange.py",
        "corporate.py",
        "trading_calendar.py",
        "signals.py",
        "historical_sessions.py",
        "baseline_actions.py",
        "baseline_static_bands.py",
        "market.py",
        "convention_market.py",
    ]
    h = hashlib.sha256()
    for n in names:
        h.update(n.encode() + b"\0" + (Path(__file__).parent / n).read_bytes())
    return h.hexdigest()


def replay(root, benchmark, out):
    from .convention_market import certify

    receipt = certify(root.parent.parent)
    root = root.resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(Path(__file__).parent), "rev-parse", "HEAD"], text=True
    ).strip()
    prereg = Path(__file__).resolve().parents[2] / "docs/PREREGISTRATION.md"
    proof = {
        "entry_band_convention": conventions.ENTRY_BAND_VERSION,
        "entry_revision_sha256": market.sha(
            prereg.with_name("STAGE3_ENTRY_REVISION.md").read_bytes()
        ),
        "preregistration_sha256": market.sha(prereg.read_bytes()),
        "market_snapshot_sha256": receipt["snapshot_sha256"],
        "code_sha256": source_hash(),
        "benchmark_sha256": market.sha(benchmark.read_bytes()),
        "shareholder_evidence_sha256": market.sha(steady_actions.REVISED_HOLDERS.read_bytes()),
        "cost_config_hash": execution_config()["config_hash"],
        "strategy_config_sha256": market.sha(REGISTRY.read_bytes()),
    }
    if receipt.get("close_convention"):
        proof.update(
            close_convention=receipt["close_convention"],
            close_evidence_sha256=receipt["independent_close_input_sha256"],
            supplemental_holder_sha256=market.sha(steady_actions.CLOSE_HOLDERS.read_bytes()),
        )
    fingerprint = market.sha(json.dumps(proof, sort_keys=True).encode())
    folder = out / fingerprint[:20]
    folder.mkdir(parents=True, exist_ok=True)
    audited = json.loads((root.parent.parent / "baseline/preflight.json").read_bytes())["sessions"]
    dates = list(map(date.fromisoformat, audited))
    schedule = walk_windows(dates, 3, 1, None)
    comparators = read_benchmark(benchmark)
    gaps = {}
    journals = folder / "journal.sqlite"
    with closing(open_journal(journals)) as db:
        for name, test, _ in schedule:
            prior = dates[dates.index(test[0]) - 1]
            gaps.update(step(root, db, name, test, prior, proof))
            print(
                name,
                test[0],
                test[-1],
                {v: last_state(db, f"{v}-{name}")[0] for v in VARIANTS},
                flush=True,
            )
        results = measure(db, VARIANTS, schedule, comparators, gaps)
    if proof["code_sha256"] != source_hash():
        raise market.MarketError("Monthly account code changed during the historical run")
    if (
        market.sha(benchmark.read_bytes()) != proof["benchmark_sha256"]
        or certify(root.parent.parent)["snapshot_sha256"] != receipt["snapshot_sha256"]
    ):
        raise market.MarketError("Monthly account inputs changed during the historical run")
    output = {
        "study": "stage3-steady-momentum",
        "identity": fingerprint,
        "proof": proof,
        "launch_git_commit": commit,
        "journal_sha256": market.sha(journals.read_bytes()),
        "journal": "journal.sqlite",
        "benchmark": json.loads(benchmark.with_suffix(".json").read_bytes())
        if benchmark.with_suffix(".json").is_file()
        else None,
        "variants": results,
        "funded_valuation_gaps": gaps,
        "methods": {
            "turnover": "One half of absolute traded opening notional divided by preceding marked equity.",
            "gross": "Same fills and quantities. Restore their charged fees and adverse execution slippage in that day's return.",
            "gate": "Gate at the prior close for the arrival of each marked return. Gate subsets use trading-session annualization.",
            "invested_sessions": "Whole-account net close-to-close returns on sessions with stock exposure at the preceding close or during the session, including opening purchases and final sales. Benchmark uses the same pairs of dated closes. No rescaling to full exposure.",
            "planned_entries": "Each opening purchase or top-up sized above zero, or a planned new name unable to buy a whole share, counted once. A missing opening quote uses the preceding holding mark to classify a retained underweight position. Only missing executable data enters the coverage numerator. Out-of-window pending instructions are disclosed separately.",
            "verdict": "An unresolved valuation makes the overall verdict inconclusive. Compare matching covered windows only. Coverage requires a complete valuation path and at most 10% of due purchases blocked by missing data. Require at least two thirds of included windows to beat TRI, higher linked CAGR, and drawdown no larger than 1.2 times TRI.",
        },
    }
    write_json(folder / "results.json", output)
    return folder / "results.json"


def forward(root, day, out):
    """Explicit research-only paper observation. Reuse the same transaction as every replay session."""
    dates = market.sessions(root)
    if day not in dates or dates.index(day) == 0:
        raise market.MarketError("Forward observation needs the preceding exchange session")
    prior = dates[dates.index(day) - 1]
    path = out / "forward" / "journal.sqlite"
    definition = {
        "mode": "forward",
        "entry_band_convention": conventions.ENTRY_BAND_VERSION,
        "code_sha256": source_hash(),
        "shareholder_evidence_sha256": market.sha(steady_actions.REVISED_HOLDERS.read_bytes()),
        "cost_config_hash": execution_config()["config_hash"],
        "strategy_config_sha256": market.sha(REGISTRY.read_bytes()),
    }
    with closing(open_journal(path)) as db:
        old = db.execute("SELECT seed,definition FROM books LIMIT 1").fetchone()
        if old is None:
            gaps = step(root, db, "forward", [day], prior, definition)
        else:
            ids = {lb: f"A{lb}-forward" for lb in LOOKBACKS}
            for lb, run in ids.items():
                s = db.execute("SELECT definition FROM books WHERE run=?", (run,)).fetchone()
                if not s or json.loads(s[0]) != {**definition, "lookback": lb}:
                    raise market.MarketError(
                        "Forward portfolio convention or code revision changed"
                    )
            gaps = {}
            _advance_day(
                root,
                db,
                day,
                prior,
                ids,
                steady_actions.terms(root, revised=True),
                execution_config(),
                gaps,
            )
    if gaps:
        raise market.MarketError(f"Forward portfolio valuation requires evidence: {gaps}")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", type=Path, default=Path("data/stage3/market"))
    parser.add_argument("--benchmark", type=Path, default=Path("data/stage3/benchmark.csv"))
    parser.add_argument(
        "--output", type=Path, default=Path("data/stage3/entry-band-v2/steady-momentum")
    )
    parser.add_argument("--forward-session", type=date.fromisoformat)
    args = parser.parse_args()
    print(
        forward(args.market, args.forward_session, args.output)
        if args.forward_session
        else replay(args.market, args.benchmark, args.output)
    )


if __name__ == "__main__":
    main()
