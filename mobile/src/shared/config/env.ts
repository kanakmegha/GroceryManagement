export interface PublicEnvConfig {
  apiUrl: string;
  clerkPublishableKey: string;
}

function normalizeBaseUrl(rawUrl: string | undefined): string {
  const fallback = 'http://localhost:8000';
  const trimmed = (rawUrl ?? '').trim();
  if (!trimmed) {
    return fallback;
  }
  return trimmed.replace(/\/+$/, '');
}

export function getPublicEnvConfig(): PublicEnvConfig {
  return {
    apiUrl: normalizeBaseUrl(process.env.EXPO_PUBLIC_API_URL),
    clerkPublishableKey: (process.env.EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY ?? '').trim(),
  };
}
