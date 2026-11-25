import React from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Slot, usePathname, useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { SafeAreaView } from 'react-native-safe-area-context';
import { colors, layout, radii, spacing } from '../../shared/theme/tokens';
import { DemoProvider } from './DemoProvider';
import { setAuthIntent } from './demoMode';

const NAV = [
  { href: '/demo', title: 'Dashboard', icon: 'home-outline' as const },
  { href: '/demo/inventory', title: 'Inventory', icon: 'cube-outline' as const },
  { href: '/demo/purchases', title: 'Purchases', icon: 'receipt-outline' as const },
  { href: '/demo/receipts', title: 'Receipts', icon: 'document-text-outline' as const },
  { href: '/demo/recipes', title: 'Recipes', icon: 'restaurant-outline' as const },
  { href: '/demo/daily-plan', title: 'Meal Plan', icon: 'calendar-outline' as const },
  { href: '/demo/analytics', title: 'Analytics', icon: 'stats-chart-outline' as const },
] as const;

function DemoChrome() {
  const pathname = usePathname();
  const router = useRouter();

  const signIn = () => {
    setAuthIntent();
    router.replace('/' as never);
  };

  return (
    <SafeAreaView style={styles.root} edges={['top', 'left', 'right']}>
      <View style={styles.banner} accessibilityRole="header">
        <View style={styles.bannerLeft}>
          <Ionicons name="eye-outline" size={16} color={colors.warning} />
          <Text style={styles.bannerText}>
            Demo Mode · sample data · read-only. Nothing you do here is saved.
          </Text>
        </View>
        <Pressable
          onPress={signIn}
          accessibilityRole="button"
          accessibilityLabel="Sign in to your own account"
          style={({ pressed }) => [styles.signIn, pressed && { opacity: 0.8 }]}
        >
          <Text style={styles.signInText}>Sign In</Text>
        </Pressable>
      </View>

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.navScroll}
        contentContainerStyle={styles.navContent}
      >
        {NAV.map((item) => {
          const active = item.href === '/demo' ? pathname === '/demo' : pathname.startsWith(item.href);
          return (
            <Pressable
              key={item.href}
              onPress={() => router.push(item.href as never)}
              accessibilityRole="button"
              accessibilityLabel={`${item.title} demo section`}
              style={[styles.navItem, active && styles.navItemActive]}
            >
              <Ionicons
                name={item.icon}
                size={18}
                color={active ? colors.primary : colors.textMuted}
              />
              <Text style={[styles.navLabel, active && styles.navLabelActive]}>{item.title}</Text>
            </Pressable>
          );
        })}
      </ScrollView>

      <View style={styles.content}>
        <Slot />
      </View>
    </SafeAreaView>
  );
}

export function DemoShell() {
  return (
    <DemoProvider>
      <DemoChrome />
    </DemoProvider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
    backgroundColor: colors.warningBg,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  bannerLeft: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flex: 1 },
  bannerText: { fontSize: 13, fontWeight: '600', color: colors.warning, flexShrink: 1 },
  signIn: {
    backgroundColor: colors.primary,
    borderRadius: radii.md,
    minHeight: 36,
    paddingHorizontal: spacing.lg,
    alignItems: 'center',
    justifyContent: 'center',
  },
  signInText: { color: colors.textOnPrimary, fontSize: 14, fontWeight: '700' },
  navScroll: {
    flexGrow: 0,
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  navContent: { paddingHorizontal: spacing.md, paddingVertical: spacing.xs, gap: spacing.xs },
  navItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    minHeight: layout.minTapTarget - 8,
    paddingHorizontal: spacing.md,
    borderRadius: radii.md,
  },
  navItemActive: { backgroundColor: colors.primaryLight },
  navLabel: { fontSize: 14, fontWeight: '600', color: colors.textSecondary },
  navLabelActive: { color: colors.primary },
  content: { flex: 1 },
});
