import React from 'react';
import { Pressable, StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import { Slot, Tabs, usePathname, useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { colors, radii, spacing } from '../../src/shared/theme/tokens';

const NAV_ITEMS = [
  { name: 'index', href: '/', title: 'Home', icon: 'home-outline' as const },
  { name: 'inventory', href: '/inventory', title: 'Inventory', icon: 'cube-outline' as const },
  { name: 'purchases', href: '/purchases', title: 'Purchases', icon: 'receipt-outline' as const },
  { name: 'budget', href: '/budget', title: 'Budget', icon: 'wallet-outline' as const },
  { name: 'recipes', href: '/recipes', title: 'Recipes', icon: 'restaurant-outline' as const },
  { name: 'settings', href: '/settings', title: 'Settings', icon: 'settings-outline' as const },
] as const;

export default function WebTabsLayout() {
  const { width } = useWindowDimensions();
  const pathname = usePathname();
  const router = useRouter();
  const isDesktop = width >= 900;

  if (isDesktop) {
    return (
      <View style={styles.desktopShell}>
        <View style={styles.sidebar}>
          <View style={styles.brandBlock}>
            <View style={styles.brandBadge}>
              <Ionicons name="leaf-outline" size={20} color={colors.textOnPrimary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.brandTitle}>Grocery</Text>
              <Text style={styles.brandSubtitle}>Client Preview</Text>
            </View>
          </View>

          <View style={styles.navList}>
            {NAV_ITEMS.map((item) => {
              const active =
                item.href === '/'
                  ? pathname === '/' || pathname === '/index'
                  : pathname.startsWith(item.href);
              return (
                <Pressable
                  key={item.name}
                  onPress={() => router.push(item.href)}
                  accessibilityRole="button"
                  accessibilityLabel={`${item.title} tab`}
                  style={({ pressed }) => [
                    styles.navItem,
                    active && styles.navItemActive,
                    pressed && !active && styles.navItemPressed,
                  ]}
                >
                  <Ionicons
                    name={item.icon}
                    size={20}
                    color={active ? colors.primary : colors.textMuted}
                  />
                  <Text style={[styles.navLabel, active && styles.navLabelActive]}>
                    {item.title}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        </View>

        <View style={styles.mainContent}>
          <Slot />
        </View>
      </View>
    );
  }

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarShowLabel: true,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
          borderTopWidth: 1,
          minHeight: 62,
          paddingTop: 6,
          paddingBottom: 8,
        },
        tabBarItemStyle: {
          minHeight: 48,
        },
        tabBarLabelStyle: {
          fontSize: 11,
          fontWeight: '600',
          marginTop: 2,
        },
      }}
    >
      {NAV_ITEMS.map((item) => (
        <Tabs.Screen
          key={item.name}
          name={item.name}
          options={{
            title: item.title,
            tabBarLabel: item.title,
            tabBarAccessibilityLabel: `${item.title} tab`,
            tabBarIcon: ({ color, size }) => (
              <Ionicons
                name={item.icon}
                size={size}
                color={typeof color === 'string' ? color : undefined}
              />
            ),
          }}
        />
      ))}
    </Tabs>
  );
}

const styles = StyleSheet.create({
  desktopShell: {
    flex: 1,
    flexDirection: 'row',
    backgroundColor: colors.background,
  },
  sidebar: {
    width: 236,
    backgroundColor: colors.surface,
    borderRightWidth: 1,
    borderRightColor: colors.border,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xl,
  },
  brandBlock: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.sm,
    paddingBottom: spacing.xl,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    marginBottom: spacing.lg,
  },
  brandBadge: {
    width: 36,
    height: 36,
    borderRadius: radii.md,
    backgroundColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  brandTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: colors.textPrimary,
  },
  brandSubtitle: {
    fontSize: 12,
    color: colors.textMuted,
  },
  navList: {
    gap: spacing.xs,
  },
  navItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    borderRadius: radii.md,
    minHeight: 44,
  },
  navItemActive: {
    backgroundColor: colors.primaryLight,
  },
  navItemPressed: {
    backgroundColor: colors.surfaceMuted,
  },
  navLabel: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  navLabelActive: {
    color: colors.primary,
  },
  mainContent: {
    flex: 1,
    backgroundColor: colors.background,
  },
});
