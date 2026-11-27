import {
  computeDraftFinancials,
  deriveTotalReviewState,
  formatSignedRupeesFromPaise,
  resolvePatchTotalAmountPaise,
} from '../purchaseFinancials';

describe('purchase total reconciliation review state', () => {
  const baseLines = [
    { localKey: '1', qtyText: '1', unitPriceRupeesText: '100.00', lineTotalRupeesText: '100.00' },
  ];

  it('allows confirmation with no warning on a normal reconciled receipt', () => {
    const financials = computeDraftFinancials({
      lines: baseLines,
      subtotalRupees: '100.00',
      discountRupees: '0',
      additionalChargesRupees: '0',
      cgstRupees: '0',
      sgstRupees: '0',
      igstRupees: '0',
      roundingRupees: '0',
      receiptTotalRupees: '100.00',
    });
    const state = deriveTotalReviewState({
      missingFields: [],
      receiptTotalRupees: '100.00',
      financials,
    });
    expect(state.isTotalReviewRequired).toBe(false);
    expect(state.canConfirm).toBe(true);
    expect(state.reason).toBeNull();
  });

  it('flags unresolved mismatch, exposes discrepancy, and blocks confirmation', () => {
    const financials = computeDraftFinancials({
      lines: baseLines,
      subtotalRupees: '100.00',
      discountRupees: '0',
      additionalChargesRupees: '0',
      cgstRupees: '0',
      sgstRupees: '0',
      igstRupees: '0',
      roundingRupees: '0',
      receiptTotalRupees: '150.00',
    });
    const state = deriveTotalReviewState({
      missingFields: ['total_amount'],
      receiptTotalRupees: '150.00',
      financials,
    });
    expect(state.isTotalReviewRequired).toBe(true);
    expect(state.canConfirm).toBe(false);
    expect(state.extractedTotalPaise).toBe(15000);
    expect(state.calculatedTotalPaise).toBe(10000);
    expect(state.discrepancyPaise).toBe(5000);
    expect(state.reason).toContain('disagrees');
  });

  it('handles negative calculated total (-₹218.01) without treating ₹0 as explicit total', () => {
    const financials = computeDraftFinancials({
      lines: baseLines,
      subtotalRupees: '100.00',
      discountRupees: '318.01',
      additionalChargesRupees: '0',
      cgstRupees: '0',
      sgstRupees: '0',
      igstRupees: '0',
      roundingRupees: '0',
      receiptTotalRupees: '0.00',
    });
    const state = deriveTotalReviewState({
      missingFields: ['total_amount'],
      receiptTotalRupees: '0.00',
      financials,
    });
    expect(state.isTotalReviewRequired).toBe(true);
    expect(state.canConfirm).toBe(false);
    expect(state.hasExplicitTotal).toBe(false);
    expect(state.extractedTotalPaise).toBeNull();
    expect(state.calculatedTotalPaise).toBe(-21801);
    expect(formatSignedRupeesFromPaise(state.calculatedTotalPaise)).toBe('-₹218.01');
    expect(state.reason).toContain('negative');
  });

  it('enables confirmation once backend clears total_amount from missing_fields', () => {
    const financials = computeDraftFinancials({
      lines: baseLines,
      subtotalRupees: '100.00',
      discountRupees: '0',
      additionalChargesRupees: '0',
      cgstRupees: '0',
      sgstRupees: '0',
      igstRupees: '0',
      roundingRupees: '0',
      receiptTotalRupees: '125.00',
    });
    const resolved = deriveTotalReviewState({
      missingFields: [],
      receiptTotalRupees: '125.00',
      financials,
    });
    expect(resolved.isTotalReviewRequired).toBe(false);
    expect(resolved.canConfirm).toBe(true);
    expect(resolved.extractedTotalPaise).toBe(12500);
  });

  it('omits total_amount_paise on PATCH unless explicitly edited, while preserving explicit 0', () => {
    // Untouched default ₹0.00 -> omitted so unrelated edits cannot clear total review
    expect(resolvePatchTotalAmountPaise('0.00', false)).toBeUndefined();
    expect(resolvePatchTotalAmountPaise('150.00', false)).toBeUndefined();

    // Explicitly entered ₹0.00 -> included as 0 paise
    expect(resolvePatchTotalAmountPaise('0.00', true)).toBe(0);
    expect(resolvePatchTotalAmountPaise('0', true)).toBe(0);

    // Manually corrected positive total -> included in paise
    expect(resolvePatchTotalAmountPaise('425.50', true)).toBe(42550);
  });
});
