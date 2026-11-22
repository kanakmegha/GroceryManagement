"""Stage E: Deterministic receipt validation and arithmetic reconciliation."""

from __future__ import annotations

from app.infra.ocr.schemas import ExtractedReceipt, ValidationIssue

MATH_RECONCILIATION_TOLERANCE_PAISE = 200


def validate_extracted_receipt(receipt: ExtractedReceipt) -> ExtractedReceipt:
    """Run deterministic arithmetic, duplicate, and anomaly checks without mutating values."""
    issues: list[ValidationIssue] = list(receipt.validation_issues)
    warnings: list[str] = list(receipt.warnings)

    if receipt.adapter_used == "ocr_provider_unavailable":
        issues.append(
            ValidationIssue(
                code="OCR_PROVIDER_UNAVAILABLE",
                severity="error",
                message=(
                    "No OCR engine is available on this host. Install tesseract-ocr "
                    "(Linux/Docker), "
                    "run on macOS Apple Vision, or enable a cloud OCR/VLM provider."
                ),
                field="adapter_used",
            )
        )

    if not receipt.lines:
        if receipt.document_type == "bill_summary":
            issues.append(
                ValidationIssue(
                    code="BILL_SUMMARY_ONLY",
                    severity="warning",
                    message="Bill summary only — attach itemized invoice or add items.",
                    field="lines",
                )
            )
            if not any("bill summary" in w.lower() for w in warnings):
                warnings.append("Bill summary only — attach itemized invoice or add items.")
        else:
            issues.append(
                ValidationIssue(
                    code="NO_LINE_ITEMS",
                    severity="error",
                    message="No line items could be extracted; manual review required.",
                    field="lines",
                )
            )
            if not any("no line items" in w.lower() or "no text" in w.lower() for w in warnings):
                warnings.append("No line items could be extracted; manual review required.")

    # 1. Per-line math & decimal anomaly checks
    seen_lines: dict[tuple[str, int | None], int] = {}
    for idx, line in enumerate(receipt.lines):
        if (
            line.has_explicit_unit_price
            and line.quantity is not None
            and line.unit_price_paise is not None
            and line.total_paise is not None
        ):
            expected_line = round(line.quantity * line.unit_price_paise)
            net_expected = expected_line - (line.discount_paise or 0)
            candidates = [expected_line, net_expected]
            diff_line = min(
                abs(c - target)
                for c in candidates
                for target in (
                    [line.total_paise, line.taxable_paise]
                    if line.taxable_paise is not None
                    else [line.total_paise]
                )
            )
            if diff_line > max(100, int(0.03 * max(line.total_paise, 1))):
                # Check for 100x decimal-point placement error
                if line.total_paise > 0 and (
                    abs(expected_line - line.total_paise * 100) <= 200
                    or abs(expected_line * 100 - line.total_paise) <= 200
                ):
                    msg = f"Possible decimal placement error on '{line.name}'."
                    line.warnings.append(msg)
                    issues.append(
                        ValidationIssue(
                            code="DECIMAL_ANOMALY",
                            severity="warning",
                            message=msg,
                            line_index=idx,
                        )
                    )
                else:
                    msg = (
                        f"Line math mismatch on '{line.name}': "
                        f"{line.quantity} x ₹{line.unit_price_paise / 100:.2f} != "
                        f"₹{line.total_paise / 100:.2f}."
                    )
                    line.warnings.append(msg)
                    issues.append(
                        ValidationIssue(
                            code="LINE_MATH_MISMATCH",
                            severity="warning",
                            message=msg,
                            line_index=idx,
                        )
                    )
                line.needs_review = True

        if line.total_paise is None or line.total_paise == 0:
            line.needs_review = True
            issues.append(
                ValidationIssue(
                    code="MISSING_LINE_PRICE",
                    severity="warning",
                    message=f"Missing or zero price for '{line.name}'.",
                    line_index=idx,
                )
            )

        dup_key = ((line.normalized_name or line.name).strip().lower(), line.total_paise)
        if dup_key in seen_lines and dup_key[0]:
            msg = (
                f"Possible duplicate line item '{line.name}' (lines {seen_lines[dup_key] + 1} "
                f"and {idx + 1})."
            )
            warnings.append(msg)
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_LINE_ITEM",
                    severity="info",
                    message=msg,
                    line_index=idx,
                )
            )
        else:
            seen_lines[dup_key] = idx

        # Check for incomplete or numeric-only description
        if not any(ch.isalpha() for ch in (line.name or "")):
            msg = f"Incomplete product description on row {idx + 1}: '{line.name}'."
            line.warnings.append(msg)
            line.needs_review = True
            issues.append(
                ValidationIssue(
                    code="INCOMPLETE_PRODUCT_DESCRIPTION",
                    severity="warning",
                    message=msg,
                    line_index=idx,
                )
            )

        # Check if line total equals taxable value despite non-zero per-line tax columns
        line_tax_sum = sum(
            v
            for v in (line.cgst_paise, line.sgst_paise, line.igst_paise, line.cess_paise)
            if v is not None
        )
        if (
            line.taxable_paise is not None
            and line.total_paise is not None
            and line_tax_sum > 0
            and abs(line.total_paise - line.taxable_paise) <= 5
            and abs((line.taxable_paise + line_tax_sum) - line.total_paise) > 10
        ):
            msg = f"Line total for '{line.name}' equals taxable value despite non-zero tax columns."
            line.warnings.append(msg)
            line.needs_review = True
            issues.append(
                ValidationIssue(
                    code="TOTAL_EQUALS_TAXABLE_WITH_TAX",
                    severity="warning",
                    message=msg,
                    line_index=idx,
                )
            )

        # Check 100x decimal-drop OCR anomaly against taxable + taxes (e.g. '18164' -> 1816400 vs
        # 181.64)
        if (
            line.taxable_paise is not None
            and line.total_paise is not None
            and line.total_paise >= 10000
            and line.taxable_paise > 0
        ):
            expected_with_tax = line.taxable_paise + line_tax_sum
            if abs((line.total_paise / 100.0) - expected_with_tax) <= max(
                100, int(0.05 * expected_with_tax)
            ):
                msg = (
                    f"Possible 100x decimal-drop error on '{line.name}' total "
                    f"(₹{line.total_paise / 100:.2f} vs taxable+tax "
                    f"₹{expected_with_tax / 100:.2f})."
                )
                line.warnings.append(msg)
                warnings.append(msg)
                line.needs_review = True
                issues.append(
                    ValidationIssue(
                        code="DECIMAL_ANOMALY",
                        severity="warning",
                        message=msg,
                        line_index=idx,
                    )
                )

    # 1b. Discontinuous serial number check
    serial_nums = [line.sr_no for line in receipt.lines if line.sr_no is not None]
    if len(serial_nums) >= 2:
        expected_seq = list(range(serial_nums[0], serial_nums[0] + len(serial_nums)))
        if serial_nums != expected_seq:
            msg = (
                f"Discontinuous serial numbers detected ({serial_nums}); some product rows "
                "may be missing."
            )
            warnings.append(msg)
            issues.append(
                ValidationIssue(
                    code="DISCONTINUOUS_SERIAL_NUMBERS",
                    severity="warning",
                    message=msg,
                    field="lines",
                )
            )

    # 1c. Incomplete extraction detection (distinguish OCR failure from genuine 1-item receipt)
    incomplete_reasons: list[str] = []
    if (
        receipt.candidate_row_count is not None
        and receipt.candidate_row_count >= 3
        and len(receipt.lines) < max(2, int(receipt.candidate_row_count * 0.5))
    ):
        incomplete_reasons.append(
            f"recovered only {len(receipt.lines)} of ~{receipt.candidate_row_count} candidate rows"
        )
    if (
        len(receipt.lines) == 1
        and receipt.printed_item_qty is not None
        and receipt.printed_item_qty >= 5
        and (receipt.lines[0].quantity or 0) < receipt.printed_item_qty * 0.5
    ):
        incomplete_reasons.append(
            f"extracted 1 item (qty {receipt.lines[0].quantity or 0:g}) vs printed item "
            f"qty {receipt.printed_item_qty:g}"
        )
    if (
        len(receipt.lines) == 1
        and receipt.grand_total_paise is not None
        and receipt.grand_total_paise > 0
        and receipt.lines[0].total_paise is not None
        and receipt.lines[0].total_paise < receipt.grand_total_paise * 0.3
    ):
        incomplete_reasons.append(
            f"single item ₹{receipt.lines[0].total_paise / 100:.2f} is far below printed "
            f"total ₹{receipt.grand_total_paise / 100:.2f}"
        )

    is_incomplete_extraction = bool(incomplete_reasons)
    if is_incomplete_extraction:
        msg = (
            f"Incomplete extraction ({'; '.join(incomplete_reasons)}). "
            "Retry upload or perform targeted review before confirming."
        )
        warnings.append(msg)
        issues.append(
            ValidationIssue(
                code="INCOMPLETE_EXTRACTION",
                severity="error",
                message=msg,
                field="lines",
            )
        )

    for w in warnings:
        if "handwritten/ambiguous annotation" in w.lower():
            issues.append(
                ValidationIssue(
                    code="ANNOTATED_GRAND_TOTAL",
                    severity="warning",
                    message=w,
                    field="grand_total_paise",
                )
            )
            break

    # 2. Line sum vs subtotal reconciliation
    lines_sum = sum(line.total_paise or 0 for line in receipt.lines)
    if receipt.subtotal_paise is not None and receipt.lines:
        sub_diff = abs(lines_sum - receipt.subtotal_paise)
        if sub_diff > MATH_RECONCILIATION_TOLERANCE_PAISE:
            msg = (
                f"Subtotal mismatch: lines sum ₹{lines_sum / 100:.2f} vs "
                f"printed subtotal ₹{receipt.subtotal_paise / 100:.2f}."
            )
            warnings.append(msg)
            issues.append(
                ValidationIssue(
                    code="SUBTOTAL_MISMATCH",
                    severity="warning",
                    message=msg,
                    field="subtotal_paise",
                )
            )

    # 3. Grand total reconciliation
    # Note: on some Indian receipts & GST invoices, line totals include tax or subtotal == lines_sum
    base_subtotal = receipt.subtotal_paise if receipt.subtotal_paise is not None else lines_sum
    extra_charges = receipt.additional_charges_paise or 0
    if extra_charges == 0 and (receipt.handling_charges_paise or receipt.delivery_charges_paise):
        extra_charges = (receipt.handling_charges_paise or 0) + (
            receipt.delivery_charges_paise or 0
        )
    total_discounts = receipt.discount_paise or 0
    if total_discounts == 0 and receipt.loyalty_discount_paise:
        total_discounts = receipt.loyalty_discount_paise
    adjustments = (receipt.rounding_paise or 0) + (receipt.goodwill_paise or 0)
    total_taxes = (receipt.tax_paise or 0) + (receipt.cess_paise or 0)

    computed_total = base_subtotal + total_taxes + extra_charges - total_discounts + adjustments
    computed_inclusive = base_subtotal + extra_charges - total_discounts + adjustments
    receipt.computed_total_paise = None if is_incomplete_extraction else computed_total

    math_reconciled = False
    if not is_incomplete_extraction and receipt.grand_total_paise is not None and receipt.lines:
        diff_with_tax = abs(computed_total - receipt.grand_total_paise)
        diff_inclusive = abs(computed_inclusive - receipt.grand_total_paise)
        if diff_with_tax <= MATH_RECONCILIATION_TOLERANCE_PAISE:
            math_reconciled = True
        elif total_taxes and diff_inclusive <= MATH_RECONCILIATION_TOLERANCE_PAISE:
            # Tax-inclusive line items (common in Indian retail MRP receipts & GST tables)
            math_reconciled = True
            receipt.computed_total_paise = computed_inclusive
        else:
            msg = (
                f"Math mismatch: computed ₹{computed_total / 100:.2f} vs "
                f"printed total ₹{receipt.grand_total_paise / 100:.2f}."
            )
            warnings.append(msg)
            issues.append(
                ValidationIssue(
                    code="GRAND_TOTAL_MISMATCH",
                    severity="warning",
                    message=msg,
                    field="grand_total_paise",
                )
            )
    elif receipt.grand_total_paise is None and receipt.lines:
        issues.append(
            ValidationIssue(
                code="MISSING_GRAND_TOTAL",
                severity="info",
                message=(
                    "Printed grand total was not detected; using computed total for draft review."
                ),
                field="grand_total_paise",
            )
        )
        warnings.append("Printed grand total was not detected; review before confirming.")

    # 4. Overall confidence & needs_review
    line_confs = [line.confidence for line in receipt.lines]
    avg_line_conf = sum(line_confs) / len(line_confs) if line_confs else 0.0
    if receipt.adapter_used == "ocr_provider_unavailable":
        issues.append(
            ValidationIssue(
                code="OCR_PROVIDER_UNAVAILABLE",
                severity="error",
                message=(
                    "No OCR engine is available on this host. Install tesseract-ocr "
                    "(Linux/Docker), "
                    "run on macOS Apple Vision, or enable a cloud OCR/VLM provider."
                ),
                field="adapter_used",
            )
        )

    if not receipt.lines:
        overall_conf = 0.0
    elif is_incomplete_extraction:
        overall_conf = min(0.35, avg_line_conf * 0.5)
    elif math_reconciled:
        overall_conf = min(0.99, max(receipt.confidence, avg_line_conf))
    else:
        overall_conf = max(0.25, min(0.74, avg_line_conf * 0.85))

    has_line_review = any(line.needs_review for line in receipt.lines)
    has_blocking_issue = any(issue.severity in ("warning", "error") for issue in issues)
    quality_unreadable = (
        receipt.quality_report is not None and not receipt.quality_report.is_readable
    )

    receipt.math_reconciled = math_reconciled
    receipt.confidence = round(overall_conf, 2)
    receipt.validation_issues = issues
    receipt.warnings = list(dict.fromkeys(warnings))
    receipt.needs_review = (
        (not receipt.lines)
        or (not math_reconciled)
        or has_line_review
        or has_blocking_issue
        or quality_unreadable
        or overall_conf < 0.75
    )
    return receipt
