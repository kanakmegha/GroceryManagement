import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useAuth } from '@clerk/expo';
import { ApiClientError, createApiClient } from '../../shared/api/client';
import { AuthMeResponse, AuthUser, Household } from '../../shared/api/types';
import { ErrorState, LoadingState, ScreenContainer } from '../../shared/components';
import { getPublicEnvConfig } from '../../shared/config/env';
import {
  traceApiRequest,
  traceMount,
  traceRender,
  traceTransition,
  traceUnmount,
} from '../../shared/diagnostics/perfTrace';

export interface HouseholdContextValue {
  user: AuthUser | null;
  household: Household | null;
  households: Household[];
  activeHouseholdId: string | null;
  setActiveHouseholdId: (householdId: string) => void;
  isLoading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

const HouseholdContext = createContext<HouseholdContextValue | undefined>(undefined);

export function useHousehold(): HouseholdContextValue {
  const ctx = useContext(HouseholdContext);
  if (!ctx) {
    throw new Error('useHousehold must be used within a HouseholdBootstrapGate');
  }
  return ctx;
}

export interface HouseholdBootstrapGateProps {
  children: React.ReactNode;
}

export function HouseholdBootstrapGate({ children }: HouseholdBootstrapGateProps) {
  const { getToken, signOut, isLoaded, isSignedIn, userId } = useAuth();
  traceRender('HouseholdBootstrapGate');

  const [authMe, setAuthMe] = useState<AuthMeResponse | null>(null);
  const [activeHouseholdId, setActiveHouseholdIdState] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Store unstable Clerk callbacks and mutable state in refs so bootstrap identity stays stable.
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  const activeHouseholdIdRef = useRef<string | null>(null);
  activeHouseholdIdRef.current = activeHouseholdId;

  const authMeRef = useRef<AuthMeResponse | null>(null);
  authMeRef.current = authMe;

  const inFlightRef = useRef<Promise<void> | null>(null);
  const bootstrappedUserRef = useRef<string | null>(null);

  useEffect(() => {
    traceMount('HouseholdBootstrapGate');
    return () => traceUnmount('HouseholdBootstrapGate');
  }, []);

  const bootstrap = useCallback(
    async (reason = 'manual_refetch') => {
      if (!isLoaded || !isSignedIn) {
        return;
      }

      if (inFlightRef.current) {
        return inFlightRef.current;
      }

      const run = (async () => {
        if (!authMeRef.current) {
          setIsLoading(true);
        }
        setError(null);
        traceTransition('HouseholdBootstrap', `fetch_start (${reason})`);
        traceApiRequest('/api/v1/auth/me', reason);

        try {
          const { apiUrl } = getPublicEnvConfig();
          const client = createApiClient({
            baseUrl: apiUrl,
            getToken: (opts) => getTokenRef.current(opts),
            getHouseholdId: () => activeHouseholdIdRef.current,
          });

          const data = await client.get<AuthMeResponse>('/api/v1/auth/me');
          setAuthMe(data);
          const initialHousehold = data.active_household ?? data.households[0] ?? null;
          setActiveHouseholdIdState((prev) => prev ?? initialHousehold?.id ?? null);
          bootstrappedUserRef.current = userId ?? data.user.clerk_user_id;
          traceTransition('HouseholdBootstrap', 'fetch_success');
        } catch (err) {
          const message =
            err instanceof ApiClientError || err instanceof Error
              ? err.message
              : 'Unable to load your household profile.';
          setError(message);
          traceTransition('HouseholdBootstrap', `fetch_error: ${message}`);
        } finally {
          setIsLoading(false);
          inFlightRef.current = null;
        }
      })();

      inFlightRef.current = run;
      return run;
    },
    [isLoaded, isSignedIn, userId]
  );

  useEffect(() => {
    if (!isLoaded || !isSignedIn) {
      bootstrappedUserRef.current = null;
      return;
    }
    const sessionKey = userId ?? 'signed_in';
    if (bootstrappedUserRef.current === sessionKey && authMeRef.current) {
      return;
    }
    void bootstrap('session_init');
  }, [isLoaded, isSignedIn, userId, bootstrap]);

  const setActiveHouseholdId = useCallback((householdId: string) => {
    traceTransition('HouseholdBootstrap', `switch_household=${householdId}`);
    setActiveHouseholdIdState(householdId);
    setAuthMe((prev) => {
      if (!prev) {
        return prev;
      }
      const matched = prev.households.find((h) => h.id === householdId);
      if (!matched) {
        return prev;
      }
      return {
        ...prev,
        active_household: matched,
      };
    });
  }, []);

  const value = useMemo<HouseholdContextValue>(
    () => ({
      user: authMe?.user ?? null,
      household: authMe?.active_household ?? null,
      households: authMe?.households ?? [],
      activeHouseholdId,
      setActiveHouseholdId,
      isLoading,
      error,
      refetch: () => bootstrap('explicit_refetch'),
    }),
    [authMe, activeHouseholdId, setActiveHouseholdId, isLoading, error, bootstrap]
  );

  if (isLoading && !authMe) {
    return (
      <ScreenContainer>
        <LoadingState message="Setting up your kitchen household..." />
      </ScreenContainer>
    );
  }

  if (error && !authMe) {
    return (
      <ScreenContainer>
        <ErrorState
          title="Could not load household"
          message={error}
          onRetry={() => {
            void bootstrap('retry_button');
          }}
          retryLabel="Retry Connection"
          secondaryActionLabel="Sign Out"
          onSecondaryAction={() => {
            void signOut();
          }}
        />
      </ScreenContainer>
    );
  }

  return (
    <HouseholdContext.Provider value={value}>
      {children}
    </HouseholdContext.Provider>
  );
}
