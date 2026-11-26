import { useMemo, useRef } from 'react';
import { useAuth } from '@clerk/expo';
import { ApiClient, createApiClient } from '../../shared/api/client';
import { getPublicEnvConfig } from '../../shared/config/env';
import { useHousehold } from './HouseholdContext';

export function useApiClient(): ApiClient {
  const { getToken } = useAuth();
  const { activeHouseholdId } = useHousehold();

  // @clerk/clerk-expo's useAuth() returns a new getToken closure on every render.
  // Keeping it in a ref prevents apiClient from changing identity and re-triggering effects.
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  const householdIdRef = useRef<string | null>(activeHouseholdId);
  householdIdRef.current = activeHouseholdId;

  const { apiUrl } = getPublicEnvConfig();

  return useMemo(() => {
    return createApiClient({
      baseUrl: apiUrl,
      getToken: (opts) => getTokenRef.current(opts),
      getHouseholdId: () => householdIdRef.current,
    });
  }, [apiUrl]);
}
