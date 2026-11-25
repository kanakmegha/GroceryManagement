import React from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';
import { useRouter } from 'expo-router';
import { clearAuthIntent, DEMO_HOME_HREF } from './demoMode';

/** Shown on the signed-out screen so visitors can return to the public demo. */
export function DemoEntryLink() {
  const router = useRouter();
  return (
    <Pressable
      onPress={() => {
        clearAuthIntent();
        router.replace(DEMO_HOME_HREF as never);
      }}
      accessibilityRole="link"
      accessibilityLabel="Try the live demo without signing in"
      style={styles.link}
    >
      <Text style={styles.text}>Just looking? Try the live demo — no sign-in needed</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  link: { alignItems: 'center', justifyContent: 'center', minHeight: 48, padding: 12 },
  text: { fontSize: 14, fontWeight: '600', color: '#1B6B4A', textDecorationLine: 'underline' },
});
