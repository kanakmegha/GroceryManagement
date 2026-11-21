import { executeSsoSignIn } from '../ssoFlow';

describe('executeSsoSignIn', () => {
  it('activates session on createdSessionId success', async () => {
    const setActive = jest.fn(async () => undefined);
    const startSSOFlow = jest.fn(async () => ({
      createdSessionId: 'sess_123',
      setActive,
      authSessionResult: { type: 'success' },
    }));

    const outcome = await executeSsoSignIn(
      startSSOFlow,
      'oauth_google',
      'exp://192.168.1.31:8082/--/sso-callback'
    );

    expect(outcome).toEqual({ status: 'success', message: null });
    expect(startSSOFlow).toHaveBeenCalledWith({
      strategy: 'oauth_google',
      redirectUrl: 'exp://192.168.1.31:8082/--/sso-callback',
    });
    expect(setActive).toHaveBeenCalledWith({ session: 'sess_123' });
  });

  it('handles user cancellation cleanly without hanging', async () => {
    const startSSOFlow = jest.fn(async () => ({
      createdSessionId: null,
      setActive: jest.fn(),
      authSessionResult: { type: 'cancel' },
    }));

    const outcome = await executeSsoSignIn(
      startSSOFlow,
      'oauth_google',
      'exp://192.168.1.31:8082/--/sso-callback'
    );

    expect(outcome.status).toBe('cancelled');
    expect(outcome.message).toContain('cancelled');
  });

  it('handles dismiss callback cleanly', async () => {
    const startSSOFlow = jest.fn(async () => ({
      createdSessionId: null,
      setActive: jest.fn(),
      authSessionResult: { type: 'dismiss' },
    }));

    const outcome = await executeSsoSignIn(
      startSSOFlow,
      'oauth_google',
      'exp://192.168.1.31:8082/--/sso-callback'
    );

    expect(outcome.status).toBe('cancelled');
  });

  it('handles missing requirements cleanly', async () => {
    const startSSOFlow = jest.fn(async () => ({
      createdSessionId: null,
      setActive: jest.fn(),
      signUp: { status: 'missing_requirements' },
    }));

    const outcome = await executeSsoSignIn(
      startSSOFlow,
      'oauth_google',
      'exp://192.168.1.31:8082/--/sso-callback'
    );

    expect(outcome.status).toBe('incomplete');
    expect(outcome.message).toContain('Additional account verification');
  });

  it('surfaces thrown OAuth errors so user can retry', async () => {
    const startSSOFlow = jest.fn(async () => {
      throw new Error('Redirect URL is not allowed');
    });

    const outcome = await executeSsoSignIn(
      startSSOFlow,
      'oauth_google',
      'exp://192.168.1.31:8082/--/sso-callback'
    );

    expect(outcome).toEqual({
      status: 'error',
      message: 'Redirect URL is not allowed',
    });
  });
});
