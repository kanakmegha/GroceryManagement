"""Structured intermediate representation (IR) for layout-aware table reconstruction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ColumnRole = Literal[
    "sr_no",
    "hsn",
    "description",
    "qty",
    "unit",
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
    "unmapped",
]


class TableColumnIR(BaseModel):
    """Detected semantic column with inferred visual boundaries."""

    column_id: int
    associated_header: str
    semantic_field: ColumnRole
    x_min: float
    x_max: float
    x_center: float
    alignment: Literal["left", "right", "center"] = "left"


class TableCellIR(BaseModel):
    """Single cell inside the intermediate table representation."""

    document_id: str
    page_number: int
    table_id: int
    row_id: int
    column_id: int
    cell_text: str
    cell_bbox: list[float] | None = None
    associated_header: str
    semantic_field: ColumnRole
    confidence: float = 1.0
    source_ocr_evidence: str = ""


class TableRowIR(BaseModel):
    """Logical table row (potentially reconstructed from multiple visual lines)."""

    row_id: int
    page_number: int
    cells: dict[int, TableCellIR] = Field(default_factory=dict)
    raw_text: str = ""
    row_bbox: list[float] | None = None


class TableIR(BaseModel):
    """Complete intermediate table structure with diagnostics."""

    document_id: str
    table_id: int
    table_kind: Literal["item_table", "tax_summary_table", "payment_table", "unknown"] = "item_table"
    page_numbers: list[int] = Field(default_factory=list)
    columns: list[TableColumnIR] = Field(default_factory=list)
    rows: list[TableRowIR] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
