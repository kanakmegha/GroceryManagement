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
export const RECEIPT_OCR_TIMEOUT_MS = 60_000;
export const MAX_IMAGE_LONG_EDGE_PX = 1600;
export const JPEG_COMPRESSION_QUALITY = 0.8;
export const SMALL_JPEG_SKIP_BYTES = 300 * 1024;

const COMPRESSIBLE_IMAGE_MIME_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);

export interface CompressedReceiptResult {
  bytes: Uint8Array;
  mimeType: string;
  name: string;
  compressed: boolean;
}

export type ImageCompressorFn = (input: {
  bytes: Uint8Array;
  mimeType: string;
  name: string;
  maxLongEdgePx: number;
  quality: number;
}) => Promise<CompressedReceiptResult | null>;

function replaceExtensionWithJpg(filename: string): string {
  const trimmed = filename.trim();
  if (!trimmed) return `receipt-${Date.now()}.jpg`;
  const dotIdx = trimmed.lastIndexOf('.');
  if (dotIdx <= 0) return `${trimmed}.jpg`;
  return `${trimmed.slice(0, dotIdx)}.jpg`;
}

export function computeScaledDimensions(
  width: number,
  height: number,
  maxLongEdgePx: number = MAX_IMAGE_LONG_EDGE_PX
): { width: number; height: number; resized: boolean } {
  if (width <= 0 || height <= 0) {
    return { width, height, resized: false };
  }
  const longEdge = Math.max(width, height);
  if (longEdge <= maxLongEdgePx) {
    return { width, height, resized: false };
  }
  const scale = maxLongEdgePx / longEdge;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
    resized: true,
  };
}

async function compressImageInBrowser(input: {
  bytes: Uint8Array;
  mimeType: string;
  name: string;
  maxLongEdgePx: number;
  quality: number;
}): Promise<CompressedReceiptResult | null> {
  if (typeof document === 'undefined' || typeof Blob === 'undefined') {
    return null;
  }

  const blob = new Blob([input.bytes as BlobPart], { type: input.mimeType });
  let sourceWidth = 0;
  let sourceHeight = 0;
  let drawable: CanvasImageSource | null = null;
  let cleanup: (() => void) | null = null;

  try {
    if (typeof createImageBitmap === 'function') {
      const bitmap = await createImageBitmap(blob);
      sourceWidth = bitmap.width;
      sourceHeight = bitmap.height;
      drawable = bitmap;
      cleanup = () => bitmap.close();
    } else if (typeof Image !== 'undefined' && typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
      const objectUrl = URL.createObjectURL(blob);
      cleanup = () => URL.revokeObjectURL(objectUrl);
      const img = await new Promise<HTMLImageElement>((resolve, reject) => {
        const el = new Image();
        el.onload = () => resolve(el);
        el.onerror = () => reject(new Error('Image decode failed'));
        el.src = objectUrl;
      });
      sourceWidth = img.naturalWidth || img.width;
      sourceHeight = img.naturalHeight || img.height;
      drawable = img;
    } else {
      return null;
    }

    if (!drawable || sourceWidth <= 0 || sourceHeight <= 0) {
      return null;
    }

    const scaled = computeScaledDimensions(sourceWidth, sourceHeight, input.maxLongEdgePx);
    if (
      !scaled.resized &&
      input.mimeType === 'image/jpeg' &&
      input.bytes.byteLength <= SMALL_JPEG_SKIP_BYTES
    ) {
      return null;
    }

    const canvas = document.createElement('canvas');
    canvas.width = scaled.width;
    canvas.height = scaled.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      return null;
    }
    ctx.drawImage(drawable, 0, 0, scaled.width, scaled.height);

    const outBlob = await new Promise<Blob | null>((resolve) => {
      canvas.toBlob((b) => resolve(b), 'image/jpeg', input.quality);
    });
    if (!outBlob || outBlob.size === 0) {
      return null;
    }

    const outBytes = new Uint8Array(await outBlob.arrayBuffer());
    if (outBytes.byteLength === 0 || outBytes.byteLength >= input.bytes.byteLength) {
      return null;
    }

    return {
      bytes: outBytes,
      mimeType: 'image/jpeg',
      name: replaceExtensionWithJpg(input.name),
      compressed: true,
    };
  } finally {
    if (cleanup) {
      try {
        cleanup();
      } catch {
        // Ignore cleanup errors
      }
    }
  }
}

