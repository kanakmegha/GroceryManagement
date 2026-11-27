export const IOS_INSTALL_DISMISSED_KEY = 'gm_ios_install_banner_dismissed';

const STATIC_PREFIXES = ['/_expo/static/', '/icons/'];
const STATIC_EXACT = new Set(['/favicon.ico', '/manifest.webmanifest']);

/**
 * Pure mirror of sw.js cacheability rules for deterministic unit testing.
 * Strictly allows ONLY same-origin GET requests for versioned static assets.
 */
export function shouldCacheStaticAsset(params: {
  method: string;
  url: string;
  appOrigin: string;
}): boolean {
  if (params.method.toUpperCase() !== 'GET') return false;
  let parsed: URL;
  try {
    parsed = new URL(params.url);
  } catch {
    return false;
  }
  if (parsed.origin !== params.appOrigin) return false;
  if (parsed.pathname.startsWith('/api/') || parsed.pathname.startsWith('/sso-callback')) {
    return false;
  }
  if (STATIC_EXACT.has(parsed.pathname)) return true;
  return STATIC_PREFIXES.some((prefix) => parsed.pathname.startsWith(prefix));
}

export function shouldShowIosInstallBanner(params: {
  isWeb: boolean;
  userAgent: string;
  isStandalone: boolean;
  dismissed: boolean;
}): boolean {
  if (!params.isWeb || params.isStandalone || params.dismissed) return false;
  const ua = params.userAgent || '';
  const isIos = /iPhone|iPad|iPod/i.test(ua);
  const isSafari = /Safari/i.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS/i.test(ua);
  return isIos && isSafari;
}

export function registerPwaServiceWorker(): void {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return;
  if (!('serviceWorker' in navigator)) return;

  const isSecure =
    window.location.protocol === 'https:' ||
    window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1';
  if (!isSecure) return;

  const register = () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // Non-fatal: app works normally without a service worker
    });
  };

  if (typeof document !== 'undefined' && document.readyState === 'complete') {
    register();
  } else {
    window.addEventListener('load', register, { once: true });
  }
}
