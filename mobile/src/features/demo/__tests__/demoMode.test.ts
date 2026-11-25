import {
  AUTH_INTENT_KEY,
  clearAuthIntent,
  hasAuthIntent,
  isDemoPath,
  setAuthIntent,
  shouldRedirectToDemo,
  type StorageLike,
} from '../demoMode';
import { DEMO_CAPABILITIES } from '../demoCapabilities';

function memoryStorage(): StorageLike & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
  };
}

describe('demo mode routing', () => {
  it('identifies demo paths only under /demo', () => {
    expect(isDemoPath('/demo')).toBe(true);
    expect(isDemoPath('/demo/inventory')).toBe(true);
    expect(isDemoPath('/demonstration')).toBe(false);
    expect(isDemoPath('/')).toBe(false);
    expect(isDemoPath('/inventory')).toBe(false);
  });

  it('redirects anonymous visitors at root to demo only when enabled and without auth intent', () => {
    const base = { demoAvailable: true, pathname: '/', authIntent: false };
    expect(shouldRedirectToDemo(base)).toBe(true);
    expect(shouldRedirectToDemo({ ...base, demoAvailable: false })).toBe(false);
    expect(shouldRedirectToDemo({ ...base, authIntent: true })).toBe(false);
    expect(shouldRedirectToDemo({ ...base, pathname: '/inventory' })).toBe(false);
  });

  it('persists and clears sign-in intent', () => {
    const s = memoryStorage();
    expect(hasAuthIntent(s)).toBe(false);
    setAuthIntent(s);
    expect(s.data.get(AUTH_INTENT_KEY)).toBe('1');
    expect(hasAuthIntent(s)).toBe(true);
    clearAuthIntent(s);
    expect(hasAuthIntent(s)).toBe(false);
  });

  it('treats unavailable storage as no intent', () => {
    expect(hasAuthIntent(null)).toBe(false);
  });
});

describe('demo capabilities', () => {
  it('denies every mutating or paid capability', () => {
    expect(Object.values(DEMO_CAPABILITIES).every((v) => v === false)).toBe(true);
    expect(Object.isFrozen(DEMO_CAPABILITIES)).toBe(true);
  });
});
