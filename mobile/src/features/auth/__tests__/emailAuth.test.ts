import {
  extractClerkErrorMessage,
  findEmailCodeFactor,
  isValidEmail,
  normalizeEmail,
  resendEmailOtpSignIn,
  resendEmailSignUpCode,
  signInWithPassword,
  startEmailOtpSignIn,
  startEmailSignUp,
  verifyEmailOtpSignIn,
  verifyEmailSignUp,
  type SignInResourceLike,
  type SignUpResourceLike,
} from '../emailAuth';

describe('emailAuth helpers', () => {
  describe('normalizeEmail and isValidEmail', () => {
    it('trims and lowercases email addresses', () => {
      expect(normalizeEmail('  Alice@Example.COM  ')).toBe('alice@example.com');
    });

    it('validates email format', () => {
      expect(isValidEmail('alice@example.com')).toBe(true);
      expect(isValidEmail('  alice+test@sub.domain.org ')).toBe(true);
      expect(isValidEmail('not-an-email')).toBe(false);
      expect(isValidEmail('')).toBe(false);
    });
  });

  describe('extractClerkErrorMessage', () => {
    it('returns longMessage from Clerk API error when present', () => {
      const err = {
        clerkError: true,
        errors: [
          {
            code: 'form_password_pwned',
            message: 'Password is compromised.',
            longMessage: 'Password has been found in an online data breach. Please use another password.',
          },
        ],
      };

      expect(extractClerkErrorMessage(err)).toBe(
        'Password has been found in an online data breach. Please use another password.'
      );
    });

    it('falls back to message when longMessage is missing', () => {
      const err = {
        clerkError: true,
        errors: [{ code: 'form_identifier_not_found', message: 'Account not found.' }],
      };

      expect(extractClerkErrorMessage(err)).toBe('Account not found.');
    });

    it('uses custom isClerkAPIResponseError predicate when provided', () => {
      const err = {
        errors: [{ longMessage: 'Custom predicate Clerk error.' }],
      };

      expect(extractClerkErrorMessage(err, () => true)).toBe('Custom predicate Clerk error.');
    });

    it('falls back to standard Error message and default fallback', () => {
      expect(extractClerkErrorMessage(new Error('Network offline'))).toBe('Network offline');
      expect(extractClerkErrorMessage({ clerkError: true, errors: [] }, undefined, 'Fallback')).toBe(
        'Fallback'
      );
      expect(extractClerkErrorMessage(null, undefined, 'Fallback')).toBe('Fallback');
    });
  });

  describe('findEmailCodeFactor', () => {
    it('finds email_code factor with emailAddressId', () => {
      const factor = findEmailCodeFactor([
        { strategy: 'password' },
        { strategy: 'email_code', emailAddressId: 'idn_123' },
      ]);

      expect(factor).toEqual({ strategy: 'email_code', emailAddressId: 'idn_123' });
    });

    it('returns null when missing or lacking emailAddressId', () => {
      expect(findEmailCodeFactor(null)).toBeNull();
      expect(findEmailCodeFactor([])).toBeNull();
      expect(findEmailCodeFactor([{ strategy: 'password' }])).toBeNull();
      expect(findEmailCodeFactor([{ strategy: 'email_code' }])).toBeNull();
    });
  });
});

