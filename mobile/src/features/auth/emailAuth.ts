export interface ClerkErrorItem {
  code?: string;
  message?: string;
  longMessage?: string;
}

export interface ClerkApiErrorLike {
  clerkError?: boolean;
  errors?: ClerkErrorItem[];
}

export interface SignInFactor {
  strategy: string;
  emailAddressId?: string;
}

export interface SignInResultLike {
  status: string | null;
  createdSessionId?: string | null;
  supportedFirstFactors?: SignInFactor[] | null;
}

export interface SignInResourceLike {
  create: (params: {
    identifier: string;
    password?: string;
  }) => Promise<SignInResultLike>;
  prepareFirstFactor: (params: {
    strategy: 'email_code';
    emailAddressId: string;
  }) => Promise<SignInResultLike>;
  attemptFirstFactor: (params: {
    strategy: 'email_code';
    code: string;
  }) => Promise<SignInResultLike>;
}

export interface SignUpResultLike {
  status: string | null;
  createdSessionId?: string | null;
}

export interface SignUpResourceLike {
  create: (params: {
    emailAddress: string;
    password?: string;
  }) => Promise<SignUpResultLike>;
  prepareEmailAddressVerification: (params: {
    strategy: 'email_code';
  }) => Promise<SignUpResultLike>;
  attemptEmailAddressVerification: (params: {
    code: string;
  }) => Promise<SignUpResultLike>;
}

export type SetActiveFn = (params: { session: string }) => Promise<void>;

export type AuthFlowOutcome =
  | { status: 'complete' }
  | { status: 'needs_code'; emailAddressId?: string }
  | { status: 'error'; message: string };

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

export function isValidEmail(email: string): boolean {
  return EMAIL_PATTERN.test(normalizeEmail(email));
}

function isClerkErrorShape(err: unknown): err is ClerkApiErrorLike {
  return (
    typeof err === 'object' &&
    err !== null &&
    'clerkError' in err &&
    (err as ClerkApiErrorLike).clerkError === true &&
    Array.isArray((err as ClerkApiErrorLike).errors)
  );
}

export function extractClerkErrorMessage(
  err: unknown,
  isClerkApiError?: (error: unknown) => boolean,
  fallback = 'Something went wrong. Please try again.'
): string {
  const matchedClerk = isClerkApiError ? isClerkApiError(err) : isClerkErrorShape(err);
  if (matchedClerk || isClerkErrorShape(err)) {
    const errors = (err as ClerkApiErrorLike).errors;
    const first = Array.isArray(errors) ? errors[0] : undefined;
    const message = first?.longMessage || first?.message;
    if (message) {
      return message;
    }
  }

  if (err instanceof Error && err.message) {
    return err.message;
  }

  return fallback;
}

export function findEmailCodeFactor(
  supportedFirstFactors?: SignInFactor[] | null
): { strategy: 'email_code'; emailAddressId: string } | null {
  if (!Array.isArray(supportedFirstFactors)) {
    return null;
  }

  const match = supportedFirstFactors.find(
    (factor): factor is { strategy: 'email_code'; emailAddressId: string } =>
      factor.strategy === 'email_code' &&
      typeof factor.emailAddressId === 'string' &&
      factor.emailAddressId.length > 0
  );

  return match ?? null;
}

export async function startEmailOtpSignIn(
  signIn: SignInResourceLike,
  setActive: SetActiveFn,
  email: string,
  isClerkApiError?: (error: unknown) => boolean
): Promise<AuthFlowOutcome> {
  try {
    const result = await signIn.create({ identifier: normalizeEmail(email) });

    if (result.status === 'complete' && result.createdSessionId) {
      await setActive({ session: result.createdSessionId });
      return { status: 'complete' };
    }

    const emailCodeFactor = findEmailCodeFactor(result.supportedFirstFactors);
    if (!emailCodeFactor) {
      return {
        status: 'error',
        message:
          'Email verification code is not available for this account. Try Password or Continue with Google.',
      };
    }

    await signIn.prepareFirstFactor({
      strategy: 'email_code',
      emailAddressId: emailCodeFactor.emailAddressId,
    });

    return { status: 'needs_code', emailAddressId: emailCodeFactor.emailAddressId };
  } catch (err) {
    return {
      status: 'error',
      message: extractClerkErrorMessage(
        err,
        isClerkApiError,
        'Unable to send verification code. Please try again.'
      ),
    };
  }
}

