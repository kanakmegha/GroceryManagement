import React, { useState } from 'react';
import { StatusBar } from 'expo-status-bar';
import { Platform, View } from 'react-native';
import { Redirect, Slot, usePathname, type ErrorBoundaryProps } from 'expo-router';
import { DemoEntryLink } from '../src/features/demo/DemoEntryLink';
import {
  DEMO_HOME_HREF,
  hasAuthIntent,
  isDemoPath,
  shouldRedirectToDemo,
} from '../src/features/demo/demoMode';
import { ClerkLoaded, ClerkLoading, ClerkProvider, Show } from '@clerk/expo';
import { tokenCache } from '@clerk/expo/token-cache';
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

  const { clerkPublishableKey, demoModeEnabled } = getPublicEnvConfig();
  const pathname = usePathname();

  // Explicit application modes. Demo Mode never mounts Clerk or the authenticated QueryClient,
  // so anonymous browsing has no auth prerequisite and no cache shared with real households.
  if (demoModeEnabled && isDemoPath(pathname)) {
    return (
      <SafeAreaProvider>
        <StatusBar style="dark" />
        <Slot />
      </SafeAreaProvider>
    );
  }
  if (
    demoModeEnabled &&
    Platform.OS === 'web' &&
    shouldRedirectToDemo({ demoAvailable: true, pathname, authIntent: hasAuthIntent() })
  ) {
    return <Redirect href={DEMO_HOME_HREF as never} />;
  }

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
            <Show when="signed-in">
              <HouseholdBootstrapGate>
                <Slot />
              </HouseholdBootstrapGate>
            </Show>
            <Show when="signed-out">
              <View style={{ flex: 1 }}>
                <View style={{ flex: 1 }}>
                  <SignInScreen />
                </View>
                {demoModeEnabled ? <DemoEntryLink /> : null}
              </View>
            </Show>
          </ClerkLoaded>
        </ClerkProvider>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}
