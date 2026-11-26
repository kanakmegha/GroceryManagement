import { ApiClientError, createApiClient } from '../client';

function makeJsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: (name: string) =>
        name.toLowerCase() === 'content-type' ? 'application/json' : null,
    },
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe('createApiClient', () => {
  it('attaches Authorization and X-Household-Id headers on requests', async () => {
    const fetchMock = jest.fn(async () =>
      makeJsonResponse(200, { ok: true })
    ) as unknown as typeof fetch;

    const client = createApiClient({
      baseUrl: 'https://api.example.com/',
      getToken: async () => 'clerk_jwt_123',
      getHouseholdId: () => 'hh_abc',
      fetchImpl: fetchMock,
    });

    const result = await client.get<{ ok: boolean }>('/api/v1/auth/me');
    expect(result).toEqual({ ok: true });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = (fetchMock as jest.Mock).mock.calls[0];
    expect(url).toBe('https://api.example.com/api/v1/auth/me');
    expect(init.headers).toMatchObject({
      Accept: 'application/json',
      Authorization: 'Bearer clerk_jwt_123',
      'X-Household-Id': 'hh_abc',
    });
  });

  it('retries once on 401 with getToken({ skipCache: true })', async () => {
    const getToken = jest
      .fn()
      .mockResolvedValueOnce('stale_token')
      .mockResolvedValueOnce('fresh_token');

    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(
        makeJsonResponse(401, {
          error: { code: 'UNAUTHORIZED', message: 'Token expired' },
        })
      )
      .mockResolvedValueOnce(makeJsonResponse(200, { household: { id: 'hh_1' } }));

    const client = createApiClient({
      baseUrl: 'http://localhost:8000',
      getToken,
      fetchImpl: fetchMock as unknown as typeof fetch,
    });

    const data = await client.get<{ household: { id: string } }>('/api/v1/auth/me');
    expect(data.household.id).toBe('hh_1');
    expect(getToken).toHaveBeenNthCalledWith(1, undefined);
    expect(getToken).toHaveBeenNthCalledWith(2, { skipCache: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe('Bearer fresh_token');
  });

  it('maps backend ApiErrorEnvelope cleanly to ApiClientError', async () => {
    const fetchMock = jest.fn(async () =>
      makeJsonResponse(403, {
        error: {
          code: 'FORBIDDEN',
          message: 'Not a member of this household',
          details: { household_id: 'hh_other' },
          request_id: 'req_123',
        },
      })
    ) as unknown as typeof fetch;

    const client = createApiClient({
      baseUrl: 'http://localhost:8000',
      getToken: async () => 'token_1',
      fetchImpl: fetchMock,
    });

    await expect(client.get('/api/v1/auth/me')).rejects.toMatchObject({
      name: 'ApiClientError',
      status: 403,
      code: 'FORBIDDEN',
      message: 'Not a member of this household',
      details: { household_id: 'hh_other' },
      requestId: 'req_123',
      isNetworkError: false,
    });
  });

  it('throws ApiClientError with isNetworkError when fetch rejects', async () => {
    const fetchMock = jest.fn(async () => {
      throw new Error('Network request failed');
    }) as unknown as typeof fetch;

    const client = createApiClient({
      baseUrl: 'http://localhost:8000',
      getToken: async () => 'token_1',
      fetchImpl: fetchMock,
    });

    await expect(client.get('/api/v1/auth/me')).rejects.toBeInstanceOf(ApiClientError);
    await expect(client.get('/api/v1/auth/me')).rejects.toMatchObject({
      status: 0,
      code: 'NETWORK_ERROR',
      isNetworkError: true,
    });
  });

  it('throws 401 ApiClientError when getToken returns null', async () => {
    const fetchMock = jest.fn() as unknown as typeof fetch;

    const client = createApiClient({
      baseUrl: 'http://localhost:8000',
      getToken: async () => null,
      fetchImpl: fetchMock,
    });

    await expect(client.get('/api/v1/auth/me')).rejects.toMatchObject({
      status: 401,
      code: 'UNAUTHORIZED',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('deduplicates concurrent 401 responses into a single token refresh and retries all with the fresh token', async () => {
    let resolveRefresh!: (tok: string) => void;
    const refreshPromise = new Promise<string>((resolve) => {
      resolveRefresh = resolve;
    });

    const getToken = jest.fn((opts?: { skipCache?: boolean }) => {
      if (opts?.skipCache) {
        return refreshPromise;
      }
      return Promise.resolve('stale_jwt');
    });

    const fetchMock = jest.fn(async (_url: string, init?: RequestInit) => {
      const auth = (init?.headers as Record<string, string>)?.Authorization;
      if (auth === 'Bearer stale_jwt') {
        return makeJsonResponse(401, {
          error: { code: 'UNAUTHORIZED', message: 'Token expired' },
        });
      }
      return makeJsonResponse(200, { ok: true, auth });
    }) as unknown as typeof fetch;

    const client = createApiClient({
      baseUrl: 'http://localhost:8001',
      getToken,
      fetchImpl: fetchMock,
    });

    const p1 = client.get<{ ok: boolean; auth: string }>('/api/v1/inventory/summary');
    const p2 = client.get<{ ok: boolean; auth: string }>('/api/v1/purchases?limit=5');
    const p3 = client.get<{ ok: boolean; auth: string }>('/api/v1/recipes/preferences');

    // Allow initial 401s to settle before completing single-flight refresh
    await new Promise((r) => setTimeout(r, 10));
    resolveRefresh('fresh_jwt_999');

    const [r1, r2, r3] = await Promise.all([p1, p2, p3]);
    expect(r1).toEqual({ ok: true, auth: 'Bearer fresh_jwt_999' });
    expect(r2).toEqual({ ok: true, auth: 'Bearer fresh_jwt_999' });
    expect(r3).toEqual({ ok: true, auth: 'Bearer fresh_jwt_999' });

    const refreshCalls = getToken.mock.calls.filter((c) => c[0]?.skipCache === true);
    expect(refreshCalls).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });

  it('retries at most once on 401 and returns second 401 to the caller', async () => {
    const getToken = jest
      .fn()
      .mockResolvedValueOnce('stale_jwt')
      .mockResolvedValueOnce('still_bad_jwt');

    const fetchMock = jest.fn(async () =>
      makeJsonResponse(401, {
        error: { code: 'UNAUTHORIZED', message: 'Invalid token' },
      })
    ) as unknown as typeof fetch;

    const client = createApiClient({
      baseUrl: 'http://localhost:8001',
      getToken,
      fetchImpl: fetchMock,
    });

    await expect(client.get('/api/v1/auth/me')).rejects.toMatchObject({
      name: 'ApiClientError',
      status: 401,
      code: 'UNAUTHORIZED',
      message: 'Invalid token',
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('clears shared refresh promise when token refresh rejects so later calls can recover', async () => {
    let shouldFail = true;
    const getToken = jest.fn(async (opts?: { skipCache?: boolean }) => {
      if (opts?.skipCache) {
        if (shouldFail) {
          throw new Error('Clerk refresh failed');
        }
        return 'recovered_jwt';
      }
      return 'stale_jwt';
    });

    const fetchMock = jest.fn(async (_url: string, init?: RequestInit) => {
      const auth = (init?.headers as Record<string, string>)?.Authorization;
      if (auth === 'Bearer recovered_jwt') {
        return makeJsonResponse(200, { ok: true });
      }
      return makeJsonResponse(401, {
        error: { code: 'UNAUTHORIZED', message: 'Token expired' },
      });
    }) as unknown as typeof fetch;

    const client = createApiClient({
      baseUrl: 'http://localhost:8001',
      getToken,
      fetchImpl: fetchMock,
    });

    await expect(
      Promise.all([
        client.get('/api/v1/a'),
        client.get('/api/v1/b'),
      ])
    ).rejects.toThrow('Clerk refresh failed');

    shouldFail = false;
    const recovered = await client.get<{ ok: boolean }>('/api/v1/c');
    expect(recovered).toEqual({ ok: true });
  });

  it('does not trigger token refresh on 403 responses', async () => {
    const getToken = jest.fn(async () => 'valid_jwt');
    const fetchMock = jest.fn(async () =>
      makeJsonResponse(403, {
        error: { code: 'FORBIDDEN', message: 'Access denied' },
      })
    ) as unknown as typeof fetch;

    const client = createApiClient({
      baseUrl: 'http://localhost:8001',
      getToken,
      fetchImpl: fetchMock,
    });

    await expect(client.get('/api/v1/households')).rejects.toMatchObject({
      status: 403,
      code: 'FORBIDDEN',
    });
    expect(getToken).toHaveBeenCalledTimes(1);
    expect(getToken).toHaveBeenCalledWith(undefined);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('preserves method, query parameters, and JSON body during 401 retry', async () => {
    const getToken = jest
      .fn()
      .mockResolvedValueOnce('stale_jwt')
      .mockResolvedValueOnce('fresh_jwt');

    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(
        makeJsonResponse(401, { error: { code: 'UNAUTHORIZED', message: 'Expired' } })
      )
      .mockResolvedValueOnce(makeJsonResponse(201, { id: 'p_1' }));

    const client = createApiClient({
      baseUrl: 'http://localhost:8001',
      getToken,
      fetchImpl: fetchMock as unknown as typeof fetch,
    });

    const payload = { merchant_name: 'FreshMart', items: [{ name: 'Milk', qty: 1 }] };
    const res = await client.post<{ id: string }>('/api/v1/purchases?draft=true', payload);
    expect(res).toEqual({ id: 'p_1' });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [retryUrl, retryInit] = fetchMock.mock.calls[1];
    expect(retryUrl).toBe('http://localhost:8001/api/v1/purchases?draft=true');
    expect(retryInit.method).toBe('POST');
    expect(retryInit.body).toBe(JSON.stringify(payload));
    expect((retryInit.headers as Record<string, string>).Authorization).toBe('Bearer fresh_jwt');
  });

  it('aborts and throws distinguishable REQUEST_TIMEOUT error when timeoutMs elapses', async () => {
    const clearSpy = jest.spyOn(globalThis, 'clearTimeout');
    const fetchMock = jest.fn((_url: string, init?: RequestInit) => {
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          const err = new Error('The operation was aborted.');
          err.name = 'AbortError';
          reject(err);
        });
      });
    }) as unknown as typeof fetch;

    const client = createApiClient({
      baseUrl: 'http://localhost:8001',
      getToken: async () => 'valid_jwt',
      fetchImpl: fetchMock,
      defaultTimeoutMs: 25,
    });

    await expect(client.get('/api/v1/recipes/recommendations')).rejects.toMatchObject({
      name: 'ApiClientError',
      status: 0,
      code: 'REQUEST_TIMEOUT',
      isNetworkError: true,
      isTimeout: true,
    });
    expect(clearSpy).toHaveBeenCalled();
    clearSpy.mockRestore();
  });

  it('does not classify intentional caller abort as REQUEST_TIMEOUT', async () => {
    const callerController = new AbortController();
    const fetchMock = jest.fn((_url: string, init?: RequestInit) => {
      return new Promise<Response>((_resolve, reject) => {
        const failAbort = () => {
          const err = new Error('Aborted by user');
          err.name = 'AbortError';
          reject(err);
        };
        if (init?.signal?.aborted) {
          failAbort();
          return;
        }
        init?.signal?.addEventListener('abort', failAbort, { once: true });
      });
    }) as unknown as typeof fetch;

    const client = createApiClient({
      baseUrl: 'http://localhost:8001',
      getToken: async () => 'valid_jwt',
      fetchImpl: fetchMock,
      defaultTimeoutMs: 5000,
    });

    const req = client.get('/api/v1/inventory/summary', { signal: callerController.signal });
    callerController.abort();

    await expect(req).rejects.toMatchObject({
      name: 'AbortError',
      message: 'Aborted by user',
    });
  });

  it('honors per-request timeoutMs override on post() above defaultTimeoutMs', async () => {
    jest.useFakeTimers();
    const fetchMock = jest.fn((_url: string, init?: RequestInit) => {
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          reject(new Error('The operation was aborted.'));
        });
      });
    });

    const client = createApiClient({
      baseUrl: 'http://localhost:8001',
      getToken: async () => 'jwt_1',
      fetchImpl: fetchMock as unknown as typeof fetch,
      defaultTimeoutMs: 15_000,
    });

    let settled = false;
    const reqPromise = client
      .post('/api/v1/recipes/chat', { message: 'hi' }, { timeoutMs: 65_000 })
      .catch((err) => {
        settled = true;
        return err;
      });

    await jest.advanceTimersByTimeAsync(16_000);
    expect(settled).toBe(false);

    await jest.advanceTimersByTimeAsync(50_000);
    const err = (await reqPromise) as ApiClientError;
    expect(settled).toBe(true);
    expect(err).toBeInstanceOf(ApiClientError);
    expect(err.code).toBe('REQUEST_TIMEOUT');
    expect(err.message).toContain('65s');
    jest.useRealTimers();
  });
});