export async function resendEmailOtpSignIn(
  signIn: SignInResourceLike,
  emailAddressId: string,
  isClerkApiError?: (error: unknown) => boolean
): Promise<AuthFlowOutcome> {
  try {
    await signIn.prepareFirstFactor({
      strategy: 'email_code',
      emailAddressId,
    });
    return { status: 'needs_code', emailAddressId };
  } catch (err) {
    return {
      status: 'error',
      message: extractClerkErrorMessage(
        err,
        isClerkApiError,
        'Unable to resend verification code. Please try again.'
      ),
    };
  }
}

export async function verifyEmailOtpSignIn(
  signIn: SignInResourceLike,
  setActive: SetActiveFn,
  code: string,
  isClerkApiError?: (error: unknown) => boolean
): Promise<AuthFlowOutcome> {
  try {
    const result = await signIn.attemptFirstFactor({
      strategy: 'email_code',
      code: code.trim(),
    });

    if (result.status === 'complete' && result.createdSessionId) {
      await setActive({ session: result.createdSessionId });
      return { status: 'complete' };
    }

    return {
      status: 'error',
      message: 'Additional verification is required to complete sign-in.',
    };
  } catch (err) {
    return {
      status: 'error',
      message: extractClerkErrorMessage(
        err,
        isClerkApiError,
        'Invalid or expired verification code. Please try again.'
      ),
    };
  }
}

export async function signInWithPassword(
  signIn: SignInResourceLike,
  setActive: SetActiveFn,
  email: string,
  password: string,
  isClerkApiError?: (error: unknown) => boolean
): Promise<AuthFlowOutcome> {
  try {
    const result = await signIn.create({
      identifier: normalizeEmail(email),
      password,
    });

    if (result.status === 'complete' && result.createdSessionId) {
      await setActive({ session: result.createdSessionId });
      return { status: 'complete' };
    }

    if (result.status === 'needs_second_factor') {
      return {
        status: 'error',
        message:
          'Two-factor authentication is enabled on this account. Please sign in with Email Code or Google.',
      };
    }

    if (result.status === 'needs_first_factor') {
      return {
        status: 'error',
        message:
          'Additional sign-in verification is required. Please use Email Code or Continue with Google.',
      };
    }

    return {
      status: 'error',
      message: 'Sign-in could not be completed. Please try again.',
    };
  } catch (err) {
    return {
      status: 'error',
      message: extractClerkErrorMessage(
        err,
        isClerkApiError,
        'Unable to sign in. Check your email and password and try again.'
      ),
    };
  }
}

export async function startEmailSignUp(
  signUp: SignUpResourceLike,
  setActive: SetActiveFn,
  email: string,
  password: string,
  isClerkApiError?: (error: unknown) => boolean
): Promise<AuthFlowOutcome> {
  try {
    const trimmedPassword = password.trim();
    const result = await signUp.create({
      emailAddress: normalizeEmail(email),
      ...(trimmedPassword.length > 0 ? { password } : {}),
    });

    if (result.status === 'complete' && result.createdSessionId) {
      await setActive({ session: result.createdSessionId });
      return { status: 'complete' };
    }

    await signUp.prepareEmailAddressVerification({ strategy: 'email_code' });
    return { status: 'needs_code' };
  } catch (err) {
    return {
      status: 'error',
      message: extractClerkErrorMessage(
        err,
        isClerkApiError,
        'Unable to create account. Please check your details and try again.'
      ),
    };
  }
}

export async function resendEmailSignUpCode(
  signUp: SignUpResourceLike,
  isClerkApiError?: (error: unknown) => boolean
): Promise<AuthFlowOutcome> {
  try {
    await signUp.prepareEmailAddressVerification({ strategy: 'email_code' });
    return { status: 'needs_code' };
  } catch (err) {
    return {
      status: 'error',
      message: extractClerkErrorMessage(
        err,
        isClerkApiError,
        'Unable to resend verification code. Please try again.'
      ),
    };
  }
}

export async function verifyEmailSignUp(
  signUp: SignUpResourceLike,
  setActive: SetActiveFn,
  code: string,
  isClerkApiError?: (error: unknown) => boolean
): Promise<AuthFlowOutcome> {
  try {
    const result = await signUp.attemptEmailAddressVerification({
      code: code.trim(),
    });

    if (result.status === 'complete' && result.createdSessionId) {
      await setActive({ session: result.createdSessionId });
      return { status: 'complete' };
    }

    return {
      status: 'error',
      message: 'Account verification is not complete yet. Please try again.',
    };
  } catch (err) {
    return {
      status: 'error',
      message: extractClerkErrorMessage(
        err,
        isClerkApiError,
        'Invalid or expired verification code. Please try again.'
      ),
    };
  }
}
