export const colors = {
  primary: '#1B6B4A',
  primaryPressed: '#145238',
  primaryLight: '#E8F3EE',
  background: '#F8FAF7',
  surface: '#FFFFFF',
  surfaceMuted: '#F3F4F6',
  textPrimary: '#111827',
  textSecondary: '#4B5563',
  textMuted: '#6B7280',
  textOnPrimary: '#FFFFFF',
  border: '#E5E7EB',
  error: '#B91C1C',
  errorBg: '#FEF2F2',
  errorBorder: '#FECACA',
  warning: '#92400E',
  warningBg: '#FFFBEB',
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

export const radii = {
  sm: 8,
  md: 12,
  lg: 16,
  full: 9999,
} as const;

export const layout = {
  minTapTarget: 48,
} as const;
