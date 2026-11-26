import { createApiClient } from '../../../shared/api/client';
import {
  buildReceiptFormData,
  compressReceiptImageIfNeeded,
  computeScaledDimensions,
  MAX_IMAGE_LONG_EDGE_PX,
  RECEIPT_OCR_TIMEOUT_MS,
  resolveReceiptMimeType,
  SMALL_JPEG_SKIP_BYTES,
  validateReceiptAsset,
} from '../receiptUpload';

describe('receiptUpload and multipart API client handling', () => {
  it('resolves MIME types including image/jpg and filename extensions', () => {
    expect(resolveReceiptMimeType('image/jpg', 'photo.jpg')).toBe('image/jpeg');
    expect(resolveReceiptMimeType('application/octet-stream', 'invoice.PDF')).toBe(
      'application/pdf'
    );
    expect(resolveReceiptMimeType(null, 'receipt.png')).toBe('image/png');
  });

  it('rejects missing URIs and unsupported file types cleanly', () => {
    expect(() =>
      validateReceiptAsset({ uri: '', name: 'receipt.jpg', mimeType: 'image/jpeg' })
    ).toThrow('no readable URI');

    expect(() =>
      validateReceiptAsset({
        uri: 'file:///tmp/archive.zip',
        name: 'archive.zip',
        mimeType: 'application/zip',
      })
    ).toThrow('Unsupported receipt file type');
  });

  it('builds FormData with bytes() part on React Native FormData (Expo SDK 58)', async () => {
    const sampleBytes = new Uint8Array([0x25, 0x50, 0x44, 0x46]); // %PDF
    const mockLocalFetch = jest.fn(async () => ({
      ok: true,
      status: 200,
      arrayBuffer: async () => sampleBytes.buffer,
    }));

    const appendedParts: Array<[string, unknown]> = [];
    const OriginalFormData = globalThis.FormData;
    class MockRnFormData {
      getParts() {
        return appendedParts.map(([, val]) => ({ file: val }));
      }
      append(key: string, val: unknown) {
        appendedParts.push([key, val]);
      }
    }
    globalThis.FormData = MockRnFormData as unknown as typeof FormData;

    try {
      const formData = await buildReceiptFormData(
        {
          uri: 'file:///cache/receipt.pdf',
          name: 'bigbasket.pdf',
          mimeType: 'application/pdf',
        },
        mockLocalFetch as unknown as typeof fetch
      );

      expect(formData).toBeInstanceOf(MockRnFormData);
      expect(appendedParts).toHaveLength(1);
      expect(appendedParts[0][0]).toBe('file');
      const part = appendedParts[0][1] as {
        name: string;
        type: string;
        bytes: () => Promise<Uint8Array>;
      };
      expect(part.name).toBe('bigbasket.pdf');
      expect(part.type).toBe('application/pdf');
      expect(await part.bytes()).toEqual(sampleBytes);
    } finally {
      globalThis.FormData = OriginalFormData;
    }
  });

  it('preserves Authorization and X-Household-Id without overriding multipart Content-Type', async () => {
    const fetchMock = jest.fn(async () =>
      new Response(JSON.stringify({ id: 'draft_1', status: 'draft', items: [] }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const client = createApiClient({
      baseUrl: 'http://192.168.1.31:8001',
      getToken: async () => 'clerk_tok_123',
      getHouseholdId: () => 'hh_123',
      fetchImpl: fetchMock as unknown as typeof fetch,
    });

    const fd = new FormData();
    fd.append('file', new Blob(['hello'], { type: 'text/plain' }), 'receipt.txt');

    await client.post('/api/v1/purchases/ocr', fd);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer clerk_tok_123');
    expect(headers['X-Household-Id']).toBe('hh_123');
    expect(headers['Content-Type']).toBeUndefined();
    expect(init.body).toBe(fd);
  });

  it('scales large images to max long edge 1600px while preserving aspect ratio', () => {
    expect(computeScaledDimensions(4000, 3000, MAX_IMAGE_LONG_EDGE_PX)).toEqual({
      width: 1600,
      height: 1200,
      resized: true,
    });
    expect(computeScaledDimensions(1200, 900, MAX_IMAGE_LONG_EDGE_PX)).toEqual({
      width: 1200,
      height: 900,
      resized: false,
    });
  });

  it('compresses large JPEG and PNG uploads while skipping small JPEGs and PDFs', async () => {
    const largeJpeg = new Uint8Array(900 * 1024); // 900 KB
    const smallJpeg = new Uint8Array(Math.floor(SMALL_JPEG_SKIP_BYTES / 2)); // 150 KB
    const pngBytes = new Uint8Array(600 * 1024); // 600 KB
    const pdfBytes = new Uint8Array(500 * 1024); // 500 KB
    const compressedBytes = new Uint8Array(180 * 1024); // 180 KB

    const mockCompressor = jest.fn(async ({ name }: { name: string }) => ({
      bytes: compressedBytes,
      mimeType: 'image/jpeg',
      name: name.replace(/\.[^.]+$/, '.jpg'),
      compressed: true,
    }));

    // Large JPEG -> compressed
    const resLarge = await compressReceiptImageIfNeeded(
      largeJpeg,
      'image/jpeg',
      'photo.jpeg',
      mockCompressor
    );
    expect(resLarge.compressed).toBe(true);
    expect(resLarge.bytes.byteLength).toBe(180 * 1024);
    expect(resLarge.mimeType).toBe('image/jpeg');

    // Small JPEG -> skipped without calling compressor
    mockCompressor.mockClear();
    const resSmall = await compressReceiptImageIfNeeded(
      smallJpeg,
      'image/jpeg',
      'small.jpg',
      mockCompressor
    );
    expect(resSmall.compressed).toBe(false);
    expect(resSmall.bytes.byteLength).toBe(smallJpeg.byteLength);
    expect(mockCompressor).not.toHaveBeenCalled();

    // PNG -> compressed to JPEG with .jpg filename
    const resPng = await compressReceiptImageIfNeeded(
      pngBytes,
      'image/png',
      'scan.png',
      mockCompressor
    );
    expect(resPng.compressed).toBe(true);
    expect(resPng.mimeType).toBe('image/jpeg');
    expect(resPng.name).toBe('scan.jpg');

    // PDF -> untouched
    mockCompressor.mockClear();
    const resPdf = await compressReceiptImageIfNeeded(
      pdfBytes,
      'application/pdf',
      'invoice.pdf',
      mockCompressor
    );
    expect(resPdf.compressed).toBe(false);
    expect(resPdf.bytes).toBe(pdfBytes);
    expect(resPdf.mimeType).toBe('application/pdf');
    expect(mockCompressor).not.toHaveBeenCalled();
  });

  it('falls back to original bytes when compression throws', async () => {
    const raw = new Uint8Array(500 * 1024);
    const failingCompressor = jest.fn(async () => {
      throw new Error('Canvas unavailable');
    });
    const res = await compressReceiptImageIfNeeded(raw, 'image/jpeg', 'bill.jpg', failingCompressor);
    expect(res.compressed).toBe(false);
    expect(res.bytes).toBe(raw);
    expect(res.mimeType).toBe('image/jpeg');
    expect(res.name).toBe('bill.jpg');
  });

  it('exposes a 60s receipt-specific OCR timeout without altering global default', () => {
    expect(RECEIPT_OCR_TIMEOUT_MS).toBe(60_000);
  });
});
