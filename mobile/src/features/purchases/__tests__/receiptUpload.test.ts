import { createApiClient } from '../../../shared/api/client';
import {
  buildReceiptFormData,
  resolveReceiptMimeType,
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
});
