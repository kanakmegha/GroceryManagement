export interface CleanedProductDisplay {
  title: string;
  packageDetail: string | null;
}

const PACK_SIZE_RE =
  /\b(\d+(?:\.\d+)?\s*(?:kg|g|gm|gms|mg|l|lt|ltr|ml|pc|pcs|pack|packs|pouch|carton|jar|bottle|tin|box)(?:\s+(?:pouch|carton|jar|bottle|tin|box))?)\b/gi;

const LEADING_OCR_NOISE_RE = /^[\s•*°\-_]+/;
const LEADING_SINGLE_DIGIT_NOISE_RE = /^\d{1,2}\s+(?=[A-Za-z])/;

/**
 * Clean a canonical product name for user-facing Pantry display without
 * guessing or substituting an ingredient identity.
 */
export function cleanPantryProductDisplayName(rawName: string): CleanedProductDisplay {
  let cleaned = (rawName || '').replace(LEADING_OCR_NOISE_RE, '').trim();
  cleaned = cleaned.replace(LEADING_SINGLE_DIGIT_NOISE_RE, '').trim();
  cleaned = cleaned.replace(/\s+/g, ' ');

  const packMatches: string[] = [];
  const withoutPack = cleaned
    .replace(PACK_SIZE_RE, (match) => {
      packMatches.push(match.trim());
      return ' ';
    })
    .replace(/\s*[-,|/]+\s*$/g, '')
    .replace(/\s+/g, ' ')
    .trim();

  const title = withoutPack.length >= 2 ? withoutPack : cleaned;
  const packageDetail = packMatches.length > 0 ? packMatches.join(' · ') : null;
  return { title, packageDetail };
}

export function isExpiryBadgeRelevant(tone: 'expired' | 'soon' | 'ok' | 'none'): boolean {
  return tone === 'expired' || tone === 'soon';
}

export function formatLotCountLabel(lotCount: number): string {
  return `${lotCount} ${lotCount === 1 ? 'lot' : 'lots'}`;
}
