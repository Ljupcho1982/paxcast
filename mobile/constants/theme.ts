/**
 * PaxCast design tokens.
 *
 * The palette is built around one idea: uncertainty is the product, so the
 * confidence bands must be the most visually prominent element on screen, and
 * the median line must never read as "the answer". Bands use a single hue at
 * varying opacity so that width -- not colour -- carries the meaning.
 */

export const Colors = {
  bg: '#0B1020',
  surface: '#141B31',
  surfaceAlt: '#1B2440',
  border: '#26314F',

  text: '#E8ECF5',
  textMuted: '#93A0BC',
  textFaint: '#5E6B88',

  // Forecast band ramp: outermost (P5-P95) is faintest.
  band95: 'rgba(94, 158, 255, 0.10)',
  band90: 'rgba(94, 158, 255, 0.16)',
  band75: 'rgba(94, 158, 255, 0.26)',
  median: '#5E9EFF',

  // Scenario overlay, deliberately a different hue from the baseline.
  scenario: '#F2A33C',
  scenarioBand: 'rgba(242, 163, 60, 0.18)',

  capacity: '#FF5C7A',
  success: '#3ECF8E',
  warning: '#F2A33C',
  danger: '#FF5C7A',
} as const;

export const Confidence = {
  HIGH: { color: Colors.success, label: 'High confidence' },
  MEDIUM: { color: Colors.warning, label: 'Medium confidence' },
  LOW: { color: Colors.danger, label: 'Low confidence' },
} as const;

export const Spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;

export const Radius = { sm: 8, md: 12, lg: 16, xl: 20 } as const;

export const Type = {
  display: { fontSize: 34, fontWeight: '700' as const, letterSpacing: -0.5 },
  title: { fontSize: 22, fontWeight: '700' as const, letterSpacing: -0.3 },
  heading: { fontSize: 17, fontWeight: '600' as const },
  body: { fontSize: 15, fontWeight: '400' as const },
  label: { fontSize: 13, fontWeight: '500' as const },
  caption: { fontSize: 11, fontWeight: '500' as const, letterSpacing: 0.3 },
  mono: { fontSize: 15, fontWeight: '600' as const, fontVariant: ['tabular-nums' as const] },
};
