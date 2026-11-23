"""First reported quarterly facts can become public at different documented instants."""

from __future__ import annotations

from itertools import groupby

from .fundamental_facts import FIELDS


def first_fact_timeline(candidates, attest, formal=None, *, audited_through=None):
    """Accept additional firsts only after exhaustive, source-verified body-only notices.

    A missing or partly decoded PDF cannot prove which other fields it previously
    published. An exchange 'Original' structured filing may supply previously absent
    facts only behind an unbroken sequence of exhaustive earlier texts.
    """
    records = []
    other_basis = []
    sources = {}
    originals = dict.fromkeys(FIELDS)
    clocks = {}
    observable = set(FIELDS)
    blocked = None
    absence_evidence = []
    absence_open = bool(audited_through)
    absence_before = audited_through
    prior = list(candidates)
    if formal:
        prior.append(formal)
    for instant, coeval in groupby(
        sorted(prior, key=lambda n: n["announced_at"]), key=lambda n: n["announced_at"]
    ):
        observed = []
        failures = []
        for notice in coeval:
            evidence, reason = attest(notice)
            if evidence and evidence.get("other_basis"):
                other_basis.append({**evidence, "announced_at": instant})
            elif evidence:
                observed.append((notice, evidence))
            else:
                failures.append(reason)
        if not observed and not failures:
            continue
        if absence_open:
            if (
                instant <= audited_through
                and not failures
                and all(
                    ev.get("exhaustive_body")
                    and "eps" in ev.get("absent_fields", [])
                    and ev["values"].get("eps") is None
                    for _, ev in observed
                )
            ):
                absence_evidence.extend(
                    {
                        "announced_at": instant,
                        "source_url": ev["notice_url"],
                        "sha256": ev["notice_sha256"],
                        "accession_sha256": ev.get("accession_sha256"),
                    }
                    for _, ev in observed
                )
            else:
                # At this exact instant a changed, opaque or EPS-bearing publication
                # could already be public. The proven upper bound must stay exclusive.
                absence_before = min(audited_through, instant)
                absence_open = False
        if not observed:
            blocked = next(iter(failures), "first_release_facts_unreconciled")
            break
        conflict = False
        for key in FIELDS:
            choices = [
                (ev["values"][key], ev, notice)
                for notice, ev in observed
                if ev["values"].get(key) is not None
            ]
            if not choices or key in clocks or key not in observable:
                continue
            if len({v for v, _, _ in choices}) != 1:
                conflict = True
                break
            value, proof, _note = choices[0]
            originals[key] = value
            clocks[key] = instant
            scoped = proof.get("field_page_evidence", {}).get(key, {})
            sources[key] = {
                "source_url": proof["notice_url"],
                "sha256": proof["notice_sha256"],
                "accession": proof.get("accession_sha256"),
                "page": proof.get("page"),
                "pdf_sha256": proof.get("pdf_sha256"),
                **scoped,
            }
        if conflict:
            # Do not silently arbitrate conflicting co-timed original publications.
            return None, "conflicting_original_fields_at_same_clock"
        records.extend((notice, proof) for notice, proof in observed)
        if len(clocks) == len(FIELDS):
            break
        for key in set(FIELDS) - clocks.keys():
            if failures or not all(
                ev.get("exhaustive_body") and key in ev.get("absent_fields", [])
                for _, ev in observed
            ):
                observable.discard(key)
        if not (observable - clocks.keys()):
            blocked = next(iter(failures), "additional_fields_unproved_in_original_statement")
            break
    if not clocks:
        return None, (blocked or "no_first_fields_verified")
    initial = records[0][0]["announced_at"]
    effective = clocks.get("eps", initial)
    evidence = next(
        (
            proof
            for n, proof in records
            if n["announced_at"] == effective and proof["values"].get("eps") is not None
        ),
        records[0][1],
    )
    absence_start = absence_evidence[0]["announced_at"] if absence_evidence else None
    proven_interval = bool(absence_start and absence_before and absence_start < absence_before)
    return {
        "eps_absence_from": absence_start if proven_interval else None,
        "eps_absence_before": absence_before if proven_interval else None,
        "eps_absence_evidence": absence_evidence if proven_interval else [],
        "values": originals,
        "fact_announced_at": clocks,
        "fact_sources": sources,
        "initial_disclosure_at": initial,
        "announced_at": effective,
        "complete_available_at": max(clocks.values()) if len(clocks) == len(FIELDS) else None,
        "evidence": evidence,
        "editions": records,
        "remaining_fields_gap": blocked,
        "other_basis_documents": other_basis,
    }, None
