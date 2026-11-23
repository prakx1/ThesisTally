"""Reconstruct the 500-session price history and ex-date total shareholder return."""

import json
import re
import tempfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import baseline_earnings, convention_market, market, steady_actions
from . import history as official_history


def history(data, archive, days):
    """Extend the certified 400-session panels using the same official price chains."""
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        (work / "market/sources/adjusted").mkdir(parents=True)
        (work / "evidence").mkdir()
        (work / "evidence/splits").symlink_to((data / "baseline/evidence/splits").resolve())
        tape, _ = convention_market.corrected_tape(
            data, data / "baseline/market", work / "market", days
        )
        aliases = Path("sources/adjusted/action-aliases.json")
        if (work / "market" / aliases).read_bytes() != (archive / aliases).read_bytes():
            raise market.MarketError(
                "Long-history alias evidence disagrees with the certified view"
            )
    events = json.loads((archive / "sources/stage3-events.json").read_bytes())
    for day, securities in sorted(events["days"].items()):
        d = date.fromisoformat(day)
        for isin, item in securities.items():
            mask = tape.day.eq(d) & tape["isin"].eq(isin)
            if item.get("quarantine_release"):
                tape.loc[mask, "unexplained_jump"] = False
            if not (term := item.get("rights_issue")):
                continue
            now = tape[mask].iloc[0]
            prior = tape[tape["isin"].eq(isin) & tape.day.lt(d)].sort_values("day").iloc[-1]
            observed = now.price_factor / prior.price_factor
            if prior.chain != now.chain or not np.isclose(
                observed, term["factor"], rtol=1e-10, atol=0
            ):
                tail = tape.day.ge(d) & tape.chain.eq(now.chain)
                tape.loc[tail, "chain"] = prior.chain
                tape.loc[tail, "price_factor"] *= term["factor"] / observed
                tape.loc[tail, "share_factor"] *= prior.share_factor / now.share_factor
            if not market.is_strict_move(now.close, term["terp"]):
                tape.loc[mask, "unexplained_jump"] = False
    tape = tape[~tape.symbol.isin(market.BENCHMARKS)].copy()
    # A genuine unresolved move breaks classification for any window that crosses it.
    tape.sort_values(["day", "chain"], inplace=True)
    cuts = tape.groupby("chain", sort=False).unexplained_jump.cumsum()
    tape["chain"] = tape.chain + "#" + cuts.astype(str)
    tape = tape[~tape.unexplained_jump].copy()
    if tape.duplicated(["day", "chain"]).any():
        raise market.MarketError("Nonunique adjusted lineage")
    return tape


# Literal variants present in the NSE annual EQ notice archives. "Division"
# is a separate capital event and cannot identify cash income.
DIV = re.compile(
    r"\b(?:div|divd|dividend|dividends|divdend|diviednd|dividen|dividiend|divividend|"
    r"ividend|dividned|ldividend|interimdividend|intdividend|"
    r"(?:meeting|me0eting)dividend|divided(?=\s*[-:]?\s*(?:rs|re)\b))\b",
    re.IGNORECASE,
)
CAPITAL = re.compile(r"bonus|split|rights|merger|arrangement|consolidation", re.IGNORECASE)
CASH = re.compile(r"(?:\b(?:rs|re|inr)\.?\s*[-:]?\s*|₹\s*)([\d,]+(?:\.\d+)?)", re.IGNORECASE)
BARE = re.compile(
    r"\b(?:dividend|div)\s*[-:]?\s*(\d+(?:\.\d+)?)\s*(?:/-)?\s*per\s+sh", re.IGNORECASE
)


def dividend_amount(subject):
    """Return gross cash per share or None when the printed terms are ambiguous."""
    if CAPITAL.search(subject) or re.search(
        r"face value|nominal|paid.up|%", subject, re.IGNORECASE
    ):
        return None
    # Amounts described as included in an aggregate do not constitute a second payment.
    text = re.split(
        r"\b(?:including|inclusive of|of which|comprising)\b", subject, flags=re.IGNORECASE
    )[0]
    values = CASH.findall(text)
    if values:
        return sum(float(v.replace(",", "")) for v in values)
    bare = BARE.search(text)
    return float(bare[1]) if bare else None


def standalone_cash(subjects):
    """Multiple NSE notices can be revisions of one cash leg on the same ex date."""
    amounts = []
    components = set()
    for subject in sorted(subjects):
        amount = dividend_amount(subject)
        if amount is None:
            return np.nan
        labels = set(re.findall(r"\b(special|final|interim)\b", subject.casefold()))
        if not labels:
            labels.add("ordinary")
        if components & labels or (
            ("ordinary" in components and labels & {"final", "interim"})
            or ("ordinary" in labels and components & {"final", "interim"})
        ):
            return np.nan
        components.update(labels)
        amounts.append(amount)
    return sum(amounts)


def matching_issue(quoted, printed, symbol, issuer_quotes):
    """The same issue family, unique ordinary EQ line and exact exchange ticker."""
    issuer = baseline_earnings.issuer(quoted)
    return bool(
        issuer
        and issuer == baseline_earnings.issuer(printed)
        and quoted[7:9] == printed[7:9] == "01"
        and issuer_quotes.get(issuer) == {(symbol, quoted)}
    )


