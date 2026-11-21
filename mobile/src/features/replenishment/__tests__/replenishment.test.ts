import {
  formatConfidenceLabel,
  formatQuantityBasisLabel,
  formatSourceLabel,
  isActionableReplenishmentItem,
  mapRecommendationToShoppingSource,
} from '../replenishmentHelpers';
import type { ReplenishmentItem } from '../../../shared/api/types';

describe('Smart Replenishment & Shopping List helpers', () => {
  it('formats confidence, source, and quantity basis labels accurately', () => {
    expect(formatConfidenceLabel('high')).toBe('High confidence');
    expect(formatConfidenceLabel('medium')).toBe('Medium confidence');
    expect(formatConfidenceLabel('low')).toBe('Low confidence');
    expect(formatConfidenceLabel('insufficient_data')).toBe('Insufficient data');

    expect(formatSourceLabel('depleted')).toBe('Depleted');
    expect(formatSourceLabel('recipe_missing')).toBe('Recipe');
    expect(formatSourceLabel('predicted_replenishment')).toBe('Predicted');
    expect(formatSourceLabel('manual')).toBe('Manual');

    expect(formatQuantityBasisLabel('consumption_prediction')).toBe('Consumption prediction');
    expect(formatQuantityBasisLabel('historical_pattern')).toBe('Historical pattern');
    expect(formatQuantityBasisLabel('insufficient_data')).toBe('Insufficient data');
  });

  it('maps replenishment items to structured shopping-list sources', () => {
    const baseItem: ReplenishmentItem = {
      canonical_item_id: 'c1',
      item_name: 'Toned Milk',
      category: 'dairy',
      base_unit: 'ml',
      group: 'buy_now',
      stock_status: 'depleted',
      available_qty: 0,
      reserved_qty: 0,
      usable_qty: 0,
      expired_qty: 0,
      low_stock_threshold_qty: 0,
      purchase_count: 5,
      quantity_basis: 'consumption_prediction',
      unit_mismatch: false,
      confidence: 'high',
      confidence_reason: 'Consistent pattern',
      reason: 'Depleted',
      dismissal_count: 0,
    };

    expect(mapRecommendationToShoppingSource(baseItem)).toBe('depleted');
    expect(
      mapRecommendationToShoppingSource({ ...baseItem, stock_status: 'low_stock' })
    ).toBe('low_stock');
    expect(
      mapRecommendationToShoppingSource({
        ...baseItem,
        group: 'buy_soon',
        stock_status: 'running_out_soon',
      })
    ).toBe('predicted_replenishment');
    expect(
      mapRecommendationToShoppingSource({
        ...baseItem,
        group: 'regular_purchases',
        stock_status: 'adequate',
      })
    ).toBe('regular_purchase');
  });

  it('allows only HIGH-confidence eligible items with positive recommended_qty', () => {
    const eligible: ReplenishmentItem = {
      canonical_item_id: 'c1',
      item_name: 'Milk',
      category: 'dairy',
      base_unit: 'ml',
      group: 'buy_now',
      stock_status: 'low_stock',
      available_qty: 500,
      reserved_qty: 0,
      usable_qty: 500,
      expired_qty: 0,
      low_stock_threshold_qty: 0,
      purchase_count: 6,
      recommended_qty: 1000,
      recommended_unit: 'ml',
      quantity_basis: 'consumption_prediction',
      evidence_basis: 'consumption_based',
      eligibility_status: 'ELIGIBLE',
      unit_mismatch: false,
      confidence: 'high',
      confidence_reason: 'Consistent pattern',
      reason: 'Running low',
      dismissal_count: 0,
    };

    expect(isActionableReplenishmentItem(eligible)).toBe(true);
    expect(isActionableReplenishmentItem({ ...eligible, confidence: 'medium' })).toBe(false);
    expect(isActionableReplenishmentItem({ ...eligible, confidence: 'low' })).toBe(false);
    expect(isActionableReplenishmentItem({ ...eligible, confidence: 'insufficient_data' })).toBe(false);
    expect(isActionableReplenishmentItem({ ...eligible, eligibility_status: 'SUPPRESSED' })).toBe(false);
    expect(isActionableReplenishmentItem({ ...eligible, recommended_qty: 0 })).toBe(false);
    expect(isActionableReplenishmentItem({ ...eligible, unit_mismatch: true })).toBe(false);
  });
});
