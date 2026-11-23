"""Certify each first PDF text-layer number using independent evidence before use."""

from decimal import Decimal

from .fundamental_facts import FIELDS


def verify_text_fields(original, structured, structured_sha):
    accepted = dict.fromkeys(FIELDS)
    checks = {}
    for key in FIELDS:
        first = original.get(key)
        if first is None:
            continue
        later = structured.get(key) if structured_sha else None
        equal = later is not None and Decimal(str(first)) == Decimal(str(later))
        if equal:
            accepted[key] = first
        checks[key] = {
            "earlier_text_candidate": first,
            "structured_value": later,
            "structured_sha256": structured_sha,
            "status": (
                "agrees_with_independent_structured_value"
                if equal
                else "conflicting_original_text_and_structured"
                if later is not None
                else "no_independent_numeric_confirmation"
            ),
        }
    return accepted, checks


def merge_original_fields(primary, supplemental, *, source="optical"):
    """Combine two independently keyed readings of the same dated original packet."""
    if primary is None:
        return supplemental
    if supplemental is None:
        return primary
    values = dict(primary["values"])
    evidence = dict(primary.get("field_page_evidence", {}))
    conflicts = dict(primary.get("original_field_conflicts", {}))
    for field, details in supplemental.get("original_field_conflicts", {}).items():
        conflicts[field] = details
        values[field] = None
        evidence.pop(field, None)
    for field, value in supplemental["values"].items():
        if value is None or field in conflicts:
            continue
        if values.get(field) is None:
            values[field] = value
            evidence[field] = supplemental["field_page_evidence"][field]
        elif Decimal(str(values[field])) != Decimal(str(value)):
            conflicts[field] = {"primary": values[field], source: value}
            values[field] = None
            evidence.pop(field, None)
    return {
        **primary,
        "values": values,
        "field_page_evidence": evidence,
        {
            "optical": "additional_original_optics",
            "native": "additional_native_cells",
            "reviewed_xbrl": "additional_reviewed_xbrl_optics",
        }[source]: supplemental,
        "original_field_conflicts": conflicts,
    }
