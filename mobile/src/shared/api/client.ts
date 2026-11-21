import { ApiErrorEnvelope } from './types';

export type GetTokenFn = (options?: { skipCache?: boolean }) => Promise<string | null>;

export interface ApiClientConfig {
  baseUrl: string;
  getToken: GetTokenFn;
  getHouseholdId?: () => string | null;
  fetchImpl?: typeof fetch;
}

export interface ApiRequestOptions extends Omit<RequestInit, 'headers' | 'body'> {
  headers?: Record<string, string>;
  body?: unknown;
  skipAuth?: boolean;
  skipHouseholdHeader?: boolean;
}

export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: Record<string, unknown>;
  readonly requestId?: string;
  readonly isNetworkError: boolean;

  constructor(params: {
    message: string;
    status: number;
    code: string;
    details?: Record<string, unknown>;
    requestId?: string;
    isNetworkError?: boolean;
  }) {
    super(params.message);
    this.name = 'ApiClientError';
    this.status = params.status;
    this.code = params.code;
    this.details = params.details;
    this.requestId = params.requestId;
    this.isNetworkError = params.isNetworkError ?? false;
  }
}

function defaultMessageForStatus(status: number): string {
  switch (status) {
    case 401:
      return 'Your session has expired. Please sign in again.';
    case 403:
      return 'You do not have permission to access this household resource.';
    case 404:
      return 'The requested resource was not found.';
    case 409:
      return 'This action conflicts with the current server state.';
    case 422:
      return 'The submitted data could not be validated.';
    default:
      return status >= 500
        ? 'The server encountered an unexpected error. Please try again.'
        : `Request failed with status ${status}.`;
  }
}

function isApiErrorEnvelope(payload: unknown): payload is ApiErrorEnvelope {
  if (typeof payload !== 'object' || payload === null) {
    return false;
  }
  const maybeError = (payload as Record<string, unknown>).error;
  if (typeof maybeError !== 'object' || maybeError === null) {
    return false;
  }
  const errObj = maybeError as Record<string, unknown>;
  return typeof errObj.code === 'string' && typeof errObj.message === 'string';
}

async function parseErrorResponse(response: Response): Promise<ApiClientError> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (isApiErrorEnvelope(payload)) {
    return new ApiClientError({
      status: response.status,
      code: payload.error.code,
      message: payload.error.message,
      details: payload.error.details,
      requestId: payload.error.request_id,
    });
  }

  return new ApiClientError({
    status: response.status,
    code: response.status === 401 ? 'UNAUTHORIZED' : `HTTP_${response.status}`,
    message: defaultMessageForStatus(response.status),
  });
}

export interface ApiClient {
  request<T>(path: string, options?: ApiRequestOptions): Promise<T>;
  get<T>(path: string, options?: Omit<ApiRequestOptions, 'method' | 'body'>): Promise<T>;
  post<T>(
    path: string,
    body?: unknown,
    options?: Omit<ApiRequestOptions, 'method' | 'body'>
  ): Promise<T>;
}

export function createApiClient(config: ApiClientConfig): ApiClient {
  const normalizedBaseUrl = config.baseUrl.replace(/\/+$/, '');
  const fetchFn = config.fetchImpl ?? fetch;

  async function executeRequest(
    url: string,
    options: ApiRequestOptions,
    skipCacheForToken: boolean
  ): Promise<Response> {
    const headers: Record<string, string> = {
      Accept: 'application/json',
      ...(options.headers ?? {}),
    };

    if (!options.skipAuth) {
      const token = await config.getToken(
        skipCacheForToken ? { skipCache: true } : undefined
      );
      if (!token) {
        throw new ApiClientError({
          status: 401,
          code: 'UNAUTHORIZED',
          message: 'Authentication token is unavailable. Please sign in again.',
        });
      }
      headers.Authorization = `Bearer ${token}`;
    }

    if (!options.skipHouseholdHeader && config.getHouseholdId) {
      const householdId = config.getHouseholdId();
      if (householdId) {
        headers['X-Household-Id'] = householdId;
      }
    }

    let serializedBody: RequestInit['body'];
    if (options.body !== undefined) {
      if (typeof FormData !== 'undefined' && options.body instanceof FormData) {
        serializedBody = options.body;
      } else {
        headers['Content-Type'] = headers['Content-Type'] ?? 'application/json';
        serializedBody = JSON.stringify(options.body);
      }
    }

    try {
      return await fetchFn(url, {
        ...options,
        headers,
        body: serializedBody,
      });
    } catch (err) {
      const message =
        err instanceof Error && err.message
          ? `Network request failed: ${err.message}`
          : 'Unable to reach the server. Check your internet connection.';
      throw new ApiClientError({
        status: 0,
        code: 'NETWORK_ERROR',
        message,
        isNetworkError: true,
      });
    }
  }

  async function request<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
    const normalizedPath = path.startsWith('/') ? path : `/${path}`;
    const url = `${normalizedBaseUrl}${normalizedPath}`;

    let response = await executeRequest(url, options, false);

    // Retry once on 401 with a fresh non-cached Clerk token
    if (response.status === 401 && !options.skipAuth) {
      response = await executeRequest(url, options, true);
    }

    if (!response.ok) {
      throw await parseErrorResponse(response);
    }

    if (response.status === 204) {
      return undefined as T;
    }

    const contentType = response.headers.get('content-type') ?? '';
    if (contentType.includes('application/json')) {
      return (await response.json()) as T;
    }

    return (await response.text()) as unknown as T;
  }

  return {
    request,
    get: <T>(path: string, options?: Omit<ApiRequestOptions, 'method' | 'body'>) =>
      request<T>(path, { ...options, method: 'GET' }),
    post: <T>(
      path: string,
      body?: unknown,
      options?: Omit<ApiRequestOptions, 'method' | 'body'>
    ) => request<T>(path, { ...options, method: 'POST', body }),
  };
}