def exchange_issues(data, d):
    folder = data / "sources/nse-daily" / str(d)
    if not folder.exists():
        folder = data / "baseline/evidence/sources/nse-daily" / str(d)
    if not folder.exists():
        return {}, None
    meta = json.loads((folder / "sources.json").read_bytes())["bhavcopy.zip"]
    raw = (folder / "bhavcopy.zip").read_bytes()
    if market.sha(raw) != meta["sha256"]:
        raise market.MarketError(f"Changed NSE issuer cross-check {d}")
    bhav = official_history.normalize_bhav(official_history.extract_bhav(raw), d)
    groups = defaultdict(set)
    for symbol, isin in bhav.loc[
        bhav.SctySrs.str.strip().isin(["EQ", "BE", "BZ"]), ["TckrSymb", "ISIN"]
    ].itertuples(index=False, name=None):
        if issuer := baseline_earnings.issuer(isin):
            groups[issuer].add((symbol, isin))
    return groups, meta


def cash_yields(data, tape, calendar):
    """Map ex-date notices to the traded security; missing archive coverage remains unknown."""
    sources = {}
    covered = set()
    notices = defaultdict(set)
    capital = set()
    notice_files = defaultdict(set)
    for p in sorted((data / "sources/reference").glob("actions-????-??-??_????-??-??.json")):
        raw = p.read_bytes()
        meta = json.loads(p.with_suffix(".json.source.json").read_bytes())
        if market.sha(raw) != meta["sha256"]:
            raise market.MarketError(f"Changed cash distribution record {p}")
        start, end = map(date.fromisoformat, p.stem[8:].split("_"))
        covered.update(d for d in calendar if start <= d <= end)
        sources[p.name] = meta
        for row in json.loads(raw):
            if row.get("series") != "EQ" or row.get("exDate") in (None, "-"):
                continue
            subject = " ".join(row["subject"].lower().split())
            ex = datetime.strptime(row["exDate"], "%d-%b-%Y").date()
            if CAPITAL.search(subject):
                capital.update([(ex, row["isin"]), (ex, row["symbol"])])
            if DIV.search(subject):
                key = ex, row["isin"], row["symbol"]
                notices[key].add(subject)
                notice_files[key].add(p.name)
    dates = tape.set_index(["day", "isin"]).index
    by_symbol = tape.set_index(["day", "symbol"]).index
    if not dates.is_unique or not by_symbol.is_unique:
        raise market.MarketError("Ambiguous notice identity")
    grouped = defaultdict(set)
    receipts = defaultdict(lambda: {"notice_isins": set(), "source_urls": set(), "methods": set()})
    ambiguous_identity = set()
    active_date = None
    for evidence_key, subjects in sorted(notices.items()):
        d, isin, sym = evidence_key
        if (d, isin) in dates:
            key = dates.get_loc((d, isin))
            method = "dated_isin"
        elif (d, sym) in by_symbol:
            key = by_symbol.get_loc((d, sym))
            if active_date != d:
                quotes, source = exchange_issues(data, d)
                active_date = d
            if matching_issue(tape.iloc[key]["isin"], isin, sym, quotes):
                method = "unique_dated_EQ_ticker_and_NSDL_issuer"
                receipts[key]["source_urls"].add(source["url"])
            else:
                method = "unverified_notice_identity"
                ambiguous_identity.add(key)
        else:
            continue
        receipts[key]["methods"].add(method)
        receipts[key]["notice_isins"].add(isin)
        receipts[key]["source_urls"].update(sources[f]["url"] for f in notice_files[evidence_key])
        grouped[key].update(subjects)
    holder = steady_actions.holder_register(data, revised=True)
    specific = {
        (date.fromisoformat(t["effective"]), t["predecessor_isin"]): t["bonus"]
        for t in holder["actions"]
        if t.get("bonus", {}).get("dividend_per_original")
    }
    income = np.zeros(len(tape))
    income[~tape.day.isin(covered)] = np.nan
    issues, events = [], []
    for key, subjects in sorted(grouped.items()):
        bar = tape.iloc[key]
        leg = specific.get((bar.day, bar["isin"]))
        composite = (bar.day, bar["isin"]) in capital or (bar.day, bar.symbol) in capital
        if key in ambiguous_identity:
            cash = np.nan
        elif leg and composite and any(s.strip().casefold() in subjects for s in leg["subjects"]):
            cash = leg["dividend_per_original"] / (1 + leg["new_shares"] / leg["old_shares"])
        elif composite:
            cash = np.nan
        else:
            cash = standalone_cash(subjects)
        row = {
            "day": str(bar.day),
            "isin": bar["isin"],
            "symbol": bar.symbol,
            "subjects": sorted(subjects),
            "cash_per_share": float(cash) if np.isfinite(cash) else None,
            **{name: sorted(values) for name, values in receipts[key].items()},
        }
        if not composite:
            events.append(row)
        income[key] = cash / bar.close
        if not np.isfinite(cash):
            issues.append(row)
    return pd.Series(income, index=tape.index), sources, issues, events
