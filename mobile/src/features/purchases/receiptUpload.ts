export interface ReceiptAssetInput {
  uri: string;
  name?: string | null;
  mimeType?: string | null;
}

export interface ExpoBytesFormDataPart {
  name: string;
  type: string;
  bytes: () => Promise<Uint8Array>;
}

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

const ALLOWED_MIME_TYPES = new Set([
  'image/jpeg',
  'image/png',
  'image/webp',
  'application/pdf',
  'text/plain',
]);

export function resolveReceiptMimeType(
  rawMimeType: string | null | undefined,
  filename: string
): string {
  const normalized = (rawMimeType ?? '').trim().toLowerCase();
  if (normalized === 'image/jpg') {
    return 'image/jpeg';
  }
  if (ALLOWED_MIME_TYPES.has(normalized)) {
    return normalized;
  }

  const lowerName = filename.toLowerCase();
  if (lowerName.endsWith('.pdf')) return 'application/pdf';
  if (lowerName.endsWith('.png')) return 'image/png';
  if (lowerName.endsWith('.webp')) return 'image/webp';
  if (lowerName.endsWith('.txt')) return 'text/plain';
  if (lowerName.endsWith('.jpg') || lowerName.endsWith('.jpeg')) return 'image/jpeg';

  return normalized || 'application/octet-stream';
}

export function validateReceiptAsset(asset: ReceiptAssetInput): {
  uri: string;
  name: string;
  mimeType: string;
} {
  const uri = (asset.uri ?? '').trim();
  if (!uri) {
    throw new Error('Selected receipt file has no readable URI.');
  }

  const fallbackExt = asset.mimeType === 'application/pdf' ? 'pdf' : 'jpg';
  const name = (asset.name ?? '').trim() || `receipt-${Date.now()}.${fallbackExt}`;
  const mimeType = resolveReceiptMimeType(asset.mimeType, name);

  if (!ALLOWED_MIME_TYPES.has(mimeType)) {
    throw new Error(
      `Unsupported receipt file type (${mimeType}). Please select a JPEG, PNG, WEBP, or PDF file.`
    );
  }

  return { uri, name, mimeType };
}

export async function buildReceiptFormData(
  asset: ReceiptAssetInput,
  fetchImpl: typeof fetch = fetch
): Promise<FormData> {
  const validated = validateReceiptAsset(asset);

  const localResponse = await fetchImpl(validated.uri);
  if (!localResponse.ok && localResponse.status !== 0) {
    throw new Error(`Unable to read selected receipt file (status ${localResponse.status}).`);
  }

  const buffer = await localResponse.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  if (bytes.byteLength === 0) {
    throw new Error('Selected receipt file is empty.');
  }
  if (bytes.byteLength > MAX_UPLOAD_BYTES) {
    throw new Error('Receipt file exceeds the 10 MB upload limit.');
  }

  const formData = new FormData();
  const hasReactNativeGetParts =
    typeof (formData as unknown as { getParts?: unknown }).getParts === 'function';

  if (hasReactNativeGetParts) {
    // Expo SDK 56-58's expo/fetch convertFormDataAsync rejects { uri, name, type }
    // and requires either a Blob or an object implementing { name, type, bytes() }.
    const part: ExpoBytesFormDataPart = {
      name: validated.name,
      type: validated.mimeType,
      bytes: async () => bytes,
    };
    formData.append('file', part as unknown as Blob);
  } else {
    const blob = new Blob([bytes], { type: validated.mimeType });
    formData.append('file', blob, validated.name);
  }

  return formData;
}

export async function buildMultiReceiptFormData(
  assets: ReceiptAssetInput[],
  purchaseId?: string | null,
  fetchImpl: typeof fetch = fetch
): Promise<FormData> {
  if (!assets || assets.length === 0) {
    throw new Error('At least one receipt file is required.');
  }
  const formData = new FormData();
  const hasReactNativeGetParts =
    typeof (formData as unknown as { getParts?: unknown }).getParts === 'function';

  if (purchaseId) {
    formData.append('purchase_id', purchaseId);
  }

  for (const asset of assets) {
    const validated = validateReceiptAsset(asset);
    const localResponse = await fetchImpl(validated.uri);
    if (!localResponse.ok && localResponse.status !== 0) {
      throw new Error(`Unable to read selected receipt file (status ${localResponse.status}).`);
    }
    const buffer = await localResponse.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    if (bytes.byteLength === 0) {
      throw new Error(`Selected receipt file (${validated.name}) is empty.`);
    }
    if (bytes.byteLength > MAX_UPLOAD_BYTES) {
      throw new Error(`Receipt file (${validated.name}) exceeds the 10 MB upload limit.`);
    }

    if (hasReactNativeGetParts) {
      const part: ExpoBytesFormDataPart = {
        name: validated.name,
        type: validated.mimeType,
        bytes: async () => bytes,
      };
      formData.append('files', part as unknown as Blob);
    } else {
      const blob = new Blob([bytes], { type: validated.mimeType });
      formData.append('files', blob, validated.name);
    }
  }

  return formData;
}
