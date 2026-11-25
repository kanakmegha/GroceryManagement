import React, { createContext, useContext, useMemo, useState } from 'react';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { getPublicEnvConfig } from '../../shared/config/env';
import { DEMO_CAPABILITIES, DemoCapabilities } from './demoCapabilities';
import { createDemoApiClient, DemoApiClient } from './demoApiClient';

interface DemoContextValue {
  client: DemoApiClient;
  capabilities: DemoCapabilities;
}

const DemoContext = createContext<DemoContextValue | undefined>(undefined);

/**
 * Demo Mode provider. Owns its OWN QueryClient and API client so demo cache can never mix
 * with the authenticated QueryClient (which lives in a different, mutually exclusive tree).
 */
export function DemoProvider({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: 1, staleTime: 60_000, refetchOnWindowFocus: false } },
      })
  );
  const value = useMemo<DemoContextValue>(() => {
    const { apiUrl } = getPublicEnvConfig();
    return { client: createDemoApiClient({ baseUrl: apiUrl }), capabilities: DEMO_CAPABILITIES };
  }, []);
  return (
    <QueryClientProvider client={queryClient}>
      <DemoContext.Provider value={value}>{children}</DemoContext.Provider>
    </QueryClientProvider>
  );
}

export function useDemo(): DemoContextValue {
  const ctx = useContext(DemoContext);
  if (!ctx) {
    throw new Error('useDemo must be used within a DemoProvider');
  }
  return ctx;
}

/** All demo data hooks go through here: GET-only, demo-prefixed query key. */
export function useDemoQuery<T>(path: string) {
  const { client } = useDemo();
  return useQuery<T>({
    queryKey: ['demo', path],
    queryFn: ({ signal }) => client.get<T>(path, { signal }),
  });
}
