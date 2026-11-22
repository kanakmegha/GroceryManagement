"""Centralized product naming standardization service (Phase 5B).

Separates three distinct concepts:
1. Raw receipt description (preserved untouched in PurchaseItem.raw_text).
2. Canonical product identity (clean, deterministic name for CanonicalItem).
3. User-facing display name (concise, readable label without OCR/rate/promo noise).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.modules.catalog.canonical_resolver import clean_canonical_candidate_name
from app.modules.catalog.ingredient_mapping_service import detect_cooking_variant

_LEADING_NOISE_RE = re.compile(
    r"^(?:[\s\"'*•°\-–.=]+|\d{1,2}\s*[.)*•°\-]+\s*|ml\)\s*|\d{1,2}\s+resh\s+|\d{1,2}\s+(?=[A-Za-z]{3,}\b))+",
    re.IGNORECASE,
)
_PROMO_TAIL_RE = re.compile(
    r"(?:\s*[-–|]\s*(?:perfect\s+colour.*|rich\s+aroma.*|authentic\s+taste.*|smooth\s*&\s*creamy.*|zero\s+added\s+sugar.*|7\s+days\s+return.*))+$",
    re.IGNORECASE,
)
_PACK_PAREN_RE = re.compile(
    r"\(\s*\d+(?:\s*[-–]\s*\d+)?\s*(?:kg|kgs|g|gm|gms|ml|l|ltr|pcs|pc|pack|packs|units)\s*\)",
    re.IGNORECASE,
)
_PACK_DESC_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:kg|kgs|g|gm|gms|ml|l|ltr|pcs|pc|pack|packs|pkt|pouch|carton|bottle|units)\b(?:\s*(?:pouch|carton|pack|packet|bottle|box))?",
    re.IGNORECASE,
)
_TRAILING_RS_RE = re.compile(r"\s+(?:r\$|rs\.?)\s*$", re.IGNORECASE)
_OCR_NAME_CORRECTIONS: dict[str, str] = {
    "chana bal": "Chana Dal",
    "chaner dal": "Chana Dal",
    "chana-đal": "Chana Dal",
    "chana đal": "Chana Dal",
    "tender coconut (elaneer)": "Tender Coconut",
    "resh tender coconut (elaneer)": "Tender Coconut",
    "kallmalka": "Kali Malka",
    "kali malka": "Kali Malka",
    "lal malka": "Lal Malka",
    "lalmalka": "Lal Malka",
    "khulaatta": "Khula Atta",
    "khula atta": "Khula Atta",
    "moong dhuli": "Moong Dhuli",
    "moong dhyli": "Moong Dhuli",
    "moong sabut": "Moong Sabut",
    "akhrot": "Akhrot",
    "akrot": "Akhrot",
    "badam": "Badam",
    "chawal": "Chawal",
    "lal chawal": "Lal Chawal",
    "matar": "Matar",
    "chole": "Chole",
    "chini df": "Chini DF",
    "dhaniya sabut": "Dhaniya Sabut",
    "chana kala": "Chana Kala",
    "lobiya lal": "Lobiya Lal",
    "arhar dal": "Arhar Dal",
    "urad, dhuli": "Urad Dhuli",
    "urad dhuli": "Urad Dhuli",
}
_KNOWN_BRANDS = {
    "amul",
    "aavin",
    "nandini",
    "mother",
    "heritage",
    "india",
    "gate",
    "daawat",
    "fortune",
    "saffola",
    "dhara",
    "tata",
    "aashirvaad",
    "everest",
    "mdh",
    "catch",
    "mtr",
    "eastern",
    "bb",
    "popular",
    "dabur",
    "haldiram",
    "haldirams",
    "prabhat",
    "udhaiyam",
    "happy",
    "mail",
    "get-a-way",
}


@dataclass(frozen=True)
class ProductNamingResult:
    raw_description: str
    canonical_name: str
    display_name: str
    package_size: str | None
    brand: str | None
    variant: str | None
    confidence: str  # "high" | "medium" | "low"
    needs_review: bool
    reason: str


def _smart_title_case(text: str) -> str:
    tokens = text.split()
    out: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low in {"df", "bb"}:
            out.append(tok.upper() if low == "df" else "bb")
        elif low in {"&", "x", "-"}:
            out.append(low)
        else:
            out.append(tok.capitalize() if tok.isupper() or tok.islower() else tok)
    return " ".join(out)


def standardize_product_name(
    raw_description: str,
    *,
    unit_price_paise: int | None = None,
    line_total_paise: int | None = None,
    quantity: float | None = None,
    preserve_package_size: bool = False,
) -> ProductNamingResult:
    """Produce clean canonical and display product names while preserving raw text, brand, and
    variant.
    """
    raw = raw_description or ""
    nfkc = unicodedata.normalize("NFKC", raw)
    visible = "".join(ch for ch in nfkc if unicodedata.category(ch) not in {"Cf", "Cc"}).strip()

    if not visible:
        return ProductNamingResult(
            raw_description=raw,
            canonical_name="",
            display_name="",
            package_size=None,
            brand=None,
            variant=None,
            confidence="low",
            needs_review=True,
            reason="empty_description",
        )

    # Extract package size before stripping it
    pack_matches = _PACK_DESC_RE.findall(visible)
    package_size = pack_matches[0].strip() if pack_matches else None

    # Clean receipt serials, section bleed, and rate contamination
    cleaned = clean_canonical_candidate_name(
        visible,
        unit_price_paise=unit_price_paise,
        line_total_paise=line_total_paise,
        quantity=quantity,
    )
    cleaned = _LEADING_NOISE_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"^mported\b", "Imported", cleaned, flags=re.IGNORECASE)
    cleaned = _TRAILING_RS_RE.sub("", cleaned).strip()
    cleaned = _PROMO_TAIL_RE.sub("", cleaned).strip()

    if not preserve_package_size:
        cleaned = _PACK_PAREN_RE.sub(" ", cleaned)
        cleaned = _PACK_DESC_RE.sub(" ", cleaned)

    # Collapse empty parens, repeated punctuation & whitespace (preserve balanced parens!)
    cleaned = re.sub(r"\(\s*\)", " ", cleaned)
    cleaned = re.sub(r"\s*\|\s*", " ", cleaned)
    cleaned = re.sub(r"[,\s]+", " ", cleaned).strip(' -,:.\t|*•°"')

    # Strip leading single-letter OCR prefix before uppercase staple (e.g. 'S KHULAATTA')
    cleaned = re.sub(r"^[A-Za-z]\s+(?=[A-Z]{4,}\b)", "", cleaned).strip()

    low_key = cleaned.lower()
    if low_key in _OCR_NAME_CORRECTIONS:
        canonical = _OCR_NAME_CORRECTIONS[low_key]
        conf = "high"
        reason = "ocr_standardized"
    elif "40 chawal" in low_key:
        canonical = "Chawal"
        conf = "high"
        reason = "rate_prefix_removed"
    else:
        canonical = _smart_title_case(cleaned) if cleaned else visible
        has_garble = bool(re.search(r"[&$]|nack\b", canonical, re.IGNORECASE))
        conf = "medium" if (has_garble or len(canonical) > 60) else "high"
        reason = "ocr_garble_review" if has_garble else "standardized"

    # Extract brand & variant for metadata
    toks_low = {t.lower() for t in canonical.split()}
    brands = sorted(toks_low & _KNOWN_BRANDS)
    brand = " ".join(b.capitalize() for b in brands) if brands else None
    variant = detect_cooking_variant(canonical)

    return ProductNamingResult(
        raw_description=raw,
        canonical_name=canonical,
        display_name=canonical,
        package_size=package_size,
        brand=brand,
        variant=variant,
        confidence=conf,
        needs_review=(conf != "high"),
        reason=reason,
    )
