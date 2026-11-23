"""Source-backed holder terms for the monthly account, independent of the price-series adjustment."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import corporate, market

HOLDERS = Path(__file__).with_name("revisions") / "stage3-holder-actions.json"


@lru_cache(maxsize=2)
def verified(data_root, raw):
    data = json.loads(raw)
    for item in data["evidence"]:
        file = data_root / "stage3" / "evidence" / "holder-events" / item["file"]
        if not file.exists() or market.sha(file.read_bytes()) != item["sha256"]:
            raise market.MarketError(
                f"Unverified holder terms: {item['file']}. Run python -m thesistally.steady_actions --data-root {data_root}"
            )
    return data


REVISED_HOLDERS = HOLDERS.with_name("stage3-entry-holder-actions.json")


def holder_register(data_root, *, revised=False):
    legacy = verified(data_root, HOLDERS.read_bytes())
    if not revised:
        return legacy
    extra = verified(data_root, REVISED_HOLDERS.read_bytes())
    return {
        "version": extra["version"],
        "actions": [*legacy["actions"], *extra["actions"]],
        "evidence": [*legacy["evidence"], *extra["evidence"]],
    }


CLOSE_HOLDERS = HOLDERS.with_name("close-holder-actions.json")


def terms(root, *, revised=False, close_verified=False):
    base = corporate.actions(root)
    data_root = (
        root.parent.parent if root.name == "market" and root.parent.name == "stage3" else root
    )
    records = holder_register(data_root, revised=revised)["actions"]
    if close_verified:
        records = [*records, *verified(data_root, CLOSE_HOLDERS.read_bytes())["actions"]]
    extra = {t["predecessor_isin"]: t for t in records}
    if len(extra) != len(records):
        raise market.MarketError("Conflicting holder instructions for a predecessor ISIN")
    if extra.keys() & base.keys():
        raise market.MarketError("Duplicate compulsory shareholder convention")
    return {**base, **extra}


def with_holder_dividend(context, term):
    """Credit the cash leg of a verified, still-unresolved combined stock/cash notice."""
    leg = term["bonus"]
    dividend = leg.get("dividend_per_original")
    if not dividend or context.get("holder_dividend_accounted"):
        return context
    subjects = {s.strip().casefold() for s in leg["subjects"]}
    if not any(s.strip().casefold() in subjects for s in context.get("other_actions", ())):
        return context
    return {
        **context,
        "dividend": context.get("dividend", 0) + dividend,
        "holder_dividend_accounted": True,
    }


def evidence_bytes(item, raw):
    """BSE wraps its dated notice in per-request ASP.NET tokens and site navigation."""
    if item.get("representation") != "bse-notice-html-v1":
        return raw
    start = raw.find(b'<table width="979"')
    end = raw.find(b'<td class="footerbox"', start)
    if start < 0 or end < 0:
        raise market.MarketError("Cannot locate the dated BSE notice and its signed body")
    return raw[start:end].replace(b"\r\n", b"\n").rstrip() + b"\n"


def issue_bonus(db, run, day, account, pos, term, emit):
    leg = term["bonus"]
    bonus_units = pos.qty * leg["new_shares"] / leg["old_shares"]
    base_units = pos.qty * term["new_shares"] / term["old_shares"]
    theoretical = pos.mark * pos.qty / (base_units + bonus_units)
    whole, remainder = divmod(pos.qty * leg["new_shares"], leg["old_shares"])
    fraction = remainder / leg["old_shares"]
    basis = pos.basis * bonus_units / (base_units + bonus_units)
    tranche = {
        "parent": term["predecessor_isin"],
        "isin": term["successor_isin"],
        "record": day,
        "whole": whole,
        "fraction": fraction,
        "mark": theoretical,
        "basis": basis,
        "credited": False,
        "delivered": False,
        "paid": not fraction,
    }
    # The original shares remain tradable for a pure bonus. A simultaneous
    # subdivision separately settles the original shares through the depository.
    pos.basis -= basis
    pos.mark -= theoretical * bonus_units / pos.qty
    account.bonuses.append(tranche)
    emit(
        db,
        run,
        day,
        tranche["isin"],
        "corporate_action",
        {
            "stage": "bonus_entitlement",
            "ex_date": day,
            "original_qty": pos.qty,
            "ratio": [leg["new_shares"], leg["old_shares"]],
            "whole": whole,
            "fraction": fraction,
            "cash": 0,
            "source_url": leg["source_url"],
            "convention_version": "stage3-v1",
        },
    )


def settle_bonuses(db, run, day, account, terms, quotes, emit, Position):
    income = 0.0
    for tranche in list(account.bonuses):
        leg = terms[tranche["parent"]]["bonus"]
        isin = tranche["isin"]

        def event(stage, *, isin=isin, leg=leg, tranche=tranche, **data):
            emit(
                db,
                run,
                day,
                isin,
                "corporate_action",
                {
                    "stage": stage,
                    "source_url": leg["source_url"],
                    "convention_version": "stage3-v1",
                    "ex_date": tranche["record"],
                    "credited": leg["credited"],
                    "tradable": leg["tradable"],
                    "ratio": [leg["new_shares"], leg["old_shares"]],
                    **{
                        field: leg[field]
                        for field in ("allotted", "credit_evidence_basis", "listing_source")
                        if field in leg
                    },
                    **data,
                },
            )

        if day >= leg["credited"] and not tranche["credited"]:
            tranche["credited"] = True
            event("bonus_credited", whole=tranche["whole"])
        if day >= leg["tradable"] and not tranche["delivered"]:
            row = quotes.get(isin)
            if row and row["volume"] and row["open"] > 0:
                if tranche["whole"]:
                    pos = account.positions.setdefault(
                        isin, Position(0, row["open"], row["symbol"], 0)
                    )
                    pos.qty += tranche["whole"]
                    pos.basis += (
                        tranche["basis"]
                        * tranche["whole"]
                        / (tranche["whole"] + tranche["fraction"])
                    )
                tranche["delivered"] = True
                event("bonus_tradable", whole=tranche["whole"])
        if not tranche["paid"]:
            q = quotes.get(isin)
            if (
                day >= leg["tradable"]
                and "fraction_reference" not in tranche
                and q
                and q["volume"]
                and not q.get("quarantined")
            ):
                tranche["fraction_reference"] = q["close"]
                tranche["reference_day"] = day
            if day >= leg.get("cash_date", leg["tradable"]):
                published = leg.get("fraction_price")
                if published is None and "fraction_reference" not in tranche:
                    from .steady_momentum import UnpricedHolding

                    raise UnpricedHolding(
                        day, isin, "Missing verified first tradable close for fractional bonus"
                    )
                px = tranche["fraction_reference"] if published is None else published
                cash = corporate.cash(tranche["fraction"], px)
                income += cash
                tranche["paid"] = True
                event(
                    "bonus_fraction_paid",
                    fraction=tranche["fraction"],
                    cash=cash,
                    fraction_unit_price=px,
                    first_close_fallback=published is None,
                    reference_day=tranche.get("reference_day") if published is None else None,
                    source_url=leg.get("fraction_source", leg["source_url"]),
                )
        if tranche["paid"] and tranche["delivered"]:
            account.bonuses.remove(tranche)
    return income


def fetch(data_root, *, revised=False, close_verified=False):
    """Restore the archival PDFs specified by the versioned source manifest."""
    import tempfile

    import httpx

    folder = data_root / "stage3/evidence/holder-events"
    folder.mkdir(exist_ok=True, parents=True)
    files = [HOLDERS] + ([REVISED_HOLDERS] if revised else [])
    if close_verified:
        files.append(CLOSE_HOLDERS)
    evidence = [item for file in files for item in json.loads(file.read_bytes())["evidence"]]
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for item in evidence:
            destination = folder / item["file"]
            if destination.exists():
                if market.sha(destination.read_bytes()) != item["sha256"]:
                    raise market.MarketError(f"Existing holder evidence differs: {destination}")
                continue
            last_error = None
            urls = list(
                dict.fromkeys(
                    [
                        item["url"],
                        item["url"].replace("nsearchives.nseindia.com", "archives.nseindia.com"),
                    ]
                )
            )
            for url in urls:
                try:
                    headers = (
                        {"User-Agent": "Mozilla/5.0", "Accept": "text/html"}
                        if item.get("representation") == "bse-notice-html-v1"
                        else {}
                    )
                    response = client.get(url, headers=headers)
                    response.raise_for_status()
                    content = evidence_bytes(item, response.content)
                    if market.sha(content) != item["sha256"]:
                        raise market.MarketError(
                            f"Archive content does not match holder receipt: {url}"
                        )
                    break
                except (httpx.HTTPError, market.MarketError) as exc:
                    last_error = exc
            else:
                raise market.MarketError(
                    f"Cannot restore {item['file']} from official sources"
                ) from last_error
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as temp:
                temp.write(content)
            Path(temp.name).replace(destination)
    holder_register(data_root, revised=revised)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Restore and verify the registered shareholder-action evidence."
    )
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--revised", action="store_true", help="Include entry-revision holder evidence"
    )
    parser.add_argument(
        "--close-verified", action="store_true", help="Restore supplemental A holder evidence"
    )
    args = parser.parse_args()
    fetch(args.data_root, revised=args.revised, close_verified=args.close_verified)
