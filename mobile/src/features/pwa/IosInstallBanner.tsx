import React, { useEffect, useState } from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors, radii, spacing } from '../../shared/theme/tokens';
import { IOS_INSTALL_DISMISSED_KEY, shouldShowIosInstallBanner } from './pwaRegistration';

export function IosInstallBanner() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (Platform.OS !== 'web' || typeof window === 'undefined' || typeof navigator === 'undefined') {
      return;
    }
    let dismissed = false;
    try {
      dismissed = window.localStorage.getItem(IOS_INSTALL_DISMISSED_KEY) === '1';
    } catch {
      dismissed = false;
    }
    const nav = navigator as Navigator & { standalone?: boolean };
    const isStandalone =
      Boolean(nav.standalone) ||
      Boolean(window.matchMedia?.('(display-mode: standalone)').matches);

    setVisible(
      shouldShowIosInstallBanner({
        isWeb: true,
        userAgent: navigator.userAgent,
        isStandalone,
        dismissed,
      })
    );
  }, []);

  if (!visible) return null;

  const handleDismiss = () => {
    setVisible(false);
    try {
      window.localStorage.setItem(IOS_INSTALL_DISMISSED_KEY, '1');
    } catch {
      // ignore storage errors
    }
  };

  return (
    <View style={styles.banner} accessibilityRole="region" accessibilityLabel="Install Grocery Management">
      <View style={styles.textWrap}>
        <Ionicons name="share-outline" size={18} color={colors.primary} />
        <Text style={styles.text}>
          Install Grocery Management: tap <Text style={styles.bold}>Share</Text> then{' '}
          <Text style={styles.bold}>Add to Home Screen</Text>.
        </Text>
      </View>
      <Pressable
        onPress={handleDismiss}
        accessibilityRole="button"
        accessibilityLabel="Dismiss install guide"
        style={styles.closeBtn}
      >
        <Ionicons name="close" size={18} color={colors.textMuted} />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
    backgroundColor: colors.primaryLight,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  textWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    flex: 1,
  },
  text: {
    fontSize: 13,
    lineHeight: 18,
    color: colors.textPrimary,
    flexShrink: 1,
  },
  bold: {
    fontWeight: '700',
  },
  closeBtn: {
    padding: spacing.xs,
    borderRadius: radii.sm,
  },
});
