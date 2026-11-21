import React from 'react';
import * as WebBrowser from 'expo-web-browser';
import { LoadingState, ScreenContainer } from '../src/shared/components';

WebBrowser.maybeCompleteAuthSession();

export default function SsoCallbackScreen() {
  return (
    <ScreenContainer>
      <LoadingState message="Completing sign-in..." />
    </ScreenContainer>
  );
}