describe('Email OTP Sign In', () => {
  it('starts email OTP flow and prepares first factor', async () => {
    const signIn: SignInResourceLike = {
      create: jest.fn(async () => ({
        status: 'needs_first_factor',
        supportedFirstFactors: [{ strategy: 'email_code', emailAddressId: 'idn_abc' }],
      })),
      prepareFirstFactor: jest.fn(async () => ({ status: 'needs_first_factor' })),
      attemptFirstFactor: jest.fn(),
    };
    const setActive = jest.fn(async () => undefined);

    const outcome = await startEmailOtpSignIn(signIn, setActive, '  User@Example.com ');

    expect(signIn.create).toHaveBeenCalledWith({ identifier: 'user@example.com' });
    expect(signIn.prepareFirstFactor).toHaveBeenCalledWith({
      strategy: 'email_code',
      emailAddressId: 'idn_abc',
    });
    expect(outcome).toEqual({ status: 'needs_code', emailAddressId: 'idn_abc' });
  });

  it('returns error when email_code factor is unavailable', async () => {
    const signIn: SignInResourceLike = {
      create: jest.fn(async () => ({
        status: 'needs_first_factor',
        supportedFirstFactors: [{ strategy: 'password' }],
      })),
      prepareFirstFactor: jest.fn(),
      attemptFirstFactor: jest.fn(),
    };
    const setActive = jest.fn(async () => undefined);

    const outcome = await startEmailOtpSignIn(signIn, setActive, 'user@example.com');

    expect(outcome.status).toBe('error');
    if (outcome.status === 'error') {
      expect(outcome.message).toContain('Email verification code is not available');
    }
  });

  it('verifies OTP and activates session', async () => {
    const signIn: SignInResourceLike = {
      create: jest.fn(),
      prepareFirstFactor: jest.fn(),
      attemptFirstFactor: jest.fn(async () => ({
        status: 'complete',
        createdSessionId: 'sess_otp_1',
      })),
    };
    const setActive = jest.fn(async () => undefined);

    const outcome = await verifyEmailOtpSignIn(signIn, setActive, ' 123456 ');

    expect(signIn.attemptFirstFactor).toHaveBeenCalledWith({
      strategy: 'email_code',
      code: '123456',
    });
    expect(setActive).toHaveBeenCalledWith({ session: 'sess_otp_1' });
    expect(outcome).toEqual({ status: 'complete' });
  });

  it('resends sign-in OTP code', async () => {
    const signIn: SignInResourceLike = {
      create: jest.fn(),
      prepareFirstFactor: jest.fn(async () => ({ status: 'needs_first_factor' })),
      attemptFirstFactor: jest.fn(),
    };

    const outcome = await resendEmailOtpSignIn(signIn, 'idn_abc');

    expect(signIn.prepareFirstFactor).toHaveBeenCalledWith({
      strategy: 'email_code',
      emailAddressId: 'idn_abc',
    });
    expect(outcome).toEqual({ status: 'needs_code', emailAddressId: 'idn_abc' });
  });
});

describe('Password Sign In', () => {
  it('signs in with password and activates session', async () => {
    const signIn: SignInResourceLike = {
      create: jest.fn(async () => ({
        status: 'complete',
        createdSessionId: 'sess_pw_1',
      })),
      prepareFirstFactor: jest.fn(),
      attemptFirstFactor: jest.fn(),
    };
    const setActive = jest.fn(async () => undefined);

    const outcome = await signInWithPassword(signIn, setActive, 'User@Test.com', 'supersecret12345');

    expect(signIn.create).toHaveBeenCalledWith({
      identifier: 'user@test.com',
      password: 'supersecret12345',
    });
    expect(setActive).toHaveBeenCalledWith({ session: 'sess_pw_1' });
    expect(outcome).toEqual({ status: 'complete' });
  });

  it('handles needs_first_factor and needs_second_factor cleanly', async () => {
    const setActive = jest.fn(async () => undefined);

    const firstFactorSignIn: SignInResourceLike = {
      create: jest.fn(async () => ({ status: 'needs_first_factor' })),
      prepareFirstFactor: jest.fn(),
      attemptFirstFactor: jest.fn(),
    };
    const firstOutcome = await signInWithPassword(firstFactorSignIn, setActive, 'a@b.com', 'pw');
    expect(firstOutcome.status).toBe('error');
    if (firstOutcome.status === 'error') {
      expect(firstOutcome.message).toContain('Additional sign-in verification');
    }

    const secondFactorSignIn: SignInResourceLike = {
      create: jest.fn(async () => ({ status: 'needs_second_factor' })),
      prepareFirstFactor: jest.fn(),
      attemptFirstFactor: jest.fn(),
    };
    const secondOutcome = await signInWithPassword(secondFactorSignIn, setActive, 'a@b.com', 'pw');
    expect(secondOutcome.status).toBe('error');
    if (secondOutcome.status === 'error') {
      expect(secondOutcome.message).toContain('Two-factor authentication');
    }
  });

  it('surfaces Clerk API errors on failed password sign-in', async () => {
    const signIn: SignInResourceLike = {
      create: jest.fn(async () => {
        throw {
          clerkError: true,
          errors: [{ longMessage: 'Password is incorrect. Try again, or use another method.' }],
        };
      }),
      prepareFirstFactor: jest.fn(),
      attemptFirstFactor: jest.fn(),
    };
    const setActive = jest.fn(async () => undefined);

    const outcome = await signInWithPassword(signIn, setActive, 'a@b.com', 'wrong');
    expect(outcome).toEqual({
      status: 'error',
      message: 'Password is incorrect. Try again, or use another method.',
    });
  });
});

