"""Collect official daily NSE prices, dated constituents, and corporate evidence for A126."""

import json
import shutil
from datetime import date, timedelta
from io import StringIO

import pandas as pd

from . import (
    constituents,
    conventions,
    exchange,
    forward_conventions,
    frozen_prices,
    historical_sessions,
    history,
    market,
)
from .daemon import holidays
from .pilot_data import write_json


def session_dates(root, previous, through):
    archived = set(market.sessions(root))
    day = previous + timedelta(days=1)
    while day <= through:
        if day in archived or holidays(root, day):
            yield day
        day += timedelta(days=1)


def market_view(root, cfg):
    """Seed a private prospective market from the certified final research session."""
    view = root / "forward" / cfg["tag"] / "market"
    anchor = cfg["anchor_session"]
    dated = root / "stage3/market/sessions" / anchor
    if market.sha((dated / "manifest.json").read_bytes()) != cfg["anchor_manifest_sha256"]:
        raise market.MarketError("Frozen starting exchange archive changed")
    links = {
        view / "sessions" / anchor: dated,
        view / "sources/nse-daily": root / "sources/nse-daily",
        view / "calendars": root / "calendars",
    }
    local_actions = root / "corporate_actions.json"
    if local_actions.exists():
        links[view / "corporate_actions.json"] = local_actions
    for target, source in links.items():
        if not target.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix != ".json":
                source.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source.resolve(), target_is_directory=source.is_dir())
        if target.resolve() != source.resolve():
            raise market.MarketError(f"Conflicting forward market source {target.name}")
    return view


def dated_members(root, view, day, prior, feed):
    src = root / "sessions" / str(day) / "members.csv"
    if src.exists():
        market.verify(root, day)
        raw = src.read_bytes()
        source = f"verified dated archive {day}; SHA-256 {market.sha(raw)}"
    elif day == market.today():
        response = feed.get(market.UNIVERSE_URL)
        raw, source = response.content, str(response.url)
    else:
        raw = (view / "sessions" / str(prior) / "members.csv").read_bytes()
        try:
            _, dated = market.membership(root, day)
        except market.MarketError as exc:
            if not str(exc).startswith("No dated Nifty 200 membership"):
                raise
        else:
            raw = (dated / "members.csv").read_bytes()
        source = f"NSE index volume and turnover {day}, previous dated ISIN basket {prior}"
    members = market.read_members(raw)
    frame, volume, turnover = constituents.day_table(view, day)
    matched = constituents.reconcile_members(
        frame, volume, turnover, members.symbol, dict(zip(members.symbol, members["isin"]))
    )
    if matched is None or set(matched["isin"]) != set(members["isin"]):
        # A missed date may straddle a reconstitution. A subsequent list is usable
        # only if independently matched to both published totals on the actual date.
        if day >= market.today():
            raise market.MarketError(f"Dated NSE membership disagrees with index totals on {day}")
        response = feed.get(market.UNIVERSE_URL)
        candidate = market.read_members(response.content)
        matched = constituents.reconcile_members(
            frame,
            volume,
            turnover,
            candidate.symbol,
            dict(zip(candidate.symbol, candidate["isin"])),
        )
        if matched is None or set(matched["isin"]) != set(candidate["isin"]):
            raise market.MarketError(
                f"Unresolved dated Nifty 200 change on {day}; restore the official membership interval"
            )
        members = candidate
        source = f"{response.url}, independently matched to NSE index volume and turnover on {day}"
    sectors = members.set_index("isin")["sector"]
    rows = matched.reset_index().rename(
        columns={"symbol": "Symbol", "isin": "ISIN Code", "series": "Series"}
    )
    rows["Industry"] = rows["ISIN Code"].map(sectors)
    buf = StringIO()
    rows[["Symbol", "Industry", "Series", "ISIN Code"]].to_csv(buf, index=False)
    return buf.getvalue().encode(), {
        "source": source,
        "index_volume": volume,
        "index_turnover_crore": turnover,
    }


