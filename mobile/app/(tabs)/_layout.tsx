import React from 'react';
import { Tabs } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { colors } from '../../src/shared/theme/tokens';

export default function TabsLayout() {
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
      <Tabs.Screen
        name="index"
        options={{
          title: 'Home',
          tabBarLabel: 'Home',
          tabBarAccessibilityLabel: 'Home tab',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="home-outline" size={size} color={typeof color === 'string' ? color : undefined} />
          ),
        }}
      />
      <Tabs.Screen
        name="inventory"
        options={{
          title: 'Inventory',
          tabBarLabel: 'Inventory',
          tabBarAccessibilityLabel: 'Inventory tab',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="cube-outline" size={size} color={typeof color === 'string' ? color : undefined} />
          ),
        }}
      />
      <Tabs.Screen
        name="purchases"
        options={{
          title: 'Purchases',
          tabBarLabel: 'Purchases',
          tabBarAccessibilityLabel: 'Purchases tab',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="receipt-outline" size={size} color={typeof color === 'string' ? color : undefined} />
          ),
        }}
      />
      <Tabs.Screen
        name="budget"
        options={{
          title: 'Budget',
          tabBarLabel: 'Budget',
          tabBarAccessibilityLabel: 'Budget tab',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="wallet-outline" size={size} color={typeof color === 'string' ? color : undefined} />
          ),
        }}
      />
      <Tabs.Screen
        name="recipes"
        options={{
          title: 'Recipes',
          tabBarLabel: 'Recipes',
          tabBarAccessibilityLabel: 'Recipes tab',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="restaurant-outline" size={size} color={typeof color === 'string' ? color : undefined} />
          ),
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: 'Settings',
          tabBarLabel: 'Settings',
          tabBarAccessibilityLabel: 'Settings tab',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="settings-outline" size={size} color={typeof color === 'string' ? color : undefined} />
          ),
        }}
      />
    </Tabs>
  );
}
