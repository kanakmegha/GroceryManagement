import {
  cleanPantryProductDisplayName,
  formatLotCountLabel,
  isExpiryBadgeRelevant,
} from '../pantryDisplayHelpers';

describe('Phase 5C Pantry display helpers', () => {
  it('cleans OCR noise and moves package sizes to secondary detail without guessing ingredients', () => {
    expect(cleanPantryProductDisplayName('Amul Gold Milk 1 L Pouch')).toEqual({
      title: 'Amul Gold Milk',
      packageDetail: '1 L Pouch',
    });
    expect(cleanPantryProductDisplayName('Eastern Chilli Powder 100 g')).toEqual({
      title: 'Eastern Chilli Powder',
      packageDetail: '100 g',
    });
    expect(cleanPantryProductDisplayName('• Chana Dal')).toEqual({
      title: 'Chana Dal',
      packageDetail: null,
    });
    expect(cleanPantryProductDisplayName('11 CHANA KALA')).toEqual({
      title: 'CHANA KALA',
      packageDetail: null,
    });
  });

  it('shows expiry badge only when expired or expiring soon', () => {
    expect(isExpiryBadgeRelevant('expired')).toBe(true);
    expect(isExpiryBadgeRelevant('soon')).toBe(true);
    expect(isExpiryBadgeRelevant('ok')).toBe(false);
    expect(isExpiryBadgeRelevant('none')).toBe(false);
  });

  it('formats lot counts cleanly', () => {
    expect(formatLotCountLabel(1)).toBe('1 lot');
    expect(formatLotCountLabel(7)).toBe('7 lots');
  });
});
