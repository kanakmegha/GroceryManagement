import { ApiClientError } from '../../../shared/api/client';
import { createDemoApiClient, isAllowedDemoPath } from '../demoApiClient';

const UUID = '123e4567-e89b-12d3-a456-426614174000';

function mockFetch(status: number, body: unknown) {
  return jest.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  })) as unknown as jest.MockedFunction<typeof fetch>;
}

describe('demo API path allowlist', () => {
  it('allows only /api/v1/demo/* read endpoints', () => {
    for (const p of [
      '/api/v1/demo/dashboard',
      '/api/v1/demo/inventory',
      '/api/v1/demo/purchases',
      `/api/v1/demo/purchases/${UUID}`,
      '/api/v1/demo/receipts',
      `/api/v1/demo/receipts/${UUID}`,
      '/api/v1/demo/recipes',
      '/api/v1/demo/daily-plan',
      '/api/v1/demo/analytics',
      '/api/v1/demo/purchases?limit=20&offset=0',
    ]) {
      expect(isAllowedDemoPath(p)).toBe(true);
    }
  });

  it('rejects normal household endpoints and traversal tricks', () => {
    for (const p of [
      '/api/v1/auth/me',
      '/api/v1/inventory/summary',
      '/api/v1/purchases',
      '/api/v1/discover/daily-plan',
      '/api/v1/demo/../auth/me',
      '/api/v1/demo/dashboard/../../auth/me',
      '/api/v1/demo/unknown',
      '/api/v1/demo/purchases/not-a-uuid',
      'https://evil.example/api/v1/demo/dashboard',
      '/api/v1/demo/dashboard?x=<script>',
    ]) {
      expect(isAllowedDemoPath(p)).toBe(false);
    }
  });
});

describe('demo API client', () => {
  it('sends anonymous GET with no auth or household headers and omits credentials', async () => {
    const fetchImpl = mockFetch(200, { demo_mode: true });
    const client = createDemoApiClient({ baseUrl: 'http://api.test/', fetchImpl });
    await client.get('/api/v1/demo/dashboard');
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://api.test/api/v1/demo/dashboard');
    expect(init.method).toBe('GET');
    expect(init.credentials).toBe('omit');
    const headers = init.headers as Record<string, string>;
    expect(Object.keys(headers).map((h) => h.toLowerCase())).toEqual(['accept']);
    expect(init.body).toBeUndefined();
  });

  it('exposes no mutating methods', () => {
    const client = createDemoApiClient({ baseUrl: 'http://api.test', fetchImpl: mockFetch(200, {}) });
    expect(Object.keys(client)).toEqual(['get']);
  });

  it('refuses forbidden paths without any network call', async () => {
    const fetchImpl = mockFetch(200, {});
    const client = createDemoApiClient({ baseUrl: 'http://api.test', fetchImpl });
    await expect(client.get('/api/v1/auth/me')).rejects.toMatchObject({
      code: 'DEMO_PATH_FORBIDDEN',
    });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('maps 429 and 404 to friendly errors', async () => {
    const limited = createDemoApiClient({
      baseUrl: 'http://api.test',
      fetchImpl: mockFetch(429, { error: { code: 'RATE_LIMITED', message: 'slow down' } }),
    });
    await expect(limited.get('/api/v1/demo/dashboard')).rejects.toMatchObject({
      status: 429,
      code: 'RATE_LIMITED',
    });
    const off = createDemoApiClient({
      baseUrl: 'http://api.test',
      fetchImpl: mockFetch(404, { error: { code: 'NOT_FOUND', message: 'x' } }),
    });
    const err = (await off.get('/api/v1/demo/dashboard').catch((e) => e)) as ApiClientError;
    expect(err).toBeInstanceOf(ApiClientError);
    expect(err.message).toBe('The demo is currently unavailable.');
  });

  it('maps network failures to a retryable error', async () => {
    const fetchImpl = jest.fn(async () => {
      throw new Error('boom');
    }) as unknown as typeof fetch;
    const client = createDemoApiClient({ baseUrl: 'http://api.test', fetchImpl });
    await expect(client.get('/api/v1/demo/dashboard')).rejects.toMatchObject({
      code: 'NETWORK_ERROR',
      isNetworkError: true,
    });
  });
});
