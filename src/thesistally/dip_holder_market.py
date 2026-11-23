"""Supply the shared execution engine with verified holder terms on an isolated replay view."""

import json

from . import corporate, market, steady_actions
from .pilot_data import checked_link, write_json


def split_receipt(data_root, *, revised=False):
    approved = (
        steady_actions.holder_register(data_root, revised=True)
        if revised
        else steady_actions.verified(data_root, steady_actions.HOLDERS.read_bytes())
    )
    records = [a for a in approved["actions"] if a["type"] == "stock_split" and "bonus" not in a]
    urls = {a[k] for a in records for k in ("source_url", "credit_source")}
    bonuses = [a for a in approved["actions"] if revised and a["type"] == "bonus"]
    for term in bonuses:
        leg = term["bonus"]
        urls.update(leg[k] for k in ("source_url", "listing_source", "fraction_source") if k in leg)
    evidence = [s for s in approved["evidence"] if s["url"] in urls]
    if {s["url"] for s in evidence} != urls:
        raise market.MarketError("The subdivision register lacks a source or credit receipt")
    # Apply the same date/ratio checks as the normal compulsory-equity register.
    corporate._parse(json.dumps(records).encode())
    receipt = {"version": approved["version"], "actions": records, "evidence": evidence}
    if revised:
        from .dip_bonus import parse

        parse(json.dumps(bonuses).encode())
        receipt["bonuses"] = bonuses
    return receipt


def holder_market(data_root, folder, receipt):
    source = data_root / "stage3/market"
    view = folder / "market"
    actions = view / "sources/compulsory-actions"
    actions.mkdir(parents=True, exist_ok=True)
    # Only corporate terms and their source directory are local to this study.
    # Prices, memberships, contexts, quarantines and indicators remain the certified originals.
    for parent, target, skip in [
        (source, view, {"corporate_actions.json", "sources"}),
        (source / "sources", view / "sources", {"compulsory-actions"}),
        (source / "sources/compulsory-actions", actions, set()),
    ]:
        for original in parent.iterdir():
            if original.name not in skip:
                checked_link(original, target / original.name)
    local = source / "corporate_actions.json"
    original = json.loads(local.read_bytes()) if local.is_file() else []
    records = [*original, *receipt["actions"]]
    path = view / "corporate_actions.json"
    if path.is_file() and json.loads(path.read_bytes()) != records:
        raise market.MarketError("Different dated holder terms already exist in this study")
    write_json(path, records)
    corporate.actions(view)
    if "bonuses" in receipt:
        write_json(view / "dip-bonus-actions.json", receipt["bonuses"])
    for term in receipt["actions"]:
        issuer = actions / term["predecessor_isin"]
        if issuer.is_symlink():
            raise market.MarketError("Holder evidence already exists in the immutable parent")
        issuer.mkdir(exist_ok=True)
        sources = {}
        for item in receipt["evidence"]:
            if item["url"] in (term["source_url"], term["credit_source"]):
                archive = data_root / "stage3/evidence/holder-events" / item["file"]
                checked_link(archive, issuer / item["file"])
                sources[item["file"]] = {"url": item["url"], "sha256": item["sha256"]}
        write_json(issuer / "sources.json", sources)
    return view
