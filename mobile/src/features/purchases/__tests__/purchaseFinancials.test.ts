import {
  computeDraftFinancials,
  computeLineTotalFromQtyAndRate,
  filterLiveOcrWarnings,
} from '../purchaseFinancials';

describe('Purchase draft single-source-of-truth financials', () => {
  it('recomputes line total when qty or unit price changes', () => {
    expect(computeLineTotalFromQtyAndRate('0.5', '320.00')).toBe('160.00');
    expect(computeLineTotalFromQtyAndRate('0.75', '1180.00')).toBe('885.00');
    expect(computeLineTotalFromQtyAndRate('10', '35.00')).toBe('350.00');
  });

  it('derives missing subtotal from item sum instead of ₹0.00 and reconciles AGGARWAL TRADERS ₹3,459', () => {
    const res = computeDraftFinancials({
      lines: [
        { localKey: '1', qtyText: '0.5', unitPriceRupeesText: '320.00', lineTotalRupeesText: '160.00' },
        { localKey: '2', qtyText: '10', unitPriceRupeesText: '40.00', lineTotalRupeesText: '400.00' },
        { localKey: '3', qtyText: '10', unitPriceRupeesText: '35.00', lineTotalRupeesText: '350.00' },
        { localKey: '4', qtyText: '0.75', unitPriceRupeesText: '1180.00', lineTotalRupeesText: '885.00' },
        { localKey: '5', qtyText: '1', unitPriceRupeesText: '1000.00', lineTotalRupeesText: '1000.00' },
        { localKey: '6', qtyText: '1', unitPriceRupeesText: '94.00', lineTotalRupeesText: '94.00' },
        { localKey: '7', qtyText: '3', unitPriceRupeesText: '100.00', lineTotalRupeesText: '300.00' },
        { localKey: '8', qtyText: '0.5', unitPriceRupeesText: '120.00', lineTotalRupeesText: '60.00' },
        { localKey: '9', qtyText: '1', unitPriceRupeesText: '120.00', lineTotalRupeesText: '120.00' },
        { localKey: '10', qtyText: '1', unitPriceRupeesText: '90.00', lineTotalRupeesText: '90.00' },
      ],
      subtotalRupees: '',
      discountRupees: '0.00',
      additionalChargesRupees: '0.00',
      cgstRupees: '0.00',
      sgstRupees: '0.00',
      igstRupees: '0.00',
      roundingRupees: '0.00',
      receiptTotalRupees: '3459.00',
    });

    expect(res.itemsSumPaise).toBe(345900);
    expect(res.subtotalIsDerived).toBe(true);
    expect(res.effectiveSubtotalPaise).toBe(345900);
    expect(res.computedGrandTotalPaise).toBe(345900);
    expect(res.mismatchPaise).toBe(0);
    expect(res.hasMathMismatch).toBe(false);
    expect(res.mismatchedLineKeys).toEqual([]);
  });

  it('identifies specific mismatched lines and filters stale static OCR math warnings', () => {
    const res = computeDraftFinancials({
      lines: [
        { localKey: 'bad', qtyText: '1', unitPriceRupeesText: '110.00', lineTotalRupeesText: '110110.00' },
        { localKey: 'good', qtyText: '1', unitPriceRupeesText: '90.00', lineTotalRupeesText: '90.00' },
      ],
      subtotalRupees: '',
      discountRupees: '0.00',
      additionalChargesRupees: '0.00',
      cgstRupees: '0.00',
      sgstRupees: '0.00',
      igstRupees: '0.00',
      roundingRupees: '0.00',
      receiptTotalRupees: '200.00',
    });
    expect(res.mismatchedLineKeys).toEqual(['bad']);
    expect(res.hasMathMismatch).toBe(true);

    expect(
      filterLiveOcrWarnings([
        'Math mismatch: computed ₹112166.00 vs printed total ₹2260.00.',
        'Item 4 needs review.',
      ])
    ).toEqual(['Item 4 needs review.']);
  });
});
