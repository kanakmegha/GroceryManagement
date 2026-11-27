import {
  shouldCacheStaticAsset,
  shouldShowIosInstallBanner,
} from '../pwaRegistration';

const APP_ORIGIN = 'https://lighthearted-sawine-87d3a5.netlify.app';

describe('PWA service worker static-only caching rules', () => {
  it('allows same-origin GET requests for versioned static assets and icons', () => {
    for (const url of [
      `${APP_ORIGIN}/_expo/static/js/web/__common-abc123.js`,
      `${APP_ORIGIN}/icons/icon-192.png`,
      `${APP_ORIGIN}/icons/icon-512.png`,
      `${APP_ORIGIN}/icons/apple-touch-icon.png`,
      `${APP_ORIGIN}/favicon.ico`,
      `${APP_ORIGIN}/manifest.webmanifest`,
    ]) {
      expect(shouldCacheStaticAsset({ method: 'GET', url, appOrigin: APP_ORIGIN })).toBe(true);
    }
  });

  it('never caches API, Render backend, Clerk auth, callbacks, or non-GET requests', () => {
    const forbidden = [
      { method: 'GET', url: `${APP_ORIGIN}/api/v1/auth/me` },
      { method: 'GET', url: `${APP_ORIGIN}/api/v1/inventory/summary` },
      { method: 'GET', url: `${APP_ORIGIN}/sso-callback` },
      { method: 'GET', url: 'https://grocerymanagement-4zjc.onrender.com/api/v1/auth/me' },
      { method: 'GET', url: 'https://grocerymanagement-4zjc.onrender.com/health' },
      { method: 'GET', url: 'https://clerk.example.com/v1/client' },
      { method: 'POST', url: `${APP_ORIGIN}/_expo/static/js/web/app.js` },
      { method: 'PUT', url: `${APP_ORIGIN}/api/v1/recipes/preferences` },
      { method: 'PATCH', url: `${APP_ORIGIN}/api/v1/purchases/123` },
      { method: 'DELETE', url: `${APP_ORIGIN}/api/v1/inventory/lots/123` },
    ];
    for (const req of forbidden) {
      expect(shouldCacheStaticAsset({ ...req, appOrigin: APP_ORIGIN })).toBe(false);
    }
  });
});

describe('iOS Safari install banner visibility', () => {
  const iosSafariUa =
    'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
  const iosChromeUa =
    'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/129.0.0.0 Mobile/15E148 Safari/604.1';
  const androidChromeUa =
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36';

  it('shows only on iOS Safari when not standalone and not dismissed', () => {
    expect(
      shouldShowIosInstallBanner({
        isWeb: true,
        userAgent: iosSafariUa,
        isStandalone: false,
        dismissed: false,
      })
    ).toBe(true);
  });

  it('hides when standalone, dismissed, non-web, or non-Safari browser', () => {
    expect(
      shouldShowIosInstallBanner({
        isWeb: true,
        userAgent: iosSafariUa,
        isStandalone: true,
        dismissed: false,
      })
    ).toBe(false);
    expect(
      shouldShowIosInstallBanner({
        isWeb: true,
        userAgent: iosSafariUa,
        isStandalone: false,
        dismissed: true,
      })
    ).toBe(false);
    expect(
      shouldShowIosInstallBanner({
        isWeb: false,
        userAgent: iosSafariUa,
        isStandalone: false,
        dismissed: false,
      })
    ).toBe(false);
    expect(
      shouldShowIosInstallBanner({
        isWeb: true,
        userAgent: iosChromeUa,
        isStandalone: false,
        dismissed: false,
      })
    ).toBe(false);
    expect(
      shouldShowIosInstallBanner({
        isWeb: true,
        userAgent: androidChromeUa,
        isStandalone: false,
        dismissed: false,
      })
    ).toBe(false);
  });
});
