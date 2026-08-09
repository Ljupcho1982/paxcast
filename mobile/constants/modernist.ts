/**
 * Modernist design system — ported from `modernist.css`.
 *
 * Two things about this system drive every decision below:
 *
 *  1. **Radius is zero.** `--radius-sm/md/lg` are all `0px`. Nothing is
 *     rounded, anywhere. Softening a corner breaks the system.
 *  2. **Dividers are structural, not decorative.** Section boundaries are
 *     2px; list-row boundaries are 1px. The grid is the layout.
 *
 * React Native has no `color-mix()` and no CSS custom properties, so the
 * mixes in the source are precomputed here as rgba. Every one is
 * `color-mix(in srgb, <color> N%, transparent)`, which is just alpha = N/100.
 */

export const C = {
  bg: '#f3f2f2',
  surface: '#eae9e9',
  text: '#201e1d',
  accent: '#ec3013',
  accent2: '#e15b47',
  divider: 'rgba(32,30,29,0.4)',

  neutral100: '#f8f4f4',
  neutral200: '#eae7e7',
  neutral300: '#d7d3d3',
  neutral400: '#bab6b6',
  neutral500: '#9b9797',
  neutral600: '#7d7979',
  neutral700: '#605d5d',
  neutral800: '#444141',
  neutral900: '#2d2b2b',

  accent100: '#fff2ef',
  accent200: '#ffe0d9',
  accent300: '#ffc4b8',
  accent400: '#ff9783',
  accent500: '#ff563c',
  accent600: '#dd2b0f',
  accent700: '#ae1800',
  accent800: '#7c1405',
  accent900: '#4d170e',

  // Secondary accent ramp. Unused by the two checkpoint screens, but ported so
  // this file is a complete mirror of modernist.css rather than a subset --
  // the next screen that needs a second accent should not have to go digging.
  accent2100: '#fff2ef',
  accent2200: '#ffe0da',
  accent2300: '#ffc4b9',
  accent2400: '#ff9784',
  accent2500: '#ef6853',
  accent2600: '#c94b39',
  accent2700: '#9e3526',
  accent2800: '#71261b',
  accent2900: '#471d16',
} as const;

/** Text at partial opacity — the source uses color-mix on --color-text. */
export const ink = (pct: number) => `rgba(32,30,29,${pct / 100})`;

export const S = { s1: 4, s2: 8, s3: 12, s4: 16, s6: 24, s8: 32 } as const;

/** Radius is 0 throughout this system. Kept named so the intent is explicit. */
export const RADIUS = 0;

export const F = {
  heading: 'Archivo_800ExtraBold',
  headingSemi: 'Archivo_600SemiBold',
  body: 'Archivo_400Regular',
} as const;

/**
 * Type scale lifted from the prototype's inline styles rather than from the
 * stylesheet's h1–h6, because the screens set sizes inline and those are what
 * must be matched.
 */
export const T = {
  /** Section kickers: 11px, .1em tracking, 600, 55% ink. */
  kicker: {
    fontFamily: F.headingSemi,
    fontSize: 11,
    letterSpacing: 1.1,
    color: ink(55),
  },
  /** App title / checkpoint name in the bar. */
  barTitle: {
    fontFamily: F.heading,
    fontSize: 19,
    letterSpacing: -0.38,
    color: C.text,
  },
  /** Row heading, e.g. "Checkpoint 3". */
  rowName: {
    fontFamily: F.heading,
    fontSize: 16,
    letterSpacing: -0.16,
    color: C.text,
  },
  /** Row median value. */
  rowValue: { fontFamily: F.heading, fontSize: 22, letterSpacing: -0.44 },
  /** The 72px hero figure on the detail screen. */
  hero: {
    fontFamily: F.heading,
    fontSize: 72,
    lineHeight: 65,
    letterSpacing: -2.88,
    color: C.text,
  },
  /** The 52px "1 in N" risk figure. */
  risk: {
    fontFamily: F.heading,
    fontSize: 52,
    lineHeight: 44,
    letterSpacing: -1.56,
    color: C.accent700,
  },
  body: { fontFamily: F.body, fontSize: 14, lineHeight: 21, color: C.text },
  small: { fontFamily: F.body, fontSize: 12, color: ink(60) },
  tiny: { fontFamily: F.body, fontSize: 11, color: ink(50) },
} as const;

/**
 * Wait-time colour ramp, from the prototype's `col()`.
 * Neutral while a wait is tolerable; accent once it starts costing you a flight.
 */
export function waitColor(minutes: number): string {
  if (minutes <= 15) return C.neutral300;
  if (minutes <= 30) return C.neutral600;
  if (minutes <= 45) return C.accent500;
  return C.accent800;
}

export const WAIT_LEGEND = [
  { color: C.neutral300, label: 'under 15 min', lo: 0, hi: 15 },
  { color: C.neutral600, label: '15–30', lo: 15, hi: 30 },
  { color: C.accent500, label: '30–45', lo: 30, hi: 45 },
  { color: C.accent800, label: 'over 45', lo: 45, hi: Infinity },
] as const;
