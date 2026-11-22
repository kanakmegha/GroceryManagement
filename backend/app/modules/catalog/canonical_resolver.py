"""Context-aware canonical ingredient name cleaning and duplicate-audit helpers."""

from __future__ import annotations

import re

_LEADING_SERIAL_BULLET_RE = re.compile(
    r"^(?:[\"*•°\-–]+\s*|\d{1,2}\s*[.)*•°\-]+\s*|\d{1,2}\s+(?=[A-Za-z][A-Za-z]+\s+[A-Za-z])|[A-Za-z]\s+(?=[A-Z]{3,}\b))+"
)
_SECTION_BLEED_RE = re.compile(r"^(?:DRY\s*FRUITS|DAL)\s*[-=]{2,}>?\s*", re.IGNORECASE)
_SUMMARY_BLEED_RE = re.compile(
    r"\b(?:item|atem|a[eê]m|tim)\s*qty\s*[:\-]?\s*\d*\s*$",
    re.IGNORECASE,
)
_TRAILING_UNIT_RATE_RE = re.compile(
    r"\s+(?:kg|kgs|g|gm|gms|ml|l|ltr|pcs|pc)\s+\d+(?:\.\d+)?\s*$",
    re.IGNORECASE,
)
_KNOWN_GRADE_PREFIXES = {"40 chawal"}


def clean_canonical_candidate_name(
    raw_name: str,
    *,
    unit_price_paise: int | None = None,
    line_total_paise: int | None = None,
    quantity: float | None = None,
) -> str:
    """Clean OCR/receipt noise from an item name without stripping genuine grades or variants.

    Preserves e.g. '40 Chawal' (grade) and 'Chana Kala' vs 'Chana Dal', while stripping
    receipt serials ('1 *', '11 '), summary bleed ('Atem Qty: 28'), section bleed ('DAL =====>'),
    and rate/price contamination ('120 Chana Dal', 'Badam 1180', 'Chana Dal 1120 123 234').
    """
    text = (raw_name or "").strip()
    if not text:
        return ""

    text = _SECTION_BLEED_RE.sub("", text).strip()
    text = _SUMMARY_BLEED_RE.sub("", text).strip()

    # Preserve known grade-prefixed staples like '40 Chawal' (strip only leading serial before '40
    # Chawal')
    grade_m = re.match(r"^(?:\d{1,2}\s+)?(40\s+chawal)\b", text, re.IGNORECASE)
    if grade_m:
        return "40 Chawal"

    # Strip leading bullets, quotes, and serials (e.g. '1 * ', '11 ', '• ', '" ')
    prev = None
    while prev != text:
        prev = text
        text = _LEADING_SERIAL_BULLET_RE.sub("", text).strip()
        # Strip leading single/2-digit serial before a single capitalized/uppercase word (e.g. '6
        # Moong', '10 CHOLE')
        text = re.sub(r"^(?:[1-9]|[12]\d)\s+(?=[A-Za-z]{3,}\b)", "", text).strip()

    # Strip leading 3+ digit rate prefix when followed by alphabetic product words (e.g. '120
    # Chana Dal', '130 Chana Dal')
    text = re.sub(r"^\d{3,5}\s+(?=[A-Za-z]{2,})", "", text).strip()

    # Strip trailing '<unit> <rate>' (e.g. 'Moong Dhuli KG 120', 'LALMALKA KG 94')
    text = _TRAILING_UNIT_RATE_RE.sub("", text).strip()

    # Strip trailing standalone numbers that match known receipt rate/total/qty or >=3-digit
    # rate/noise suffixes
    price_ints: set[int] = set()
    if unit_price_paise is not None and unit_price_paise > 0:
        price_ints.add(round(unit_price_paise / 100))
    if line_total_paise is not None and line_total_paise > 0:
        price_ints.add(round(line_total_paise / 100))
    qty_int = round(quantity) if quantity is not None and quantity > 0 else None

    tokens = text.split()
    while len(tokens) >= 2:
        last = tokens[-1].rstrip(".,:-")
        if re.fullmatch(r"\d+(?:\.\d+)?", last):
            try:
                num_val = float(last)
            except ValueError:
                break
            num_int = round(num_val)
            is_known_price = num_int in price_ints
            is_known_qty = (
                qty_int is not None and abs(num_val - quantity) < 1e-3 and num_int == qty_int
            )
            is_rate_or_noise_tail = (
                num_val >= 100 or "." in last or (len(tokens) >= 3 and num_val >= 10)
            )
            if is_known_price or is_known_qty or is_rate_or_noise_tail:
                tokens.pop()
                continue
        break

    cleaned = " ".join(tokens)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(' -,:.\t|*•°"')
    return cleaned
