"""Layout-aware semantic table reconstruction engine backed by TableIR."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.infra.ocr.schemas import (
    ExtractedReceiptLine,
    OcrTokenBox,
    normalize_metric_unit,
    parse_inr_to_paise,
)
from app.infra.ocr.table_ir import (
    ColumnRole,
    TableCellIR,
    TableColumnIR,
    TableIR,
    TableRowIR,
)

# Ordered from most specific (composite labels, percentage sub-columns) to generic.
_HEADER_PATTERNS: list[tuple[ColumnRole, re.Pattern[str]]] = [
    ("sr_no", re.compile(r"^(?:sr\.?\s*no\.?|sl\.?\s*no\.?|s\.?\s*no\.?|si\.?\s*no\.?|sr\.?|sl\.?|#)$", re.I)),
    ("hsn", re.compile(r"\bhsn(?:/sac)?(?:\s*code)?\b|\bsac(?:\s*code)?\b", re.I)),
    (
        "description",
        re.compile(
            r"\b(?:i?tem\s*(?:&|and)?\s*descript?i?o?n|product\s*desc(?:ription)?|particulars|description|product\s*name|goods|sku|item)\b",
            re.I,
        ),
    ),
    ("mrp", re.compile(r"\b(?:unit\s*)?(?:mrp|rsp|list\s*price)(?:/rsp)?\b", re.I)),
    ("unit_taxable", re.compile(r"\bunit\s*taxable(?:\s*(?:val(?:ue)?|amt|amount))?\b", re.I)),
    ("cgst_pct", re.compile(r"\bcgst\s*(?:rate\s*\(%\)|rate|%)(?!.*\b(?:amt|amount|val(?:ue)?)\b)", re.I)),
    ("sgst_pct", re.compile(r"\b(?:sgst|utgst|s/ut\s*gst)(?:/\s*utgst)?\s*(?:rate\s*\(%\)|rate|%)(?!.*\b(?:amt|amount|val(?:ue)?)\b)", re.I)),
    ("igst_pct", re.compile(r"\bigst\s*(?:rate\s*\(%\)|rate|%)(?!.*\b(?:amt|amount|val(?:ue)?)\b)", re.I)),
    ("cess_pct", re.compile(r"\bcess\s*(?:rate\s*\(%\)|rate|%)(?!.*\b(?:amt|amount|val(?:ue)?)\b)", re.I)),
    ("tax_pct", re.compile(r"^(?:rate\s*\(%\)|tax\s*rate)$", re.I)),
    ("discount_pct", re.compile(r"\b(?:disc(?:ount)?\s*(?:\(%\)|%)|%\s*disc(?:ount)?)\b", re.I)),
    (
        "rate",
        re.compile(
            r"\b(?:unit\s*price|product\s*rate|selling\s*price|rate(?!\s*\(%\))|price)\b",
            re.I,
        ),
    ),
    ("qty", re.compile(r"\b(?:qty|aty|quantity|qnty|guant|quant|pcs)\b", re.I)),
    ("unit", re.compile(r"^(?:uom|unit)$", re.I)),
    ("gross", re.compile(r"\bgross(?:\s*(?:val(?:ue)?|amt|amount))?\b", re.I)),
    ("discount", re.compile(r"\b(?:disc(?:ount)?(?:/margin)?(?:\s*(?:amt|amount|val))?)\b", re.I)),
    ("taxable", re.compile(r"\btaxable(?:\s*(?:val(?:ue)?|amt|amount))?\b", re.I)),
    ("cgst", re.compile(r"\bcgst(?:\s*(?:amt|amount|val(?:ue)?))?\b", re.I)),
    ("sgst", re.compile(r"\b(?:sgst|utgst|s/ut\s*gst)(?:/\s*utgst)?(?:\s*(?:amt|amount|val(?:ue)?))?\b", re.I)),
    ("igst", re.compile(r"\bigst(?:\s*(?:amt|amount|val(?:ue)?))?\b", re.I)),
    ("cess", re.compile(r"\bcess(?:\s*(?:amt|amount|val(?:ue)?))?\b", re.I)),
    ("gst", re.compile(r"\b(?:gst|vat|tax)\b", re.I)),
    ("other_charges", re.compile(r"\b(?:other\s*chg|other\s*charges|charges)", re.I)),
    (
        "total",
        re.compile(
            r"\b(?:net\s*amt|net\s*amount|net\s*val(?:ue)?|line\s*total|total\s*amt\.?|total|amount|amt\.?|value)\b",
            re.I,
        ),
    ),
]

_HEADER_FRAGMENT_WORDS = {
    "sr", "sl", "si", "no", "no.", "item", "tem", "&", "description", "descripton", "particulars", "hsn", "sac",
    "code", "unit", "uom", "mrp", "rsp", "mrp/rsp", "qty", "aty", "quantity", "qnty", "guant", "quant", "product",
    "rate", "rate(%)", "price", "price*", "taxable", "value", "value*", "gross", "disc",
    "disc.", "discount", "discount/", "margin", "margin*", "other", "charges", "charges**",
    "cgst", "sgst", "utgst", "sgst/", "s/utgst", "s/ut", "igst", "cess", "gst", "amt",
    "amt.", "amount", "total", "%",
}

_TABLE_STOP_RE = re.compile(
    r"(?:\bsub\s*total\b|\bsubtotal\b|\bgrand\s*total\b|\bg\.?\s*(?:total|al)\b|\binvoice\s*total\b|\bnet\s*payable\b|"
    r"\bamount\s*payable\b|\bpayable\s*amount\b|\btotal\s*qty\b|\b(?:item|atem)\s*qty\b|\btotal\s*items\b|\bround(?:ing|\s*off)\b|"
    r"\bgoodwill\b|\bredeemed\b|\bhandling\s*charg|\bdelivery\s*charg|\bpacking\s*charg|"
    r"\bhsn\s*summary\b|\btax\s*summary\b|\bgst\s*information\b|\bpaid\s*via\b|"
    r"\bterms\s*(?:&|and)\s*conditions\b|\bauthorised\s*signatory\b|\bamount\s*in\s*words\b)",
    re.I,
)

_SECTION_DIVIDER_RE = re.compile(r"^[^0-9]*(?:={2,}|[-=]{2,}>?|-{3,})\s*$")
_EXPLICIT_CANCELLED_RE = re.compile(r"^(?:\[?\s*(?:cancelled|void|strike)\s*\]?\b|~~.+~~\s*$)", re.I)
_CROSSED_OUT_RE = re.compile(r"^(?:~~|~{1,2}\s*[^~]|\[?\s*(?:cancelled|void|strike)\s*\]?\b)", re.I)

_ROW_TERMINATOR_RE = re.compile(r"\b(?:\d+\s*days?\s*retu[rnmu]{1,3}|non[\s-]*returnable|returnable)\b", re.I)
_TOTAL_FOOTER_RE = re.compile(r"^(?:total|net\s*total|bill\s*total|gross\s*total)\s*:?$", re.I)
_SR_NO_RE = re.compile(r"^(\d{1,3})[.)]?$")
_HSN_RE = re.compile(r"^\d{4,8}$")
_PCT_TOKEN_RE = re.compile(r"^\d+(?:[.,]\d+)?%$")
_QTY_UNIT_RE = re.compile(
    r"(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|kgs|kc|g|gm|gms|gram|grams|ml|l|ltr|litre|litres|pcs|pc|nos|no|pkt|pkts|pack|packs|box|boxes|btl|bottle|bottles|oz|lb|lbs|fl\s*oz|gal)\b",
    re.I,
)
_PACK_SIZE_RE = re.compile(
    r"\b(\d+(?:\.\d+)?\s*(?:kg|g|gm|gms|ml|l|ltr|pcs|pc|pack|packs|oz|lb|lbs|fl\s*oz|gal))\b",
    re.I,
)
_VARIANT_PAREN_RE = re.compile(r"\(([^)]{2,40})\)")
_NUMERIC_ONLY_RE = re.compile(r"^[+\-−]?(?:₹|Rs\.?|INR|USD|EUR|GBP|AED|\$|€|£)?\s*\d[\d,.\s]*%?$")


@dataclass
class _ColSpec:
    column_id: int
    role: ColumnRole
    header_text: str
    x_left: float
    x_right: float
    x_center: float
    x_min: float = 0.0
    x_max: float = 1.0


@dataclass
class TableExtractionResult:
    lines: list[ExtractedReceiptLine] = field(default_factory=list)
    tables: list[TableIR] = field(default_factory=list)
    has_table_headers: bool = False
    detected_columns: list[ColumnRole] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    first_table_y: float | None = None


def _cluster_page_lines(boxes: list[OcrTokenBox]) -> list[list[OcrTokenBox]]:
    if not boxes:
        return []
    ordered = sorted(boxes, key=lambda b: (b.page, b.y_center, b.x))
    lines: list[list[OcrTokenBox]] = []
    current: list[OcrTokenBox] = []
    current_page: int | None = None
    current_y: float | None = None
    current_h = 0.015

    for box in ordered:
        if not box.text.strip():
            continue
        if current_page is None or box.page != current_page:
            if current:
                lines.append(sorted(current, key=lambda item: item.x))
            current = [box]
            current_page = box.page
            current_y = box.y_center
            current_h = max(box.h, 0.012)
            continue
        y_overlap = min(
            max(b.y + b.h for b in current),
            box.y + box.h,
        ) - max(
            min(b.y for b in current),
            box.y,
        )
        min_h = min(current_h, box.h)
        anchor_y = current[0].y_center
        tol = min(0.018, max(current_h, box.h, 0.010) * 0.52)
        same_row = (
            current_y is not None
            and abs(box.y_center - anchor_y) <= tol
            and (min_h <= 0 or y_overlap >= min_h * 0.30)
        )
        if same_row:
            current.append(box)
            current_y = sum(item.y_center for item in current) / len(current)
            current_h = max(current_h, box.h)
        else:
            lines.append(sorted(current, key=lambda item: item.x))
            current = [box]
            current_y = box.y_center
            current_h = max(box.h, 0.012)
    if current:
        lines.append(sorted(current, key=lambda item: item.x))
    return lines


def _match_column_role(text: str) -> ColumnRole | None:
    stripped = text.strip()
    if not stripped or len(stripped) > 48:
        return None
    for role, pattern in _HEADER_PATTERNS:
        if pattern.search(stripped):
            return role
    return None


def _merge_adjacent_header_tokens(line: list[OcrTokenBox]) -> list[OcrTokenBox]:
    """Merge tightly spaced word tokens on a single header row (e.g. 'Item' + 'Description', 'CGST' + '%')."""
    if len(line) <= 1:
        return line
    merged: list[OcrTokenBox] = [line[0].model_copy()]
    for tok in line[1:]:
        prev = merged[-1]
        gap = tok.x - prev.x_right
        prev_role = _match_column_role(prev.text)
        tok_role = _match_column_role(tok.text)
        if prev_role is not None and tok_role is not None and prev_role != tok_role:
            # Allow 'Unit' + 'MRP/RSP' -> 'mrp', 'Unit' + 'Price*' -> 'rate', 'CGST' + 'Rate(%)' -> 'cgst_pct'
            combined_test = f"{prev.text.strip()} {tok.text.strip()}"
            combined_role_test = _match_column_role(combined_test)
            if not (
                gap <= 0.015
                and combined_role_test in {"mrp", "unit_taxable", "rate", "cgst_pct", "sgst_pct", "igst_pct", "cess_pct", "discount_pct"}
            ):
                merged.append(tok.model_copy())
                continue
        combined = f"{prev.text.strip()} {tok.text.strip()}"
        combined_role = _match_column_role(combined)
        should_merge = (
            gap <= 0.008
            and combined_role is not None
            and tok_role is None
            and (prev_role is None or combined_role != prev_role)
        )
        if should_merge:
            new_x = min(prev.x, tok.x)
            new_right = max(prev.x_right, tok.x_right)
            prev.text = combined
            prev.x = new_x
            prev.w = max(0.001, new_right - new_x)
            prev.h = max(prev.h, tok.h)
            prev.confidence = min(prev.confidence, tok.confidence)
        else:
            merged.append(tok.model_copy())
    return merged


def _is_header_like_line(line: list[OcrTokenBox]) -> bool:
    if not line:
        return False
    # Reject lines with monetary decimals or 4-8 digit HSN codes
    for b in line:
        t = b.text.strip()
        if re.search(r"\d+[.,]\d{2}$", t) or _HSN_RE.match(t) or _SR_NO_RE.match(t):
            return False
    hits = 0
    for b in line:
        words = [w.strip(".,:*()") for w in b.text.lower().split()]
        if any(w in _HEADER_FRAGMENT_WORDS for w in words) or _match_column_role(b.text) is not None:
            hits += 1
    return hits >= 1 and hits >= max(1, len(line) // 2)


def _detect_multiline_header(
    lines: list[list[OcrTokenBox]],
    start_idx: int,
) -> tuple[list[_ColSpec], int]:
    """Inspect 1..3 consecutive header lines and vertically stack tokens by x-overlap into columns."""
    first_line = lines[start_idx]
    if not _is_header_like_line(first_line):
        return [], 0

    page = first_line[0].page
    header_rows: list[list[OcrTokenBox]] = [_merge_adjacent_header_tokens(first_line)]
    consumed_extra = 0
    for offset in (1, 2):
        if start_idx + offset >= len(lines):
            break
        nxt = lines[start_idx + offset]
        if nxt[0].page != page:
            break
        if nxt[0].y - header_rows[-1][0].y > 0.055:
            break
        if _is_header_like_line(nxt):
            header_rows.append(_merge_adjacent_header_tokens(nxt))
            consumed_extra += 1
        else:
            break

    # Stack tokens across header_rows into vertical header columns by x-overlap or x_center proximity
    stacks: list[list[OcrTokenBox]] = []
    for tok in header_rows[0]:
        stacks.append([tok])

    for extra_row in header_rows[1:]:
        for tok in extra_row:
            t_center = (tok.x + tok.x_right) / 2.0
            best_stack: list[OcrTokenBox] | None = None
            best_dist = 1.0
            for st in stacks:
                st_left = min(b.x for b in st)
                st_right = max(b.x_right for b in st)
                st_center = (st_left + st_right) / 2.0
                overlaps = min(st_right, tok.x_right) - max(st_left, tok.x) > 0.002
                dist = abs(st_center - t_center)
                if (overlaps or dist <= 0.035) and dist < best_dist:
                    best_dist = dist
                    best_stack = st
            if best_stack is not None:
                best_stack.append(tok)
            else:
                stacks.append([tok])

    cols: list[_ColSpec] = []
    seen_roles: set[ColumnRole] = set()
    for st in sorted(stacks, key=lambda s: sum((b.x + b.x_right) / 2.0 for b in s) / len(s)):
        combined_text = " ".join(b.text.strip() for b in st).strip()
        role = _match_column_role(combined_text)
        if role is None:
            continue
        # If a tax/discount column appears twice (e.g. 'CGST' then 'CGST Amt.'), reclassify the first as *_pct
        if role in seen_roles and role in {"cgst", "sgst", "igst", "cess", "discount"}:
            if re.search(r"\b(?:amt\.?|amount|val(?:ue)?)\b", combined_text, re.I) or "amt." in combined_text.lower():
                for prev_c in cols:
                    if prev_c.role == role:
                        prev_c.role = f"{role}_pct"  # type: ignore[assignment]
                        seen_roles.add(prev_c.role)
                        seen_roles.discard(role)
                        break
            else:
                continue
        elif role in seen_roles and role not in {"description"}:
            continue
        seen_roles.add(role)
        x_left = min(b.x for b in st)
        x_right = max(b.x_right for b in st)
        cols.append(
            _ColSpec(
                column_id=len(cols),
                role=role,
                header_text=combined_text,
                x_left=x_left,
                x_right=x_right,
                x_center=(x_left + x_right) / 2.0,
            )
        )
    return cols, consumed_extra


def _refine_column_bounds(
    cols: list[_ColSpec],
    body_lines: list[list[OcrTokenBox]],
    vertical_grid_xs: list[float] | None = None,
) -> list[_ColSpec]:
    """Infer column boundaries using visual vertical grid lines, header positions, and data alignment."""
    ordered = sorted(cols, key=lambda c: c.x_center)
    for idx, col in enumerate(ordered):
        col.column_id = idx

    numeric_roles = {
        "hsn",
        "qty",
        "mrp",
        "rate",
        "unit_taxable",
        "gross",
        "discount_pct",
        "discount",
        "other_charges",
        "taxable",
        "tax_pct",
        "cgst_pct",
        "cgst",
        "sgst_pct",
        "sgst",
        "igst_pct",
        "igst",
        "cess_pct",
        "cess",
        "gst",
        "total",
    }

    for idx in range(len(ordered) - 1):
        left_col = ordered[idx]
        right_col = ordered[idx + 1]
        default_mid = (left_col.x_center + right_col.x_center) / 2.0

        min_gx = max(left_col.x_left + 0.01, left_col.x_center + 0.01)
        if left_col.role == "description" and left_col.x_right < right_col.x_center - 0.01:
            min_gx = max(min_gx, left_col.x_right - 0.01)
        max_gx = min(right_col.x_right - 0.01, right_col.x_center - 0.01)
        grid_candidates = [
            gx
            for gx in (vertical_grid_xs or [])
            if min_gx <= gx <= max_gx
        ]
        if grid_candidates:
            boundary = min(grid_candidates, key=lambda gx: abs(gx - default_mid))
        elif left_col.role == "description" and right_col.role in numeric_roles:
            right_num_xs: list[float] = []
            for row in body_lines[:25]:
                for tok in row:
                    t_center = (tok.x + tok.x_right) / 2.0
                    if abs(t_center - right_col.x_center) <= 0.06 and (
                        _NUMERIC_ONLY_RE.match(tok.text.strip()) or _HSN_RE.match(tok.text.strip())
                    ):
                        right_num_xs.append(tok.x)
            if right_num_xs:
                min_num_x = min(right_num_xs)
                boundary = max(left_col.x_right + 0.005, min_num_x - 0.008)
                boundary = min(boundary, right_col.x_center)
            else:
                boundary = max(default_mid, left_col.x_right + 0.02)
        else:
            boundary = default_mid

        left_col.x_max = boundary
        right_col.x_min = boundary

    if ordered:
        ordered[0].x_min = 0.0
        ordered[-1].x_max = 1.0
    return ordered


def _assign_token_to_column(box: OcrTokenBox, cols: list[_ColSpec]) -> _ColSpec:
    center = (box.x + box.x_right) / 2.0
    assigned: _ColSpec | None = None
    for col in cols:
        if col.x_min <= center <= col.x_max:
            assigned = col
            break
    if assigned is None:
        assigned = min(cols, key=lambda c: abs(c.x_center - center))

    # A serial-number column never holds alphabetic description words (e.g. 'Pouch', 'Carton').
    if assigned.role == "sr_no" and any(ch.isalpha() for ch in box.text) and not _SR_NO_RE.match(box.text.strip()):
        desc_cols = [c for c in cols if c.role == "description"]
        if desc_cols:
            return min(desc_cols, key=lambda c: abs(c.x_center - center))
    return assigned


def _extract_cell_amount_paise(tokens: list[str], *, allow_signed: bool = False) -> int | None:
    if not tokens:
        return None
    joined = " ".join(tokens).strip()
    if not joined:
        return None
    joined = re.sub(r"(\d)\s*(?:₹|Rs\.?|INR|USD|EUR|GBP|AED|\$|€|£)\s*(\d)", r"\1 \2", joined, flags=re.I)
    non_pct_parts = [part for part in joined.split() if not _PCT_TOKEN_RE.match(part)]
    for candidate in reversed(non_pct_parts):
        parsed = parse_inr_to_paise(candidate, allow_signed=allow_signed)
        if parsed is not None:
            return parsed
    return None


def _extract_cell_pct(tokens: list[str]) -> float | None:
    joined = " ".join(tokens).strip()
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%?", joined)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def extract_product_attributes(name: str) -> tuple[str | None, str | None, str | None]:
    """Extract (brand, variant, pack_size) conservatively from product description."""
    pack_match = _PACK_SIZE_RE.search(name)
    pack_size = pack_match.group(1).strip() if pack_match else None

    variant_match = _VARIANT_PAREN_RE.search(name)
    variant = variant_match.group(1).strip() if variant_match else None
    if variant is None and " - " in name:
        tail = name.split(" - ", 1)[1].strip()
        if 2 <= len(tail) <= 35:
            variant = tail

    brand: str | None = None
    tokens = [tok.strip(",.;:-") for tok in name.split() if tok.strip(",.;:-")]
    if len(tokens) >= 3:
        first = tokens[0]
        if (
            len(first) >= 2
            and first[0].isupper()
            and first.isalpha()
            and first.lower() not in {"fresh", "organic", "pure", "mini", "pack", "combo"}
        ):
            brand = first
    return brand, variant, pack_size


def _parse_qty_and_unit(
    qty_tokens: list[str],
    unit_tokens: list[str],
    desc: str,
) -> tuple[float | None, str | None]:
    qty_text = " ".join(qty_tokens).strip()
    unit_text = " ".join(unit_tokens).strip() or None
    qty_val: float | None = None

    if qty_text:
        m = _QTY_UNIT_RE.search(qty_text)
        if m:
            qty_val = float(m.group("qty").replace(",", "."))
            if not unit_text:
                unit_text = m.group("unit")
        else:
            num_m = re.search(r"(\d+(?:[.,]\d+)?)", qty_text)
            if num_m:
                qty_val = float(num_m.group(1).replace(",", "."))

    if unit_text is None and desc:
        m_desc = _QTY_UNIT_RE.search(desc)
        if m_desc:
            desc_qty = float(m_desc.group("qty").replace(",", "."))
            if qty_val is None:
                qty_val = desc_qty
                unit_text = m_desc.group("unit")
            elif abs(desc_qty - qty_val) < 1e-6:
                unit_text = m_desc.group("unit")

    return qty_val, unit_text


@dataclass
class _ClassifiedLine:
    boxes: list[OcrTokenBox]
    page: int
    y_center: float
    text: str
    col_boxes: dict[int, list[OcrTokenBox]]
    role_boxes: dict[ColumnRole, list[OcrTokenBox]]
    desc_text: str
    is_anchor: bool
    is_numeric_subrow: bool
    is_row_terminator: bool


@dataclass
class _PendingRow:
    page: int
    cells: dict[int, list[str]] = field(default_factory=dict)
    cell_boxes: dict[int, list[OcrTokenBox]] = field(default_factory=dict)
    col_roles: dict[int, ColumnRole] = field(default_factory=dict)
    boxes: list[OcrTokenBox] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)

    def add_line(self, cl: _ClassifiedLine, cols_by_id: dict[int, _ColSpec]) -> None:
        for col_id, b_list in cl.col_boxes.items():
            col = cols_by_id[col_id]
            for b in b_list:
                self.cells.setdefault(col_id, []).append(b.text.strip())
                self.cell_boxes.setdefault(col_id, []).append(b)
                self.col_roles[col_id] = col.role
                self.boxes.append(b)
        self.raw_lines.append(cl.text)


def _compute_bbox(boxes: list[OcrTokenBox]) -> list[float] | None:
    if not boxes:
        return None
    xs = [b.x for b in boxes]
    ys = [b.y for b in boxes]
    x_rights = [b.x_right for b in boxes]
    y_bottoms = [b.y + b.h for b in boxes]
    return [
        round(min(xs), 4),
        round(min(ys), 4),
        round(max(x_rights) - min(xs), 4),
        round(max(y_bottoms) - min(ys), 4),
    ]


def _pending_to_ir_row(
    pending: _PendingRow,
    *,
    document_id: str,
    table_id: int,
    row_id: int,
    cols_by_id: dict[int, _ColSpec],
) -> TableRowIR:
    ir_cells: dict[int, TableCellIR] = {}
    for col_id, tokens in pending.cells.items():
        col = cols_by_id[col_id]
        c_boxes = sorted(
            pending.cell_boxes.get(col_id, []),
            key=lambda b: (b.page, round(b.y_center, 3), b.x),
        )
        avg_conf = sum(b.confidence for b in c_boxes) / len(c_boxes) if c_boxes else 1.0
        cell_text = " ".join(b.text.strip() for b in c_boxes if b.text.strip()) if c_boxes else " ".join(tokens).strip()
        ir_cells[col_id] = TableCellIR(
            document_id=document_id,
            page_number=pending.page,
            table_id=table_id,
            row_id=row_id,
            column_id=col_id,
            cell_text=cell_text,
            cell_bbox=_compute_bbox(c_boxes),
            associated_header=col.header_text,
            semantic_field=col.role,
            confidence=round(avg_conf, 3),
            source_ocr_evidence=cell_text,
        )
    return TableRowIR(
        row_id=row_id,
        page_number=pending.page,
        cells=ir_cells,
        raw_text="\n".join(pending.raw_lines).strip(),
        row_bbox=_compute_bbox(pending.boxes),
    )


def _ir_row_to_extracted_line(row: TableRowIR) -> ExtractedReceiptLine | None:
    by_role: dict[ColumnRole, list[str]] = {}
    confs: list[float] = []
    for cell in row.cells.values():
        by_role.setdefault(cell.semantic_field, []).append(cell.cell_text)
        confs.append(cell.confidence)

    # Description is reconstructed EXCLUSIVELY from cells assigned to 'description'
    desc = " ".join(by_role.get("description", [])).strip()
    desc = _ROW_TERMINATOR_RE.sub("", desc)
    desc = re.sub(r"\s{2,}", " ", desc).strip(" -|")
    if _SECTION_DIVIDER_RE.match(desc) or _EXPLICIT_CANCELLED_RE.match(desc) or _EXPLICIT_CANCELLED_RE.match(row.raw_text.strip()):
        return None
    if _TOTAL_FOOTER_RE.match(desc) or _TABLE_STOP_RE.search(desc) or _TABLE_STOP_RE.search(row.raw_text):
        return None

    recovered_from_misalignment = False
    if not desc:
        raw_first = row.raw_text.splitlines()[0].strip() if row.raw_text else ""
        alpha_prefix = re.match(r"^(?:\d+\s+)?([A-Za-z][A-Za-z0-9\s\-./()]{1,40}?)(?=\s+\d|\s*$)", raw_first)
        if alpha_prefix:
            desc = alpha_prefix.group(1).strip(" -|")
            recovered_from_misalignment = True
        if not desc:
            return None

    uncertain_crossout = False
    if _CROSSED_OUT_RE.match(desc) or _CROSSED_OUT_RE.match(row.raw_text.strip()):
        uncertain_crossout = True
        desc = re.sub(r"^[~\s]+", "", desc).strip()
        if not desc:
            return None

    sr_text = " ".join(by_role.get("sr_no", [])).strip()
    sr_match = _SR_NO_RE.match(sr_text)
    sr_no = int(sr_match.group(1)) if sr_match else None

    hsn_text = " ".join(by_role.get("hsn", [])).strip()
    hsn_match = re.search(r"\b(\d{4,8})\b", hsn_text)
    hsn_code = hsn_match.group(1) if hsn_match else None

    qty_val, unit_text = _parse_qty_and_unit(by_role.get("qty", []), by_role.get("unit", []), desc)
    if qty_val is None and by_role.get("total"):
        qty_val = 1.0

    mrp_paise = _extract_cell_amount_paise(by_role.get("mrp", []))
    rate_paise = _extract_cell_amount_paise(by_role.get("rate", []))
    unit_taxable_paise = _extract_cell_amount_paise(by_role.get("unit_taxable", []))
    gross_paise = _extract_cell_amount_paise(by_role.get("gross", []))
    other_charges_paise = _extract_cell_amount_paise(by_role.get("other_charges", []))
    discount_paise = _extract_cell_amount_paise(by_role.get("discount", []))
    discount_pct = _extract_cell_pct(by_role.get("discount_pct", []))
    taxable_paise = _extract_cell_amount_paise(by_role.get("taxable", []))
    cgst_paise = _extract_cell_amount_paise(by_role.get("cgst", []))
    if cgst_paise is None and by_role.get("cgst_pct"):
        cgst_paise = _extract_cell_amount_paise(by_role["cgst_pct"])
    sgst_paise = _extract_cell_amount_paise(by_role.get("sgst", []))
    if sgst_paise is None and by_role.get("sgst_pct"):
        sgst_paise = _extract_cell_amount_paise(by_role["sgst_pct"])
    igst_paise = _extract_cell_amount_paise(by_role.get("igst", []))
    if igst_paise is None and by_role.get("igst_pct"):
        igst_paise = _extract_cell_amount_paise(by_role["igst_pct"])
    cess_paise = _extract_cell_amount_paise(by_role.get("cess", []))
    if cess_paise is None and by_role.get("cess_pct"):
        cess_paise = _extract_cell_amount_paise(by_role["cess_pct"])
    total_paise = _extract_cell_amount_paise(by_role.get("total", []))

    if total_paise is None and taxable_paise is not None:
        tax_sum = sum(v for v in (cgst_paise, sgst_paise, igst_paise, cess_paise) if v is not None)
        total_paise = taxable_paise + tax_sum if tax_sum > 0 else taxable_paise

    if total_paise is None and rate_paise is None and mrp_paise is None:
        return None

    has_explicit_unit_price = rate_paise is not None
    suggested_unit_price_paise: int | None = None
    uncertain_fields: list[str] = []
    row_warnings: list[str] = []
    if recovered_from_misalignment:
        uncertain_fields.append("name")
        row_warnings.append(f"Description column was misaligned on '{desc}'; verify product name.")
    if uncertain_crossout:
        uncertain_fields.append("crossout")
        row_warnings.append(
            f"Possible crossed-out or handwritten mark on '{desc}'; verify whether this item was purchased."
        )
    if (
        has_explicit_unit_price
        and rate_paise is not None
        and total_paise is not None
        and qty_val
        and qty_val > 0
        and not discount_paise
        and not taxable_paise
    ):
        expected = round(qty_val * rate_paise)
        if abs(expected - total_paise) > max(200, int(0.05 * max(total_paise, 1))):
            suggested_unit_price_paise = round(total_paise / qty_val)
            uncertain_fields.append("unit_price")
            unit_lbl = unit_text or "unit"
            row_warnings.append(
                f"Rate uncertain on '{desc}': {qty_val:g} x ₹{rate_paise / 100:.2f} != ₹{total_paise / 100:.2f} "
                f"(implied rate: ₹{suggested_unit_price_paise / 100:.2f}/{unit_lbl})."
            )

    if rate_paise is None and total_paise is not None and qty_val and qty_val > 0:
        rate_paise = int(round(total_paise / qty_val))

    brand, variant, pack_size = extract_product_attributes(desc)
    normalized_qty, base_unit = normalize_metric_unit(qty_val, unit_text)
    if base_unit == "pcs" and qty_val and pack_size:
        from app.core.units import resolve_purchase_measurement

        try:
            res_qty, res_unit = resolve_purchase_measurement(qty_val, unit_text or "pcs", pack_size)
            normalized_qty, base_unit = float(res_qty), res_unit
        except ValueError:
            pass
    avg_conf = sum(confs) / len(confs) if confs else 0.85

    return ExtractedReceiptLine(
        sr_no=sr_no,
        raw_text=row.raw_text,
        name=desc,
        brand=brand,
        variant=variant,
        quantity=qty_val,
        unit=unit_text,
        normalized_qty=normalized_qty,
        base_unit=base_unit,
        pack_size=pack_size,
        unit_price_paise=rate_paise,
        suggested_unit_price_paise=suggested_unit_price_paise,
        has_explicit_unit_price=has_explicit_unit_price,
        mrp_paise=mrp_paise,
        unit_taxable_paise=unit_taxable_paise,
        gross_paise=gross_paise,
        other_charges_paise=other_charges_paise,
        taxable_paise=taxable_paise,
        cgst_paise=cgst_paise,
        sgst_paise=sgst_paise,
        igst_paise=igst_paise,
        cess_paise=cess_paise,
        hsn_code=hsn_code,
        total_paise=total_paise,
        discount_paise=discount_paise,
        discount_pct=discount_pct,
        confidence=round(avg_conf, 3),
        source_evidence=row.raw_text,
        source_page=row.page_number,
        source_bbox=row.row_bbox,
        needs_review=total_paise is None or avg_conf < 0.7 or bool(uncertain_fields),
        uncertain_fields=uncertain_fields,
        warnings=row_warnings,
    )


def _split_gap_lines_between_anchors(
    gap_lines: list[_ClassifiedLine],
    prev_anchor_y: float | None,
    next_anchor_y: float | None,
) -> tuple[list[_ClassifiedLine], list[_ClassifiedLine]]:
    """Assign wrapped lines between two numeric anchor rows using terminators and largest vertical y-gap."""
    if not gap_lines:
        return [], []
    if prev_anchor_y is None:
        return [], gap_lines
    if next_anchor_y is None:
        return gap_lines, []

    # 1. Pure numeric sub-rows immediately below prev_anchor belong to prev_anchor
    leading_numeric: list[_ClassifiedLine] = []
    rest = gap_lines
    while rest and rest[0].is_numeric_subrow:
        leading_numeric.append(rest[0])
        rest = rest[1:]
    if not rest:
        return leading_numeric, []

    # 2. Explicit row terminator (e.g. '7 Days Return') splits cleanly
    for i, gl in enumerate(rest):
        if gl.is_row_terminator:
            return leading_numeric + rest[: i + 1], rest[i + 1 :]

    # 3. Largest vertical y-gap among [prev_anchor, rest[0], ..., rest[-1], next_anchor]
    ys = [prev_anchor_y] + [gl.y_center for gl in rest] + [next_anchor_y]
    gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
    split_idx = max(range(len(gaps)), key=lambda i: gaps[i])
    return leading_numeric + rest[:split_idx], rest[split_idx:]


def extract_table_rows(
    boxes: list[OcrTokenBox],
    *,
    document_id: str = "doc_1",
    vertical_grid_xs: list[float] | None = None,
) -> TableExtractionResult:
    """Detect semantic invoice tables, build TableIR, and extract multi-line/multi-page item rows."""
    if not boxes:
        return TableExtractionResult()

    lines = _cluster_page_lines(boxes)
    tables: list[TableIR] = []
    active_table: TableIR | None = None
    active_cols: list[_ColSpec] | None = None
    active_cols_by_id: dict[int, _ColSpec] = {}
    in_table = False
    section_lines: list[_ClassifiedLine] = []
    extracted: list[ExtractedReceiptLine] = []
    has_table_headers = False
    detected_roles: list[ColumnRole] = []
    diagnostics: list[str] = []
    first_table_y: float | None = None

    def _flush_item_section() -> None:
        nonlocal section_lines
        if not section_lines or active_table is None or active_table.table_kind != "item_table":
            section_lines = []
            return

        # Segment section_lines around numeric anchor lines
        anchor_indices = [i for i, cl in enumerate(section_lines) if cl.is_anchor]
        if not anchor_indices:
            section_lines = []
            return

        pending_rows: list[_PendingRow] = [
            _PendingRow(page=section_lines[ai].page) for ai in anchor_indices
        ]
        # Leading lines before first anchor belong to row 0
        for cl in section_lines[: anchor_indices[0]]:
            pending_rows[0].add_line(cl, active_cols_by_id)

        for k, ai in enumerate(anchor_indices):
            pending_rows[k].add_line(section_lines[ai], active_cols_by_id)
            next_ai = anchor_indices[k + 1] if k + 1 < len(anchor_indices) else len(section_lines)
            gap = section_lines[ai + 1 : next_ai]
            if k + 1 < len(anchor_indices):
                to_prev, to_next = _split_gap_lines_between_anchors(
                    gap,
                    section_lines[ai].y_center,
                    section_lines[next_ai].y_center,
                )
                for cl in to_prev:
                    pending_rows[k].add_line(cl, active_cols_by_id)
                for cl in to_next:
                    pending_rows[k + 1].add_line(cl, active_cols_by_id)
            else:
                for cl in gap:
                    pending_rows[k].add_line(cl, active_cols_by_id)

        for p_row in pending_rows:
            ir_row = _pending_to_ir_row(
                p_row,
                document_id=document_id,
                table_id=active_table.table_id,
                row_id=len(active_table.rows),
                cols_by_id=active_cols_by_id,
            )
            item = _ir_row_to_extracted_line(ir_row)
            if item is not None:
                active_table.rows.append(ir_row)
                extracted.append(item)

        section_lines = []

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        line_text = " ".join(b.text.strip() for b in line if b.text.strip())
        page = line[0].page

        header_cols, extra_header_rows = _detect_multiline_header(lines, idx)
        distinct_roles = {c.role for c in header_cols}
        is_item_table_sig = (
            len(distinct_roles) >= 3
            and (
                "description" in distinct_roles
                or ("hsn" in distinct_roles and bool(distinct_roles & {"qty", "rate", "mrp", "gross", "discount"}))
            )
            and bool(distinct_roles & {"qty", "rate", "mrp", "taxable", "total"})
        )
        is_tax_summary_sig = (
            not is_item_table_sig
            and len(distinct_roles) >= 3
            and "description" not in distinct_roles
            and "qty" not in distinct_roles
            and "hsn" in distinct_roles
            and bool(distinct_roles & {"taxable", "cgst", "sgst", "igst"})
        )

        if is_item_table_sig or is_tax_summary_sig:
            _flush_item_section()
            if first_table_y is None and page == 1:
                first_table_y = line[0].y
            idx += extra_header_rows
            body_preview = lines[idx + 1 : idx + 25]
            refined_cols = _refine_column_bounds(header_cols, body_preview, vertical_grid_xs)
            new_roles = [c.role for c in refined_cols]

            if is_tax_summary_sig:
                tax_table = TableIR(
                    document_id=document_id,
                    table_id=len(tables),
                    table_kind="tax_summary_table",
                    page_numbers=[page],
                    columns=[
                        TableColumnIR(
                            column_id=c.column_id,
                            associated_header=c.header_text,
                            semantic_field=c.role,
                            x_min=c.x_min,
                            x_max=c.x_max,
                            x_center=c.x_center,
                        )
                        for c in refined_cols
                    ],
                    diagnostics=["tax_summary_table_skipped"],
                )
                tables.append(tax_table)
                diagnostics.append("tax_summary_table_skipped")
                active_table = tax_table
                active_cols = refined_cols
                active_cols_by_id = {c.column_id: c for c in refined_cols}
                in_table = True
                idx += 1
                continue

            if (
                active_table is not None
                and active_table.table_kind == "item_table"
                and [c.semantic_field for c in active_table.columns] == new_roles
            ):
                if page not in active_table.page_numbers:
                    active_table.page_numbers.append(page)
                active_cols = refined_cols
                active_cols_by_id = {c.column_id: c for c in refined_cols}
                in_table = True
                idx += 1
                continue

            active_cols = refined_cols
            active_cols_by_id = {c.column_id: c for c in refined_cols}
            active_table = TableIR(
                document_id=document_id,
                table_id=len(tables),
                table_kind="item_table",
                page_numbers=[page],
                columns=[
                    TableColumnIR(
                        column_id=c.column_id,
                        associated_header=c.header_text,
                        semantic_field=c.role,
                        x_min=c.x_min,
                        x_max=c.x_max,
                        x_center=c.x_center,
                    )
                    for c in refined_cols
                ],
            )
            tables.append(active_table)
            in_table = True
            has_table_headers = True
            detected_roles = new_roles
            idx += 1
            continue

        if not in_table or not active_cols or active_table is None:
            idx += 1
            continue

        if _TABLE_STOP_RE.search(line_text):
            _flush_item_section()
            in_table = False
            idx += 1
            continue

        if _SECTION_DIVIDER_RE.match(line_text) or _EXPLICIT_CANCELLED_RE.match(line_text):
            idx += 1
            continue

        role_boxes: dict[ColumnRole, list[OcrTokenBox]] = {}
        col_boxes: dict[int, list[OcrTokenBox]] = {}
        for box in line:
            col = _assign_token_to_column(box, active_cols)
            role_boxes.setdefault(col.role, []).append(box)
            col_boxes.setdefault(col.column_id, []).append(box)

        desc_text = " ".join(b.text.strip() for b in role_boxes.get("description", [])).strip()
        if _TOTAL_FOOTER_RE.match(desc_text):
            _flush_item_section()
            in_table = False
            idx += 1
            continue

        if active_table.table_kind != "item_table":
            p_tax = _PendingRow(page=page)
            cl_tax = _ClassifiedLine(
                boxes=line,
                page=page,
                y_center=sum(b.y_center for b in line) / len(line),
                text=line_text,
                col_boxes=col_boxes,
                role_boxes=role_boxes,
                desc_text=desc_text,
                is_anchor=False,
                is_numeric_subrow=False,
                is_row_terminator=False,
            )
            p_tax.add_line(cl_tax, active_cols_by_id)
            active_table.rows.append(
                _pending_to_ir_row(
                    p_tax,
                    document_id=document_id,
                    table_id=active_table.table_id,
                    row_id=len(active_table.rows),
                    cols_by_id=active_cols_by_id,
                )
            )
            idx += 1
            continue

        if page not in active_table.page_numbers:
            active_table.page_numbers.append(page)

        has_sr_no = any(_SR_NO_RE.match(b.text.strip()) for b in role_boxes.get("sr_no", []))
        has_hsn = any(_HSN_RE.match(b.text.strip()) for b in role_boxes.get("hsn", []))
        has_total = _extract_cell_amount_paise([b.text for b in role_boxes.get("total", [])]) is not None
        has_rate = _extract_cell_amount_paise([b.text for b in role_boxes.get("rate", [])]) is not None
        has_taxable = _extract_cell_amount_paise([b.text for b in role_boxes.get("taxable", [])]) is not None
        has_mrp = _extract_cell_amount_paise([b.text for b in role_boxes.get("mrp", [])]) is not None
        has_qty = any(re.search(r"\d", b.text) for b in role_boxes.get("qty", []))

        is_anchor = has_total or has_hsn or has_mrp or has_rate or has_taxable or (has_sr_no and has_qty)
        is_numeric_subrow = (
            not is_anchor
            and not desc_text
            and all(_NUMERIC_ONLY_RE.match(b.text.strip()) or b.text.strip() in {"+", "-", "|"} for b in line if b.text.strip())
        )
        is_row_terminator = bool(_ROW_TERMINATOR_RE.search(line_text))

        section_lines.append(
            _ClassifiedLine(
                boxes=line,
                page=page,
                y_center=sum(b.y_center for b in line) / len(line),
                text=line_text,
                col_boxes=col_boxes,
                role_boxes=role_boxes,
                desc_text=desc_text,
                is_anchor=is_anchor,
                is_numeric_subrow=is_numeric_subrow,
                is_row_terminator=is_row_terminator,
            )
        )
        idx += 1

    _flush_item_section()
    return TableExtractionResult(
        lines=extracted,
        tables=tables,
        has_table_headers=has_table_headers,
        detected_columns=detected_roles,
        diagnostics=diagnostics,
        first_table_y=first_table_y,
    )
