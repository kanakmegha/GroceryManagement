import React, { useState } from 'react';
import { StatusBar } from 'expo-status-bar';
import { Slot, type ErrorBoundaryProps } from 'expo-router';
import { ClerkLoaded, ClerkLoading, ClerkProvider, SignedIn, SignedOut } from '@clerk/clerk-expo';
import { tokenCache } from '@clerk/clerk-expo/token-cache';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { HouseholdBootstrapGate } from '../src/features/auth/HouseholdContext';
import { SignInScreen } from '../src/features/auth/SignInScreen';
import { ErrorState, LoadingState, ScreenContainer } from '../src/shared/components';
import { getPublicEnvConfig } from '../src/shared/config/env';

export function ErrorBoundary({ error, retry }: ErrorBoundaryProps) {
  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <ScreenContainer>
        <ErrorState
          title="Unable to start Grocery Management"
          message={error?.message || 'An unexpected initialization error occurred.'}
          onRetry={retry}
        />
      </ScreenContainer>
    </SafeAreaProvider>
  );
}

export default function RootLayout() {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            staleTime: 30_000,
          },
        },
      })
  );

  const { clerkPublishableKey } = getPublicEnvConfig();

  if (!clerkPublishableKey || clerkPublishableKey.includes('...')) {
    return (
      <SafeAreaProvider>
        <StatusBar style="dark" />
        <ScreenContainer>
          <ErrorState
            title="Clerk configuration missing"
            message="Set a valid EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY in mobile/.env and restart Expo to enable authentication."
          />
        </ScreenContainer>
      </SafeAreaProvider>
    );
  }

  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <QueryClientProvider client={queryClient}>
        <ClerkProvider publishableKey={clerkPublishableKey} tokenCache={tokenCache}>
          <ClerkLoading>
            <ScreenContainer>
              <LoadingState message="Connecting to authentication..." />
            </ScreenContainer>
          </ClerkLoading>
          <ClerkLoaded>
            <SignedIn>
              <HouseholdBootstrapGate>
                <Slot />
              </HouseholdBootstrapGate>
            </SignedIn>
            <SignedOut>
              <SignInScreen />
            </SignedOut>
          </ClerkLoaded>
        </ClerkProvider>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}