export async function compressReceiptImageIfNeeded(
  bytes: Uint8Array,
  mimeType: string,
  name: string,
  compressor: ImageCompressorFn = compressImageInBrowser
): Promise<CompressedReceiptResult> {
  if (!COMPRESSIBLE_IMAGE_MIME_TYPES.has(mimeType)) {
    return { bytes, mimeType, name, compressed: false };
  }
  if (mimeType === 'image/jpeg' && bytes.byteLength <= SMALL_JPEG_SKIP_BYTES) {
    return { bytes, mimeType, name, compressed: false };
  }
  try {
    const result = await compressor({
      bytes,
      mimeType,
      name,
      maxLongEdgePx: MAX_IMAGE_LONG_EDGE_PX,
      quality: JPEG_COMPRESSION_QUALITY,
    });
    if (result && result.bytes.byteLength > 0 && result.bytes.byteLength < bytes.byteLength) {
      return result;
    }
  } catch {
    // Preserve original file when conversion is unsafe or fails
  }
  return { bytes, mimeType, name, compressed: false };
}

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
  fetchImpl: typeof fetch = fetch,
  compressor?: ImageCompressorFn
): Promise<FormData> {
  const validated = validateReceiptAsset(asset);

  const localResponse = await fetchImpl(validated.uri);
  if (!localResponse.ok && localResponse.status !== 0) {
    throw new Error(`Unable to read selected receipt file (status ${localResponse.status}).`);
  }

  const buffer = await localResponse.arrayBuffer();
  const rawBytes = new Uint8Array(buffer);
  if (rawBytes.byteLength === 0) {
    throw new Error('Selected receipt file is empty.');
  }

  const prepared = await compressReceiptImageIfNeeded(
    rawBytes,
    validated.mimeType,
    validated.name,
    compressor
  );

  if (prepared.bytes.byteLength > MAX_UPLOAD_BYTES) {
    throw new Error('Receipt file exceeds the 10 MB upload limit.');
  }

  const formData = new FormData();
  const hasReactNativeGetParts =
    typeof (formData as unknown as { getParts?: unknown }).getParts === 'function';

  if (hasReactNativeGetParts) {
    // Expo SDK 56-58's expo/fetch convertFormDataAsync rejects { uri, name, type }
    // and requires either a Blob or an object implementing { name, type, bytes() }.
    const part: ExpoBytesFormDataPart = {
      name: prepared.name,
      type: prepared.mimeType,
      bytes: async () => prepared.bytes,
    };
    formData.append('file', part as unknown as Blob);
  } else {
    const blob = new Blob([prepared.bytes as BlobPart], { type: prepared.mimeType });
    formData.append('file', blob, prepared.name);
  }

  return formData;
}

export async function buildMultiReceiptFormData(
  assets: ReceiptAssetInput[],
  purchaseId?: string | null,
  fetchImpl: typeof fetch = fetch,
  compressor?: ImageCompressorFn
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
    const rawBytes = new Uint8Array(buffer);
    if (rawBytes.byteLength === 0) {
      throw new Error(`Selected receipt file (${validated.name}) is empty.`);
    }

    const prepared = await compressReceiptImageIfNeeded(
      rawBytes,
      validated.mimeType,
      validated.name,
      compressor
    );

    if (prepared.bytes.byteLength > MAX_UPLOAD_BYTES) {
      throw new Error(`Receipt file (${prepared.name}) exceeds the 10 MB upload limit.`);
    }

    if (hasReactNativeGetParts) {
      const part: ExpoBytesFormDataPart = {
        name: prepared.name,
        type: prepared.mimeType,
        bytes: async () => prepared.bytes,
      };
      formData.append('files', part as unknown as Blob);
    } else {
      const blob = new Blob([prepared.bytes as BlobPart], { type: prepared.mimeType });
      formData.append('files', blob, prepared.name);
    }
  }

  return formData;
}
