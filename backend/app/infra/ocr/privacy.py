"""Privacy-aware PII detection and redaction for receipt OCR text and logs."""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?:(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3,5}\)?[\s.-]?)?\d{3,5}[\s.-]?\d{4,5})")
_ADDRESS_LABEL_RE = re.compile(
    r"^(?:deliver(?:y|ed)?\s+to|ship\s+to|billing\s+address|shipping\s+address|address)\s*[:\-]",
    re.IGNORECASE,
)
_NAME_LABEL_RE = re.compile(
    r"^(?:customer\s*name|recipient|billed\s+to|buyer\s+name|name)\s*[:\-]\s*\S+",
    re.IGNORECASE,
)
_TXN_ID_RE = re.compile(
    r"\b(?:utr(?:\s*no)?|upi\s*ref(?:\s*no)?|txn\s*id|transaction\s*id|ref(?:erence)?\s*no)\s*[:.\-#\s]*[A-Za-z0-9]{8,32}\b",
    re.IGNORECASE,
)
_PINCODE_LINE_RE = re.compile(r"\b\d{6}\b")


def redact_pii_text(text: str) -> tuple[str, list[str]]:
    """Mask emails, phone numbers, and delivery address lines without touching product/price lines.

    Returns (redacted_text, list_of_redacted_categories).
    """
    if not text:
        return "", []

    redacted_types: set[str] = set()
    out_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line
        lower = line.strip().lower()

        # Never mask lines that clearly carry monetary totals or item prices
        has_money = bool(re.search(r"(?:₹|rs\.?|inr|\$|€|£)\s*\d|\d+\.\d{2}\s*$", lower))

        if _EMAIL_RE.search(line):
            line = _EMAIL_RE.sub("[REDACTED_EMAIL]", line)
            redacted_types.add("email")

        if _TXN_ID_RE.search(line):
            line = _TXN_ID_RE.sub("[REDACTED_TXN_ID]", line)
            redacted_types.add("transaction_id")

        if _NAME_LABEL_RE.match(lower) and not has_money:
            label = line.split(":", 1)[0] if ":" in line else "Name"
            line = f"{label}: [REDACTED_NAME]"
            redacted_types.add("name")
        elif _ADDRESS_LABEL_RE.match(lower) and not has_money:
            label = line.split(":", 1)[0] if ":" in line else "Address"
            line = f"{label}: [REDACTED_ADDRESS]"
            redacted_types.add("address")
        elif (
            not has_money
            and any(k in lower for k in ("phone", "mob:", "mobile", "tel:", "contact"))
            and _PHONE_RE.search(line)
        ):
            line = _PHONE_RE.sub("[REDACTED_PHONE]", line)
            redacted_types.add("phone")
        elif (
            not has_money
            and _PINCODE_LINE_RE.search(line)
            and any(
                k in lower
                for k in (
                    "road",
                    "street",
                    "lane",
                    "apt",
                    "flat",
                    "floor",
                    "sector",
                    "nagar",
                    "layout",
                    "cross",
                    "main",
                )
            )
        ):
            line = "[REDACTED_ADDRESS_LINE]"
            redacted_types.add("address")

        out_lines.append(line)

    return "\n".join(out_lines), sorted(redacted_types)
