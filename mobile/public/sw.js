/* Grocery Management PWA Service Worker — Static Assets Only
 *
 * Security & privacy invariants:
 * - Caches ONLY versioned static shell assets (/_expo/static/*, /icons/*, /favicon.ico, /manifest.webmanifest).
 * - NEVER caches /api/*, Render backend origins, Clerk auth domains, /sso-callback, or any non-GET request.
 * - NEVER stores tokens, sessions, household data, or inventory responses.
 */

const CACHE_PREFIX = 'gm-static-';
const CACHE_NAME = `${CACHE_PREFIX}v1`;
const STATIC_PREFIXES = ['/_expo/static/', '/icons/'];
const STATIC_EXACT = new Set(['/favicon.ico', '/manifest.webmanifest']);

function isCacheableStaticRequest(request) {
  if (request.method !== 'GET') return false;
  let url;
  try {
    url = new URL(request.url);
  } catch {
    return false;
  }
  if (url.origin !== self.location.origin) return false;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/sso-callback')) return false;
  if (STATIC_EXACT.has(url.pathname)) return true;
  return STATIC_PREFIXES.some((prefix) => url.pathname.startsWith(prefix));
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(['/manifest.webmanifest', '/icons/icon-192.png', '/favicon.ico']))
      .catch(() => undefined)
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k.startsWith(CACHE_PREFIX) && k !== CACHE_NAME)
            .map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;

  let reqUrl;
  try {
    reqUrl = new URL(request.url);
  } catch {
    return;
  }

  // Explicitly bypass /sso-callback (including query strings) so browser network handling runs untouched
  if (reqUrl.origin === self.location.origin && reqUrl.pathname.startsWith('/sso-callback')) {
    return;
  }

  // Strictly bypass all non-GET, cross-origin (Render, Clerk), and API/auth requests
  if (isCacheableStaticRequest(request)) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((response) => {
          if (response && response.ok && response.type === 'basic') {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy)).catch(() => undefined);
          }
          return response;
        });
      })
    );
    return;
  }

  // Safe offline fallback for same-origin top-level page navigations only (never caches API responses)
  if (request.mode === 'navigate' && reqUrl.origin === self.location.origin) {
    event.respondWith(
      fetch(request).catch(
        () =>
          new Response(
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Grocery Management — Offline</title><style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#F8FAF7;color:#111827;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px;text-align:center}.card{background:#fff;border:1px solid #E5E7EB;border-radius:16px;padding:24px;max-width:360px}h1{font-size:18px;margin:0 0 8px}p{font-size:14px;color:#4B5563;margin:0}</style></head><body><div class="card"><h1>You are offline</h1><p>Grocery Management needs an internet connection to load your household data. Reconnect and refresh.</p></div></body></html>',
            { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
          )
      )
    );
  }
});
