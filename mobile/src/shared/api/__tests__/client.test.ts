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
});