describe('Create Account (Sign Up)', () => {
  it('creates account without password when password is empty and prepares email OTP', async () => {
    const signUp: SignUpResourceLike = {
      create: jest.fn(async () => ({ status: 'missing_requirements' })),
      prepareEmailAddressVerification: jest.fn(async () => ({ status: 'missing_requirements' })),
      attemptEmailAddressVerification: jest.fn(),
    };
    const setActive = jest.fn(async () => undefined);

    const outcome = await startEmailSignUp(signUp, setActive, 'New@User.com', '');

    expect(signUp.create).toHaveBeenCalledWith({ emailAddress: 'new@user.com' });
    expect(signUp.prepareEmailAddressVerification).toHaveBeenCalledWith({
      strategy: 'email_code',
    });
    expect(outcome).toEqual({ status: 'needs_code' });
  });

  it('passes password when non-empty', async () => {
    const signUp: SignUpResourceLike = {
      create: jest.fn(async () => ({ status: 'missing_requirements' })),
      prepareEmailAddressVerification: jest.fn(async () => ({ status: 'missing_requirements' })),
      attemptEmailAddressVerification: jest.fn(),
    };
    const setActive = jest.fn(async () => undefined);

    await startEmailSignUp(signUp, setActive, 'new@user.com', 'very-strong-password-15');

    expect(signUp.create).toHaveBeenCalledWith({
      emailAddress: 'new@user.com',
      password: 'very-strong-password-15',
    });
  });

  it('verifies sign-up email code and activates session', async () => {
    const signUp: SignUpResourceLike = {
      create: jest.fn(),
      prepareEmailAddressVerification: jest.fn(),
      attemptEmailAddressVerification: jest.fn(async () => ({
        status: 'complete',
        createdSessionId: 'sess_signup_1',
      })),
    };
    const setActive = jest.fn(async () => undefined);

    const outcome = await verifyEmailSignUp(signUp, setActive, '654321');

    expect(signUp.attemptEmailAddressVerification).toHaveBeenCalledWith({ code: '654321' });
    expect(setActive).toHaveBeenCalledWith({ session: 'sess_signup_1' });
    expect(outcome).toEqual({ status: 'complete' });
  });

  it('resends sign-up verification code and surfaces errors', async () => {
    const okSignUp: SignUpResourceLike = {
      create: jest.fn(),
      prepareEmailAddressVerification: jest.fn(async () => ({ status: 'missing_requirements' })),
      attemptEmailAddressVerification: jest.fn(),
    };
    expect(await resendEmailSignUpCode(okSignUp)).toEqual({ status: 'needs_code' });

    const failingSignUp: SignUpResourceLike = {
      create: jest.fn(),
      prepareEmailAddressVerification: jest.fn(async () => {
        throw {
          clerkError: true,
          errors: [{ message: 'Too many requests.' }],
        };
      }),
      attemptEmailAddressVerification: jest.fn(),
    };
    expect(await resendEmailSignUpCode(failingSignUp)).toEqual({
      status: 'error',
      message: 'Too many requests.',
    });
  });
});
