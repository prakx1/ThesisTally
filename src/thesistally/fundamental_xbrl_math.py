"""Use the registered operating-profit formula when the row repeats its own index."""

import re

from .fundamental_native_rows import _page

INDEX = re.compile(r"(?im)^(\s*\(([IVXLCDM]+)\)\s+Total Expenses\s+)(\(\2\))(?=\s{2,}[-(\d])")


def operating_profit(filing, text):
    if filing["tags"].get("operating_profit") != "RevenueFromOperations-Expenses+FinanceCosts":
        return None
    matches = list(INDEX.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    cleaned = text[: match.start(3)] + " " * len(match[3]) + text[match.end(3) :]
    output = _page(filing, cleaned)
    if not output or "operating_profit" not in output[0]:
        return None
    value = output[0]["operating_profit"]
    cell = output[1]["operating_profit"]
    if cell["cell"].get("arithmetic") != "revenue - total expenses + finance costs":
        return None
    return {
        "field": "operating_profit",
        "value": str(value),
        "method": "original_indexed_expenses_operating_profit",
        "evidence": {
            **cell,
            "printed_line": text.splitlines()[text[: match.start(3)].count("\n")],
            "repeated_statement_index": match[3],
        },
    }
