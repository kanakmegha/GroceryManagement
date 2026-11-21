import type {
  ConfidenceLevel,
  ReplenishmentItem,
  ShoppingListSourceType,
} from '../../shared/api/types';

export function formatConfidenceLabel(confidence: ConfidenceLevel): string {
  switch (confidence) {
    case 'high':
      return 'High confidence';
    case 'medium':
      return 'Medium confidence';
    case 'low':
      return 'Low confidence';
    case 'insufficient_data':
      return 'Insufficient data';
  }
}

export function formatSourceLabel(source: ShoppingListSourceType): string {
  switch (source) {
    case 'depleted':
      return 'Depleted';
    case 'low_stock':
      return 'Low stock';
    case 'predicted_replenishment':
      return 'Predicted';
    case 'regular_purchase':
      return 'Regular';
    case 'recipe_missing':
      return 'Recipe';
    case 'manual':
      return 'Manual';
  }
}

export function mapRecommendationToShoppingSource(
  item: ReplenishmentItem
): ShoppingListSourceType {
  if (item.stock_status === 'depleted') return 'depleted';
  if (item.stock_status === 'low_stock') return 'low_stock';
  if (item.group === 'regular_purchases') return 'regular_purchase';
  return 'predicted_replenishment';
}

export function formatQuantityBasisLabel(
  basis: ReplenishmentItem['quantity_basis']
): string {
  switch (basis) {
    case 'consumption_prediction':
      return 'Consumption prediction';
    case 'historical_pattern':
      return 'Historical pattern';
    case 'user_target':
      return 'User target';
    case 'insufficient_data':
      return 'Insufficient data';
  }
}

export function isActionableReplenishmentItem(item: ReplenishmentItem): boolean {
  return (
    item.confidence === 'high' &&
    item.eligibility_status !== 'SUPPRESSED' &&
    item.recommended_qty != null &&
    item.recommended_qty > 0 &&
    item.evidence_basis !== 'insufficient_data' &&
    item.quantity_basis !== 'insufficient_data' &&
    !item.unit_mismatch
  );
}
