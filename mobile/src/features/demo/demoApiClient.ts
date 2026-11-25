import { ApiClientError } from '../../shared/api/client';

/**
 * Dedicated anonymous, GET-only client for the public demo API.
 *
 * Guarantees by construction:
 *  - only paths on the /api/v1/demo/* allowlist can be requested;
 *  - there is no post/put/delete method on the interface;
 *  - no Authorization header and no X-Household-Id header are ever sent;
 *  - no cookies/credentials are sent.
 */
const DEMO_PATH_PATTERN =
  /^\/api\/v1\/demo\/(dashboard|inventory|purchases|receipts|recipes|daily-plan|analytics)(\/[0-9a-fA-F-]{36})?(\?[A-Za-z0-9_=&.%-]*)?$/;

export function isAllowedDemoPath(path: string): boolean {
  return DEMO_PATH_PATTERN.test(path);
}

export interface DemoApiClient {
  get<T>(path: string, options?: { signal?: AbortSignal }): Promise<T>;
}

export interface DemoApiClientConfig {
  baseUrl: string;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
}

export const DEMO_REQUEST_TIMEOUT_MS = 15_000;

export function createDemoApiClient(config: DemoApiClientConfig): DemoApiClient {
  const baseUrl = config.baseUrl.replace(/\/+$/, '');
  const timeoutMs = config.timeoutMs ?? DEMO_REQUEST_TIMEOUT_MS;

  async function get<T>(path: string, options?: { signal?: AbortSignal }): Promise<T> {
    if (!isAllowedDemoPath(path)) {
      throw new ApiClientError({
        status: 0,
        code: 'DEMO_PATH_FORBIDDEN',
        message: 'Demo Mode can only read /api/v1/demo/* endpoints.',
      });
    }
    const fetchFn = config.fetchImpl ?? fetch;
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    const onCallerAbort = () => controller.abort();
    options?.signal?.addEventListener('abort', onCallerAbort, { once: true });

    let response: Response;
    try {
      response = await fetchFn(`${baseUrl}${path}`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
        credentials: 'omit',
        signal: controller.signal,
      });
    } catch (err) {
      if (options?.signal?.aborted) {
        throw err;
      }
      throw new ApiClientError({
        status: 0,
        code: timedOut ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR',
        message: timedOut
          ? 'The demo server took too long to respond. Please try again.'
          : 'Unable to reach the demo server. Check your connection and try again.',
        isNetworkError: true,
        isTimeout: timedOut,
      });
    } finally {
      clearTimeout(timer);
      options?.signal?.removeEventListener('abort', onCallerAbort);
    }

    if (!response.ok) {
      let message =
        response.status === 429
          ? 'Too many demo requests. Please wait a moment and try again.'
          : response.status === 404
            ? 'The demo is currently unavailable.'
            : `Demo request failed (${response.status}).`;
      let code = `HTTP_${response.status}`;
      try {
        const payload = (await response.json()) as { error?: { code?: string; message?: string } };
        if (payload?.error?.code) code = payload.error.code;
        if (payload?.error?.message && response.status !== 404) message = payload.error.message;
      } catch {
        // keep defaults
      }
      throw new ApiClientError({ status: response.status, code, message });
    }
    return (await response.json()) as T;
  }

  return { get };
}
