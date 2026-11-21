import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import {
  isClerkAPIResponseError,
  useSignIn,
  useSignUp,
  useSSO,
} from '@clerk/clerk-expo';
import * as AuthSession from 'expo-auth-session';
import * as WebBrowser from 'expo-web-browser';
import { Ionicons } from '@expo/vector-icons';
import { ScreenContainer } from '../../shared/components';
import { colors, layout, radii, spacing } from '../../shared/theme/tokens';
import {
  isValidEmail,
  normalizeEmail,
  resendEmailOtpSignIn,
  resendEmailSignUpCode,
  signInWithPassword,
  startEmailOtpSignIn,
  startEmailSignUp,
  verifyEmailOtpSignIn,
  verifyEmailSignUp,
} from './emailAuth';
import { executeSsoSignIn } from './ssoFlow';

WebBrowser.maybeCompleteAuthSession();

type AuthMode = 'sign_in' | 'sign_up';
type SignInMethod = 'email_code' | 'password';
type AuthStep = 'form' | 'otp';
type SubmittingAction = 'email_auth' | 'resend' | 'google' | null;

export function SignInScreen() {
  const { signIn, setActive: setSignInActive, isLoaded: isSignInLoaded } = useSignIn();
  const { signUp, setActive: setSignUpActive, isLoaded: isSignUpLoaded } = useSignUp();
  const { startSSOFlow } = useSSO();

  const [mode, setMode] = useState<AuthMode>('sign_in');
  const [signInMethod, setSignInMethod] = useState<SignInMethod>('email_code');
  const [step, setStep] = useState<AuthStep>('form');

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [code, setCode] = useState('');
  const [signInEmailAddressId, setSignInEmailAddressId] = useState<string | null>(null);

  const [submittingAction, setSubmittingAction] = useState<SubmittingAction>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [infoMessage, setInfoMessage] = useState<string | null>(null);

  useEffect(() => {
    if (Platform.OS === 'web') {
      return;
    }
    void WebBrowser.warmUpAsync();
    return () => {
      void WebBrowser.coolDownAsync();
    };
  }, []);

  const isSubmitting = submittingAction !== null;

  const switchMode = useCallback(
    (nextMode: AuthMode) => {
      if (isSubmitting || nextMode === mode) {
        return;
      }
      setMode(nextMode);
      setStep('form');
      setCode('');
      setSignInEmailAddressId(null);
      setErrorMessage(null);
      setInfoMessage(null);
    },
    [isSubmitting, mode]
  );

  const switchSignInMethod = useCallback(
    (nextMethod: SignInMethod) => {
      if (isSubmitting || nextMethod === signInMethod) {
        return;
      }
      setSignInMethod(nextMethod);
      setStep('form');
      setCode('');
      setSignInEmailAddressId(null);
      setErrorMessage(null);
      setInfoMessage(null);
    },
    [isSubmitting, signInMethod]
  );

  const handleChangeEmail = useCallback(() => {
    if (isSubmitting) {
      return;
    }
    setStep('form');
    setCode('');
    setSignInEmailAddressId(null);
    setErrorMessage(null);
    setInfoMessage(null);
  }, [isSubmitting]);

  const handleSignInSubmit = useCallback(async () => {
    if (submittingAction !== null) {
      return;
    }
    if (!isSignInLoaded || !signIn || !setSignInActive) {
      setErrorMessage('Authentication is still initializing. Please try again in a moment.');
      return;
    }

    if (step === 'form') {
      if (!isValidEmail(email)) {
        setErrorMessage('Please enter a valid email address.');
        return;
      }
      if (signInMethod === 'password' && password.length === 0) {
        setErrorMessage('Please enter your password.');
        return;
      }

      setSubmittingAction('email_auth');
      setErrorMessage(null);
      setInfoMessage(null);

      try {
        if (signInMethod === 'email_code') {
          const outcome = await startEmailOtpSignIn(
            signIn,
            setSignInActive,
            email,
            isClerkAPIResponseError
          );
          if (outcome.status === 'needs_code') {
            setSignInEmailAddressId(outcome.emailAddressId ?? null);
            setStep('otp');
            setCode('');
            setInfoMessage(`We sent a 6-digit code to ${normalizeEmail(email)}.`);
          } else if (outcome.status === 'error') {
            setErrorMessage(outcome.message);
          }
        } else {
          const outcome = await signInWithPassword(
            signIn,
            setSignInActive,
            email,
            password,
            isClerkAPIResponseError
          );
          if (outcome.status === 'error') {
            setErrorMessage(outcome.message);
          }
        }
      } finally {
        setSubmittingAction(null);
      }
      return;
    }

    if (code.trim().length < 6) {
      setErrorMessage('Please enter the 6-digit verification code.');
      return;
    }

    setSubmittingAction('email_auth');
    setErrorMessage(null);
    setInfoMessage(null);

    try {
      const outcome = await verifyEmailOtpSignIn(
        signIn,
        setSignInActive,
        code,
        isClerkAPIResponseError
      );
      if (outcome.status === 'error') {
        setErrorMessage(outcome.message);
      }
    } finally {
      setSubmittingAction(null);
    }
  }, [
    code,
    email,
    isSignInLoaded,
    password,
    setSignInActive,
    signIn,
    signInMethod,
    step,
    submittingAction,
  ]);

  const handleSignUpSubmit = useCallback(async () => {
    if (submittingAction !== null) {
      return;
    }
    if (!isSignUpLoaded || !signUp || !setSignUpActive) {
      setErrorMessage('Authentication is still initializing. Please try again in a moment.');
      return;
    }

    if (step === 'form') {
      if (!isValidEmail(email)) {
        setErrorMessage('Please enter a valid email address.');
        return;
      }

      setSubmittingAction('email_auth');
      setErrorMessage(null);
      setInfoMessage(null);

      try {
        const outcome = await startEmailSignUp(
          signUp,
          setSignUpActive,
          email,
          password,
          isClerkAPIResponseError
        );
        if (outcome.status === 'needs_code') {
          setStep('otp');
          setCode('');
          setInfoMessage(`We sent a 6-digit code to ${normalizeEmail(email)}.`);
        } else if (outcome.status === 'error') {
          setErrorMessage(outcome.message);
        }
      } finally {
        setSubmittingAction(null);
      }
      return;
    }

    if (code.trim().length < 6) {
      setErrorMessage('Please enter the 6-digit verification code.');
      return;
    }

    setSubmittingAction('email_auth');
    setErrorMessage(null);
    setInfoMessage(null);

    try {
      const outcome = await verifyEmailSignUp(
        signUp,
        setSignUpActive,
        code,
        isClerkAPIResponseError
      );
      if (outcome.status === 'error') {
        setErrorMessage(outcome.message);
      }
    } finally {
      setSubmittingAction(null);
    }
  }, [
    code,
    email,
    isSignUpLoaded,
    password,
    setSignUpActive,
    signUp,
    step,
    submittingAction,
  ]);

  const handleResendCode = useCallback(async () => {
    if (submittingAction !== null) {
      return;
    }

    setSubmittingAction('resend');
    setErrorMessage(null);
    setInfoMessage(null);

    try {
      if (mode === 'sign_in') {
        if (!isSignInLoaded || !signIn || !signInEmailAddressId) {
          setErrorMessage('Unable to resend code. Please go back and try again.');
          return;
        }
        const outcome = await resendEmailOtpSignIn(
          signIn,
          signInEmailAddressId,
          isClerkAPIResponseError
        );
        if (outcome.status === 'error') {
          setErrorMessage(outcome.message);
        } else {
          setInfoMessage(`A new 6-digit code was sent to ${normalizeEmail(email)}.`);
        }
      } else {
        if (!isSignUpLoaded || !signUp) {
          setErrorMessage('Unable to resend code. Please go back and try again.');
          return;
        }
        const outcome = await resendEmailSignUpCode(signUp, isClerkAPIResponseError);
        if (outcome.status === 'error') {
          setErrorMessage(outcome.message);
        } else {
          setInfoMessage(`A new 6-digit code was sent to ${normalizeEmail(email)}.`);
        }
      }
    } finally {
      setSubmittingAction(null);
    }
  }, [
    email,
    isSignInLoaded,
    isSignUpLoaded,
    mode,
    signIn,
    signInEmailAddressId,
    signUp,
    submittingAction,
  ]);

  const handleGooglePress = useCallback(async () => {
    if (submittingAction !== null) {
      return;
    }

    setSubmittingAction('google');
    setErrorMessage(null);
    setInfoMessage(null);

    try {
      const redirectUrl = AuthSession.makeRedirectUri({
        path: 'sso-callback',
      });
      const outcome = await executeSsoSignIn(startSSOFlow, 'oauth_google', redirectUrl);
      if (outcome.status !== 'success' && outcome.message) {
        setErrorMessage(outcome.message);
      }
    } finally {
      setSubmittingAction(null);
    }
  }, [startSSOFlow, submittingAction]);

  const handlePrimarySubmit = mode === 'sign_in' ? handleSignInSubmit : handleSignUpSubmit;

  const primaryButtonLabel =
    mode === 'sign_in'
      ? step === 'otp'
        ? 'Verify & Sign In'
        : signInMethod === 'email_code'
          ? 'Send Verification Code'
          : 'Sign In'
      : step === 'otp'
        ? 'Verify Email & Continue'
        : 'Create Account';

  return (
    <ScreenContainer>
      <KeyboardAvoidingView
        style={styles.keyboardAvoid}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <ScrollView
          contentContainerStyle={styles.scrollContent}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          <View style={styles.hero}>
            <View style={styles.logoCircle}>
              <Ionicons name="basket-outline" size={32} color={colors.primary} />
            </View>
            <Text style={styles.title}>Grocery Management</Text>
            <Text style={styles.subtitle}>
              Track pantry stock, expiry dates, and household cooking with zero guesswork.
            </Text>
          </View>

          <View style={styles.card}>
            <View style={styles.modeSwitcher} accessibilityRole="tablist">
              <Pressable
                onPress={() => switchMode('sign_in')}
                disabled={isSubmitting}
                style={[styles.modeTab, mode === 'sign_in' && styles.modeTabActive]}
                accessibilityRole="tab"
                accessibilityLabel="Sign In"
                accessibilityState={{ selected: mode === 'sign_in', disabled: isSubmitting }}
              >
                <Text
                  style={[
                    styles.modeTabText,
                    mode === 'sign_in' && styles.modeTabTextActive,
                  ]}
                >
                  Sign In
                </Text>
              </Pressable>

              <Pressable
                onPress={() => switchMode('sign_up')}
                disabled={isSubmitting}
                style={[styles.modeTab, mode === 'sign_up' && styles.modeTabActive]}
                accessibilityRole="tab"
                accessibilityLabel="Create Account"
                accessibilityState={{ selected: mode === 'sign_up', disabled: isSubmitting }}
              >
                <Text
                  style={[
                    styles.modeTabText,
                    mode === 'sign_up' && styles.modeTabTextActive,
                  ]}
                >
                  Create Account
                </Text>
              </Pressable>
            </View>

            {errorMessage ? (
              <View style={styles.errorBanner} accessibilityRole="alert">
                <Ionicons name="alert-circle-outline" size={20} color={colors.error} />
                <Text style={styles.errorText}>{errorMessage}</Text>
              </View>
            ) : null}

            {infoMessage ? (
              <View style={styles.infoBanner} accessibilityRole="text">
                <Ionicons name="mail-outline" size={20} color={colors.primary} />
                <Text style={styles.infoText}>{infoMessage}</Text>
              </View>
            ) : null}

            {mode === 'sign_in' && step === 'form' ? (
              <View style={styles.methodRow}>
                <Pressable
                  onPress={() => switchSignInMethod('email_code')}
                  disabled={isSubmitting}
                  style={[
                    styles.methodChip,
                    signInMethod === 'email_code' && styles.methodChipActive,
                  ]}
                  accessibilityRole="button"
                  accessibilityLabel="Sign in with email verification code"
                  accessibilityState={{
                    selected: signInMethod === 'email_code',
                    disabled: isSubmitting,
                  }}
                >
                  <Text
                    style={[
                      styles.methodChipText,
                      signInMethod === 'email_code' && styles.methodChipTextActive,
                    ]}
                  >
                    Email Code (OTP)
                  </Text>
                </Pressable>

                <Pressable
                  onPress={() => switchSignInMethod('password')}
                  disabled={isSubmitting}
                  style={[
                    styles.methodChip,
                    signInMethod === 'password' && styles.methodChipActive,
                  ]}
                  accessibilityRole="button"
                  accessibilityLabel="Sign in with password"
                  accessibilityState={{
                    selected: signInMethod === 'password',
                    disabled: isSubmitting,
                  }}
                >
                  <Text
                    style={[
                      styles.methodChipText,
                      signInMethod === 'password' && styles.methodChipTextActive,
                    ]}
                  >
                    Password
                  </Text>
                </Pressable>
              </View>
            ) : null}

            {step === 'form' ? (
              <View style={styles.formFields}>
                <View style={styles.fieldGroup}>
                  <Text style={styles.fieldLabel}>Email</Text>
                  <TextInput
                    value={email}
                    onChangeText={setEmail}
                    placeholder="you@example.com"
                    placeholderTextColor={colors.textMuted}
                    keyboardType="email-address"
                    autoCapitalize="none"
                    autoCorrect={false}
                    textContentType="emailAddress"
                    editable={!isSubmitting}
                    style={styles.input}
                    accessibilityLabel="Email address"
                  />
                </View>

                {mode === 'sign_up' || (mode === 'sign_in' && signInMethod === 'password') ? (
                  <View style={styles.fieldGroup}>
                    <Text style={styles.fieldLabel}>
                      {mode === 'sign_up' ? 'Password' : 'Password'}
                    </Text>
                    <View style={styles.passwordWrapper}>
                      <TextInput
                        value={password}
                        onChangeText={setPassword}
                        placeholder={
                          mode === 'sign_up' ? 'At least 15 characters (if required)' : 'Enter password'
                        }
                        placeholderTextColor={colors.textMuted}
                        secureTextEntry={!showPassword}
                        autoCapitalize="none"
                        autoCorrect={false}
                        textContentType={mode === 'sign_up' ? 'newPassword' : 'password'}
                        editable={!isSubmitting}
                        style={[styles.input, styles.passwordInput]}
                        accessibilityLabel="Password"
                      />
                      <Pressable
                        onPress={() => setShowPassword((prev) => !prev)}
                        disabled={isSubmitting}
                        style={styles.passwordToggle}
                        accessibilityRole="button"
                        accessibilityLabel={showPassword ? 'Hide password' : 'Show password'}
                      >
                        <Ionicons
                          name={showPassword ? 'eye-off-outline' : 'eye-outline'}
                          size={20}
                          color={colors.textSecondary}
                        />
                      </Pressable>
                    </View>
                    {mode === 'sign_up' ? (
                      <Text style={styles.helperText}>
                        Clerk development instances require 15+ characters when password sign-up is enabled.
                      </Text>
                    ) : null}
                  </View>
                ) : null}
              </View>
            ) : (
              <View style={styles.formFields}>
                <View style={styles.fieldGroup}>
                  <Text style={styles.fieldLabel}>6-Digit Verification Code</Text>
                  <TextInput
                    value={code}
                    onChangeText={(value) => setCode(value.replace(/[^0-9]/g, '').slice(0, 6))}
                    placeholder="123456"
                    placeholderTextColor={colors.textMuted}
                    keyboardType="number-pad"
                    maxLength={6}
                    textContentType="oneTimeCode"
                    editable={!isSubmitting}
                    style={[styles.input, styles.otpInput]}
                    accessibilityLabel="6-digit verification code"
                  />
                </View>

                <View style={styles.otpActionsRow}>
                  <Pressable
                    onPress={() => {
                      void handleResendCode();
                    }}
                    disabled={isSubmitting}
                    style={styles.textLinkButton}
                    accessibilityRole="button"
                    accessibilityLabel="Resend verification code"
                    accessibilityState={{ disabled: isSubmitting }}
                  >
                    {submittingAction === 'resend' ? (
                      <ActivityIndicator size="small" color={colors.primary} />
                    ) : (
                      <Text style={styles.textLink}>Resend code</Text>
                    )}
                  </Pressable>

                  <Pressable
                    onPress={handleChangeEmail}
                    disabled={isSubmitting}
                    style={styles.textLinkButton}
                    accessibilityRole="button"
                    accessibilityLabel="Change email address"
                    accessibilityState={{ disabled: isSubmitting }}
                  >
                    <Text style={styles.textLink}>Change email</Text>
                  </Pressable>
                </View>
              </View>
            )}

            <Pressable
              onPress={() => {
                void handlePrimarySubmit();
              }}
              disabled={isSubmitting}
              style={({ pressed }) => [
                styles.primaryButton,
                pressed && !isSubmitting && styles.primaryButtonPressed,
                isSubmitting && styles.buttonDisabled,
              ]}
              accessibilityRole="button"
              accessibilityLabel={primaryButtonLabel}
              accessibilityState={{ disabled: isSubmitting }}
            >
              {submittingAction === 'email_auth' ? (
                <ActivityIndicator color={colors.textOnPrimary} />
              ) : (
                <Text style={styles.primaryButtonText}>{primaryButtonLabel}</Text>
              )}
            </Pressable>

            <View style={styles.dividerRow}>
              <View style={styles.dividerLine} />
              <Text style={styles.dividerText}>or</Text>
              <View style={styles.dividerLine} />
            </View>

            <Pressable
              onPress={() => {
                void handleGooglePress();
              }}
              disabled={isSubmitting}
              style={({ pressed }) => [
                styles.googleButton,
                pressed && !isSubmitting && styles.googleButtonPressed,
                isSubmitting && styles.buttonDisabled,
              ]}
              accessibilityRole="button"
              accessibilityLabel="Continue with Google"
              accessibilityState={{ disabled: isSubmitting }}
            >
              {submittingAction === 'google' ? (
                <ActivityIndicator color={colors.textPrimary} />
              ) : (
                <>
                  <Ionicons name="logo-google" size={20} color={colors.textPrimary} />
                  <Text style={styles.googleButtonText}>Continue with Google</Text>
                </>
              )}
            </Pressable>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  keyboardAvoid: {
    flex: 1,
  },
  scrollContent: {
    flexGrow: 1,
    justifyContent: 'center',
    paddingVertical: spacing.xl,
    gap: spacing.xl,
  },
  hero: {
    alignItems: 'center',
    gap: spacing.sm,
  },
  logoCircle: {
    width: 64,
    height: 64,
    borderRadius: radii.full,
    backgroundColor: colors.primaryLight,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.xs,
  },
  title: {
    fontSize: 24,
    fontWeight: '700',
    color: colors.textPrimary,
    textAlign: 'center',
  },
  subtitle: {
    fontSize: 14,
    lineHeight: 20,
    color: colors.textSecondary,
    textAlign: 'center',
    maxWidth: 320,
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.md,
  },
  modeSwitcher: {
    flexDirection: 'row',
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.md,
    padding: spacing.xs,
    gap: spacing.xs,
  },
  modeTab: {
    flex: 1,
    minHeight: layout.minTapTarget - 4,
    borderRadius: radii.sm,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.sm,
  },
  modeTabActive: {
    backgroundColor: colors.surface,
  },
  modeTabText: {
    fontSize: 15,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  modeTabTextActive: {
    color: colors.textPrimary,
  },
  errorBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    backgroundColor: colors.errorBg,
    borderColor: colors.errorBorder,
    borderWidth: 1,
    borderRadius: radii.md,
    padding: spacing.md,
  },
  errorText: {
    flex: 1,
    fontSize: 14,
    color: colors.error,
  },
  infoBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    backgroundColor: colors.primaryLight,
    borderRadius: radii.md,
    padding: spacing.md,
  },
  infoText: {
    flex: 1,
    fontSize: 14,
    color: colors.primaryPressed,
  },
  methodRow: {
    flexDirection: 'row',
    gap: spacing.sm,
  },
  methodChip: {
    flex: 1,
    minHeight: 40,
    borderRadius: radii.sm,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.sm,
  },
  methodChipActive: {
    borderColor: colors.primary,
    backgroundColor: colors.primaryLight,
  },
  methodChipText: {
    fontSize: 13,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  methodChipTextActive: {
    color: colors.primaryPressed,
  },
  formFields: {
    gap: spacing.md,
  },
  fieldGroup: {
    gap: spacing.xs,
  },
  fieldLabel: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.textPrimary,
  },
  input: {
    minHeight: layout.minTapTarget,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.background,
    paddingHorizontal: spacing.md,
    fontSize: 16,
    color: colors.textPrimary,
  },
  passwordWrapper: {
    position: 'relative',
    justifyContent: 'center',
  },
  passwordInput: {
    paddingRight: 48,
  },
  passwordToggle: {
    position: 'absolute',
    right: 0,
    width: layout.minTapTarget,
    height: layout.minTapTarget,
    alignItems: 'center',
    justifyContent: 'center',
  },
  helperText: {
    fontSize: 12,
    lineHeight: 16,
    color: colors.textMuted,
  },
  otpInput: {
    letterSpacing: 6,
    fontWeight: '600',
    textAlign: 'center',
  },
  otpActionsRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  textLinkButton: {
    minHeight: 44,
    justifyContent: 'center',
    paddingHorizontal: spacing.xs,
  },
  textLink: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.primary,
  },
  primaryButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
    backgroundColor: colors.primary,
    minHeight: layout.minTapTarget,
    borderRadius: radii.md,
    paddingHorizontal: spacing.lg,
  },
  primaryButtonPressed: {
    backgroundColor: colors.primaryPressed,
  },
  primaryButtonText: {
    color: colors.textOnPrimary,
    fontSize: 16,
    fontWeight: '600',
  },
  dividerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    marginVertical: spacing.xs,
  },
  dividerLine: {
    flex: 1,
    height: 1,
    backgroundColor: colors.border,
  },
  dividerText: {
    fontSize: 13,
    fontWeight: '500',
    color: colors.textMuted,
    textTransform: 'lowercase',
  },
  googleButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    minHeight: layout.minTapTarget,
    borderRadius: radii.md,
    paddingHorizontal: spacing.lg,
  },
  googleButtonPressed: {
    backgroundColor: colors.surfaceMuted,
  },
  googleButtonText: {
    color: colors.textPrimary,
    fontSize: 16,
    fontWeight: '600',
  },
  buttonDisabled: {
    opacity: 0.6,
  },
});
