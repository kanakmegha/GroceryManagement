import { createApiClient, type GetTokenFn } from '../../../shared/api/client';

describe('API client and Clerk unstable getToken stability', () => {
  it('always uses the latest getToken ref without changing client identity', async () => {
    let tokenVersion = 1;
    let currentGetToken: GetTokenFn = jest.fn(async () => `tok_${tokenVersion}`);
    const fetchMock = jest.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
    );

    const ref = { current: currentGetToken };
    const client = createApiClient({
      baseUrl: 'http://192.168.1.31:8001',
      getToken: (opts) => ref.current(opts),
      getHouseholdId: () => 'hh_1',
      fetchImpl: fetchMock as unknown as typeof fetch,
    });

    await client.get('/api/v1/auth/me');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({
      Authorization: 'Bearer tok_1',
    });

    // Simulate Clerk useAuth() returning a brand-new getToken closure on re-render
    tokenVersion = 2;
    currentGetToken = jest.fn(async () => `tok_${tokenVersion}`);
    ref.current = currentGetToken;

    await client.get('/api/v1/inventory/summary');
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1]?.[1]?.headers).toMatchObject({
      Authorization: 'Bearer tok_2',
    });
  });
});