def entrant_history(root, view, feed, isin, symbol, days, anchor, links):
    """Rebuild pre-membership observations from actual NSE quotes, by historical ISIN."""
    from .frozen_prices import FIELDS, NSE_FIELDS

    current, old_symbol = isin, None
    old_px = shares = 1.0
    records = []
    past_actions, _, coverage, _ = historical_sessions.reference_rows(root)
    for d in reversed(days[-399:]):
        history.stage_day(view, d, feed.http)
        bhav, _ = constituents.load_daily(view, d)
        eligible = bhav[bhav.SctySrs.str.strip().isin(["EQ", "BE", "BZ"])]
        row = eligible[eligible.ISIN.eq(current)]
        if row.empty and old_symbol and eligible.TckrSymb.eq(old_symbol).any():
            raise market.MarketError(
                f"Missing sourced predecessor ISIN for {old_symbol} before {d}"
            )
        if row.empty:
            continue
        if len(row) != 1:
            raise market.MarketError(f"Ambiguous historical NSE issuer {d} {current}")
        item = row.iloc[0]
        current = item.ISIN
        if d <= anchor:
            if not any(a <= d <= b for a, b in coverage["actions"]):
                raise market.MarketError(f"Missing dated NSE ex-action evidence for {d}")
            subjects = {
                (r["subject"], r.get("faceVal") or "")
                for r in past_actions.get((d, item.TckrSymb), ())
                if r["isin"] == current
            }
            prior_releases = conventions.dated(root / "stage3/market", d)
        else:
            raw = (view / "context" / str(d) / "actions.json").read_bytes()
            subjects = frozen_prices.notices(raw, d).get((current, item.TckrSymb), ())
            prior_releases = conventions.dated(view, d)
        release = prior_releases.get(current, {})
        prices = [float(item[k]) for k in NSE_FIELDS]
        op, high, low, close, volume = prices
        if not 0 < low <= min(op, close) <= max(op, close) <= high or volume < 0:
            raise market.MarketError(f"Invalid NSE quote for {symbol} on {d}")
        prior_close = float(item.PrvsClsgPric)
        capital = any(frozen_prices.CAPITAL.search(s) for s, _ in subjects)
        if (
            prior_close > 0
            and market.is_strict_move(close, prior_close)
            and not capital
            and not release.get("quarantine_release")
        ):
            break
        factors, gap = frozen_prices.adjustment_factors(subjects, prior_close, release)
        if gap:
            break
        records.append(
            {
                "day": d,
                "symbol": symbol,
                **dict(zip(FIELDS, [*(v * old_px for v in prices[:4]), volume * shares])),
            }
        )
        old_px *= factors[0]
        shares *= factors[1]
        old_symbol = None
        if any(frozen_prices.SUBDIVISION.search(s) for s, _ in subjects):
            predecessor = links.get((str(d), current))
            if predecessor:
                current = predecessor
            else:
                old_symbol = item.TckrSymb
    return pd.DataFrame(reversed(records), columns=["day", "symbol", *FIELDS])


def kite_crosscheck(feed, day, eq, members):
    comparison = []
    try:
        for m in members.itertuples():
            row = eq.loc[(eq.ISIN.eq(m.isin)) & (eq.SctySrs.str.strip().eq(m.series))].iloc[0]
            token = (int(row.FinInstrmId) << 8) | 1
            bars = feed.history(token, day, day)
            if bars.empty or str(bars.day.iloc[0])[:10] != str(day):
                comparison.append({"isin": m.isin, "status": "missing"})
                continue
            quote = float(bars.close.iloc[0])
            close = float(row.ClsPric)
            comparison.append(
                {
                    "isin": m.isin,
                    "kite_close": quote,
                    "nse_close": close,
                    "status": "agrees" if abs(quote - close) <= market.TOLERANCE else "differs",
                }
            )
    except (market.MarketError, OSError, ValueError) as exc:
        return {"status": "unavailable", "reason": type(exc).__name__, "observations": comparison}
    return {"status": "checked", "observations": comparison}


