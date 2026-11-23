"""Printed currency legends in exchange financial statements."""

import re

_MONEY = r"(?:₹|\b(?:Rs\.?|INR|rupees?)\b)"
_SCALE = r"(?:crore|lac|lakh|million)s?\b"
RUPEE_LEGEND = re.compile(
    _MONEY + r"[ .,:(]*(?:in\s+)?" + _SCALE + "|" + _SCALE + r"\s*(?:of|in)?\s*" + _MONEY,
    re.IGNORECASE,
)
FOREIGN = re.compile(
    r"[$€£¥₩]\s*(?:(?:in\s+)?(?:crores?|lakhs?|lacs?|millions?|thousands?|billions?)\b|[-(]?\d)"
    r"|\b(?:crores?|lakhs?|lacs?|millions?|thousands?|billions?)\s*(?:of|in)?\s*[$€£¥₩]"
    r"|\b(?:USD|dollars?|GBP|pounds?|EUR|euros?|JPY|yen|CNY|RMB|yuan|AUD(?!\s+ited\b)|CAD|HKD|"
    r"PKR|LKR|NPR|AED|dirhams?|francs?|CHF|SEK|SGD|NZD|SAR|ZAR|KRW|renminbi)\b",
    re.IGNORECASE,
)


def currency_source(header):
    return (
        "printed_rupee_legend"
        if RUPEE_LEGEND.search(header)
        else "printed_scale_and_structured_inr_matches"
    )
