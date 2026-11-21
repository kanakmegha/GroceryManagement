import { extractClerkErrorMessage } from './emailAuth';

export type SsoStrategy = 'oauth_google' | 'oauth_apple';

export interface SsoFlowResult {
  createdSessionId?: string | null;
  setActive?: ((params: { session: string }) => Promise<void>) | null;
  authSessionResult?: { type?: string } | null;
  signIn?: { status?: string | null } | null;
  signUp?: { status?: string | null } | null;
}

export interface ExecuteSsoOutcome {
  status: 'success' | 'cancelled' | 'incomplete' | 'error';
  message: string | null;
}

export async function executeSsoSignIn(
  startSSOFlow: (params: {
    strategy: SsoStrategy;
    redirectUrl: string;
  }) => Promise<SsoFlowResult>,
  strategy: SsoStrategy,
  redirectUrl: string
): Promise<ExecuteSsoOutcome> {
  try {
    const result = await startSSOFlow({
      strategy,
      redirectUrl,
    });

    if (result.createdSessionId && result.setActive) {
      await result.setActive({ session: result.createdSessionId });
      return { status: 'success', message: null };
    }

    const authType = result.authSessionResult?.type;
    if (authType === 'cancel' || authType === 'dismiss') {
      return {
        status: 'cancelled',
        message: 'Sign-in was cancelled. Tap Continue with Google to try again.',
      };
    }

    const pendingStatus = result.signUp?.status ?? result.signIn?.status ?? null;
    if (pendingStatus === 'missing_requirements') {
      return {
        status: 'incomplete',
        message: 'Additional account verification is required in Clerk to finish sign-in.',
      };
    }

    return {
      status: 'incomplete',
      message: 'Sign-in was not completed. Please try again.',
    };
  } catch (err) {
    const message = extractClerkErrorMessage(
      err,
      undefined,
      'Unable to complete sign-in. Please try again.'
    );
    return { status: 'error', message };
  }
}