def ensure_input(root, view, cfg, day, prior, following, terms=None):
    folder = view / "sessions" / str(day)
    if folder.exists():
        meta = market.verify(view, day)
        if meta["prior_day"] != str(prior):
            raise market.MarketError(f"Missing prior NSE session for {day}")
        # Only uncommitted dates reach this routine. A dated final band can appear
        # after its bhavcopy. Preserve the previous panel if its release evidence changes.
        source = view / "sources/forward-conventions" / f"{day}.json"
        receipts = conventions.dated(view, day)
        needs_review = any(receipts.get(i["isin"], {}).get("unverified_jump") for i in following)
        if needs_review:
            members = market.read_members((folder / "members.csv").read_bytes())
            forward_conventions.prepare(view, day, members, following)
        if market.sha(source.read_bytes()) == meta["conventions_sha256"]:
            return
        old_hash = market.sha((folder / "manifest.json").read_bytes())
        archive = view / "earlier-uncommitted-inputs" / str(day) / old_hash
        archive.parent.mkdir(exist_ok=True, parents=True)
        if archive.exists():
            raise market.MarketError(f"Duplicate uncommitted input revision for {day}")
        folder.rename(archive)
    previous = view / "sessions" / str(prior)
    market.verify(view, prior)
    with market.Feed() as feed:
        history.stage_day(view, day, feed.http)
        raw, membership = dated_members(root, view, day, prior, feed)
        members = market.read_members(raw)
        exchange.collect(view, feed, day, prior, members)
        exchange.read_context(view, day, verify_sources=True)
        bhav, idx = constituents.load_daily(view, day)
        current = frozen_prices.session_rows(day, members, bhav, idx)
        source = view / "sources/nse-daily" / str(day)
        meta = json.loads((source / "sources.json").read_bytes())
        old = pd.read_parquet(previous / "bars.parquet")
        old_members = market.read_members((previous / "members.csv").read_bytes())
        anchor = date.fromisoformat(cfg["anchor_session"])
        with market.staging(folder.parent) as tmp:
            bhav.to_csv(tmp / "bhavcopy.csv", index=False)
            shutil.copyfile(source / "indices.csv", tmp / "indices.csv")
            (tmp / "members.csv").write_bytes(raw)
            manifest = {
                "format": "exchange-history-v2",
                "day": str(day),
                "prior_day": str(prior),
                "source": "NSE final equity bhavcopy, index closes and corporate announcements",
                "source_files": meta,
                "dated_membership": membership,
            }
            write_json(tmp / "manifest.json", manifest)
            forward_conventions.prepare(view, day, members, following, session_folder=tmp)
            ex = frozen_prices.notices(
                (view / "context" / str(day) / "actions.json").read_bytes(), day
            )
            resets = json.loads((previous / "manifest.json").read_bytes()).get("reset_after", [])
            links = {
                (t["effective"], t["successor_isin"]): t["predecessor_isin"]
                for t in (terms or {}).values()
                if t.get("successor_isin") and t["successor_isin"] != t["predecessor_isin"]
            }
            shutil.copyfile(
                view / "sources/forward-conventions" / f"{day}.json", tmp / "closing-evidence.json"
            )
            block, stopped = frozen_prices.append_prices(
                old,
                old_members,
                current,
                ex,
                conventions.dated(view, day),
                resets,
                lambda i, s, ds: entrant_history(root, view, feed, i, s, ds, anchor, links),
                {new: old for (when, new), old in links.items() if when == str(day)},
            )
            block.to_parquet(tmp / "bars.parquet", index=False)
            check = kite_crosscheck(feed, day, bhav, members)
            write_json(tmp / "kite-check.json", check)
            manifest.update(
                conventions_sha256=market.sha(
                    (view / "sources/forward-conventions" / f"{day}.json").read_bytes()
                ),
                requested_start=str(block.day.min()),
                reset_after=stopped,
                verified_closes=len(members),
                members=len(members),
                candles=len(block),
                prior_manifest_sha256=market.sha((previous / "manifest.json").read_bytes()),
                sha256={
                    p.name: market.sha(p.read_bytes())
                    for p in tmp.iterdir()
                    if p.name != "manifest.json"
                },
            )
            write_json(tmp / "manifest.json", manifest)
            tmp.rename(folder)
    market.verify(view, day)
