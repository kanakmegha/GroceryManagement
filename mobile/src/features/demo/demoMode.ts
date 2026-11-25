/**
 * Explicit application-mode helpers for the public Demo Mode.
 *
 * Demo Mode is selected ONLY by route (`/demo/*`) and never mixes with the authenticated tree.
 * This module is pure (no Clerk, no React) so it can be unit tested in node.
 */

export const DEMO_HOME_HREF = '/demo';
export const AUTH_INTENT_KEY = 'gm_auth_intent';

export function isDemoPath(pathname: string): boolean {
  return pathname === '/demo' || pathname.startsWith('/demo/');
}

export type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

function defaultStorage(): StorageLike | null {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      return window.localStorage;
    }
  } catch {
    // Storage may be blocked (private mode); treat as unavailable.
  }
  return null;
}

/** True when the visitor explicitly chose to sign in to their own account. */
export function hasAuthIntent(storage: StorageLike | null = defaultStorage()): boolean {
  try {
    return storage?.getItem(AUTH_INTENT_KEY) === '1';
  } catch {
    return false;
  }
}

export function setAuthIntent(storage: StorageLike | null = defaultStorage()): void {
  try {
    storage?.setItem(AUTH_INTENT_KEY, '1');
  } catch {
    // ignore
  }
}

export function clearAuthIntent(storage: StorageLike | null = defaultStorage()): void {
  try {
    storage?.removeItem(AUTH_INTENT_KEY);
  } catch {
    // ignore
  }
}

export function shouldRedirectToDemo(params: {
  demoAvailable: boolean;
  pathname: string;
  authIntent: boolean;
}): boolean {
  const atRoot = params.pathname === '/' || params.pathname === '' || params.pathname === '/index';
  return params.demoAvailable && atRoot && !params.authIntent;
}
