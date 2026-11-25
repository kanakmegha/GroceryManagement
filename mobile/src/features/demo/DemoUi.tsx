import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { UseQueryResult } from '@tanstack/react-query';
import { EmptyState, ErrorState, LoadingState } from '../../shared/components';
import { colors, layout, radii, spacing } from '../../shared/theme/tokens';

export function Card({ children }: { children: React.ReactNode }) {
  return <View style={styles.card}>{children}</View>;
}

export function SectionTitle({ children }: { children: React.ReactNode }) {
  return <Text style={styles.sectionTitle}>{children}</Text>;
}

export function Pill({ label }: { label: string }) {
  return (
    <View style={styles.pill}>
      <Text style={styles.pillText}>{label}</Text>
    </View>
  );
}

/** A visibly disabled action that explains WHY it is unavailable. Never invokes anything. */
export function DisabledAction({ label, reason }: { label: string; reason: string }) {
  return (
    <View style={styles.disabledWrap}>
      <Pressable
        disabled
        accessibilityRole="button"
        accessibilityState={{ disabled: true }}
        accessibilityLabel={`${label} (unavailable in Demo Mode)`}
        style={styles.disabledButton}
      >
        <Ionicons name="lock-closed-outline" size={16} color={colors.textMuted} />
        <Text style={styles.disabledText}>{label}</Text>
      </Pressable>
      <Text style={styles.disabledReason}>{reason}</Text>
    </View>
  );
}

/** Standard loading / error+retry / empty handling for every demo screen. */
export function DemoQueryView<T>({
  query,
  isEmpty,
  emptyTitle,
  emptyBody,
  children,
}: {
  query: UseQueryResult<T>;
  isEmpty?: (data: T) => boolean;
  emptyTitle: string;
  emptyBody: string;
  children: (data: T) => React.ReactNode;
}) {
  if (query.isPending) {
    return <LoadingState message="Loading demo data..." />;
  }
  if (query.isError) {
    return (
      <ErrorState
        title="Could not load demo data"
        message={query.error instanceof Error ? query.error.message : 'Unexpected error.'}
        onRetry={() => {
          void query.refetch();
        }}
        retryDisabled={query.isFetching}
      />
    );
  }
  if (isEmpty?.(query.data)) {
    return <EmptyState title={emptyTitle} body={emptyBody} />;
  }
  return <>{children(query.data)}</>;
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.xs,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: colors.textPrimary,
    marginTop: spacing.md,
    marginBottom: spacing.sm,
  },
  pill: {
    backgroundColor: colors.primaryLight,
    borderRadius: radii.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    alignSelf: 'flex-start',
  },
  pillText: { fontSize: 12, fontWeight: '600', color: colors.primary },
  disabledWrap: { gap: spacing.xs, marginVertical: spacing.sm },
  disabledButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    minHeight: layout.minTapTarget,
    paddingHorizontal: spacing.lg,
    borderRadius: radii.md,
    backgroundColor: colors.surfaceMuted,
    borderColor: colors.border,
    borderWidth: 1,
    alignSelf: 'flex-start',
    opacity: 0.8,
  },
  disabledText: { fontSize: 14, fontWeight: '600', color: colors.textMuted },
  disabledReason: { fontSize: 12, color: colors.textMuted, lineHeight: 17 },
});
