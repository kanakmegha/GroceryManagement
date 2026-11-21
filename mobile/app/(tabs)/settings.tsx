import React, { useCallback, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { useAuth, useUser } from '@clerk/clerk-expo';
import { Ionicons } from '@expo/vector-icons';
import { useHousehold } from '../../src/features/auth/HouseholdContext';
import { ScreenContainer } from '../../src/shared/components';
import { colors, layout, radii, spacing } from '../../src/shared/theme/tokens';

export default function SettingsScreen() {
  const { signOut } = useAuth();
  const { user: clerkUser } = useUser();
  const { user: apiUser, household, households, setActiveHouseholdId } = useHousehold();
  const [isSigningOut, setIsSigningOut] = useState(false);

  const handleSignOut = useCallback(async () => {
    if (isSigningOut) {
      return;
    }
    setIsSigningOut(true);
    try {
      await signOut();
    } finally {
      setIsSigningOut(false);
    }
  }, [isSigningOut, signOut]);

  const displayName =
    clerkUser?.fullName ??
    apiUser?.display_name ??
    clerkUser?.primaryEmailAddress?.emailAddress ??
    apiUser?.email ??
    'Signed-in User';
  const email =
    clerkUser?.primaryEmailAddress?.emailAddress ?? apiUser?.email ?? 'No email on file';

  return (
    <ScreenContainer scrollable>
      <View style={styles.header}>
        <Text style={styles.title}>Settings</Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Account</Text>
        <View style={styles.card}>
          <Text style={styles.label}>Name</Text>
          <Text style={styles.value}>{displayName}</Text>

          <View style={styles.divider} />

          <Text style={styles.label}>Email</Text>
          <Text style={styles.value}>{email}</Text>

          <View style={styles.divider} />

          <Text style={styles.label}>Clerk User ID</Text>
          <Text style={styles.monoValue} numberOfLines={1} ellipsizeMode="middle">
            {clerkUser?.id ?? apiUser?.clerk_user_id ?? '—'}
          </Text>
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Active Household</Text>
        <View style={styles.card}>
          <Text style={styles.label}>Household Name</Text>
          <Text style={styles.value}>{household?.name ?? '—'}</Text>

          <View style={styles.divider} />

          <Text style={styles.label}>Currency</Text>
          <Text style={styles.value}>{household?.currency_code ?? '—'}</Text>

          <View style={styles.divider} />

          <Text style={styles.label}>Role</Text>
          <Text style={[styles.value, styles.capitalize]}>
            {household?.role ?? '—'}
          </Text>
        </View>
      </View>

      {households.length > 1 ? (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Switch Household</Text>
          <View style={styles.card}>
            {households.map((item, idx) => {
              const isSelected = item.id === household?.id;
              return (
                <React.Fragment key={item.id}>
                  {idx > 0 ? <View style={styles.divider} /> : null}
                  <Pressable
                    onPress={() => setActiveHouseholdId(item.id)}
                    style={styles.householdOption}
                    accessibilityRole="button"
                    accessibilityLabel={`Switch to ${item.name}`}
                    accessibilityState={{ selected: isSelected }}
                  >
                    <View style={styles.householdOptionText}>
                      <Text style={styles.value}>{item.name}</Text>
                      <Text style={styles.label}>
                        {item.currency_code} · {item.role}
                      </Text>
                    </View>
                    {isSelected ? (
                      <Ionicons name="checkmark-circle" size={22} color={colors.primary} />
                    ) : null}
                  </Pressable>
                </React.Fragment>
              );
            })}
          </View>
        </View>
      ) : null}

      <Pressable
        onPress={() => {
          void handleSignOut();
        }}
        disabled={isSigningOut}
        style={({ pressed }) => [
          styles.signOutButton,
          pressed && !isSigningOut && styles.signOutButtonPressed,
          isSigningOut && styles.buttonDisabled,
        ]}
        accessibilityRole="button"
        accessibilityLabel="Sign Out"
        accessibilityState={{ disabled: isSigningOut }}
      >
        {isSigningOut ? (
          <ActivityIndicator color={colors.error} />
        ) : (
          <>
            <Ionicons name="log-out-outline" size={20} color={colors.error} />
            <Text style={styles.signOutText}>Sign Out</Text>
          </>
        )}
      </Pressable>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  header: {
    marginBottom: spacing.lg,
  },
  title: {
    fontSize: 24,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  section: {
    marginBottom: spacing.xl,
    gap: spacing.sm,
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.textSecondary,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.xs,
  },
  label: {
    fontSize: 12,
    color: colors.textMuted,
  },
  value: {
    fontSize: 15,
    fontWeight: '600',
    color: colors.textPrimary,
    marginBottom: spacing.xs,
  },
  monoValue: {
    fontSize: 13,
    fontWeight: '500',
    color: colors.textSecondary,
  },
  capitalize: {
    textTransform: 'capitalize',
  },
  divider: {
    height: 1,
    backgroundColor: colors.border,
    marginVertical: spacing.sm,
  },
  householdOption: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    minHeight: layout.minTapTarget,
    paddingVertical: spacing.xs,
  },
  householdOptionText: {
    flex: 1,
    gap: 2,
  },
  signOutButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
    backgroundColor: colors.errorBg,
    borderColor: colors.errorBorder,
    borderWidth: 1,
    borderRadius: radii.md,
    minHeight: layout.minTapTarget,
    paddingHorizontal: spacing.lg,
    marginTop: spacing.sm,
  },
  signOutButtonPressed: {
    opacity: 0.85,
  },
  signOutText: {
    fontSize: 16,
    fontWeight: '600',
    color: colors.error,
  },
  buttonDisabled: {
    opacity: 0.6,
  },
});
