/** Shared primitives: cards, stats, badges, band readouts. */
import React from 'react';
import { View, Text, StyleSheet, ViewStyle, Pressable } from 'react-native';
import { Colors, Spacing, Radius, Type, Confidence } from '@/constants/theme';
import { formatFull } from '@/lib/api';

export function Card({ children, style }: { children: React.ReactNode; style?: ViewStyle }) {
  return <View style={[s.card, style]}>{children}</View>;
}

export function SectionTitle({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <View style={s.sectionTitle}>
      <Text style={s.sectionText}>{children}</Text>
      {hint ? <Text style={s.sectionHint}>{hint}</Text> : null}
    </View>
  );
}

export function ConfidenceBadge({ level }: { level: 'HIGH' | 'MEDIUM' | 'LOW' }) {
  const c = Confidence[level];
  return (
    <View style={[s.badge, { borderColor: c.color }]}>
      <View style={[s.dot, { backgroundColor: c.color }]} />
      <Text style={[s.badgeText, { color: c.color }]}>{c.label}</Text>
    </View>
  );
}

/**
 * The central readout. Deliberately shows P10 and P90 at the same visual weight
 * as the median: the product promise is that a bare number is never displayed
 * without its band.
 */
export function BandReadout({
  low, mid, high, label, unit = 'pax',
}: { low: number; mid: number; high: number; label: string; unit?: string }) {
  return (
    <View style={s.readout}>
      <Text style={s.readoutLabel}>{label}</Text>
      <View style={s.readoutRow}>
        <View style={s.readoutCol}>
          <Text style={s.readoutSmall}>{formatFull(low)}</Text>
          <Text style={s.readoutTick}>P10</Text>
        </View>
        <View style={[s.readoutCol, s.readoutMid]}>
          <Text style={s.readoutBig}>{formatFull(mid)}</Text>
          <Text style={s.readoutTick}>median {unit}</Text>
        </View>
        <View style={s.readoutCol}>
          <Text style={s.readoutSmall}>{formatFull(high)}</Text>
          <Text style={s.readoutTick}>P90</Text>
        </View>
      </View>
      <Text style={s.readoutFoot}>
        80% of simulated outcomes fall between {formatFull(low)} and {formatFull(high)}
      </Text>
    </View>
  );
}

export function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <View style={s.stat}>
      <Text style={[s.statValue, tone ? { color: tone } : null]}>{value}</Text>
      <Text style={s.statLabel}>{label}</Text>
    </View>
  );
}

export function Chip({
  label, active, onPress,
}: { label: string; active?: boolean; onPress?: () => void }) {
  return (
    <Pressable onPress={onPress} style={[s.chip, active && s.chipActive]}>
      <Text style={[s.chipText, active && s.chipTextActive]}>{label}</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  card: {
    backgroundColor: Colors.surface,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.lg,
    marginBottom: Spacing.md,
  },
  sectionTitle: { marginBottom: Spacing.sm },
  sectionText: { ...Type.heading, color: Colors.text },
  sectionHint: { ...Type.caption, color: Colors.textFaint, marginTop: 2 },
  badge: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    borderWidth: 1, borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm, paddingVertical: 4, alignSelf: 'flex-start',
  },
  dot: { width: 6, height: 6, borderRadius: 3 },
  badgeText: { ...Type.caption },
  readout: { alignItems: 'center' },
  readoutLabel: { ...Type.caption, color: Colors.textFaint, marginBottom: Spacing.sm },
  readoutRow: { flexDirection: 'row', alignItems: 'flex-end', gap: Spacing.lg },
  readoutCol: { alignItems: 'center' },
  readoutMid: { paddingHorizontal: Spacing.sm },
  readoutBig: { ...Type.display, color: Colors.text },
  readoutSmall: { fontSize: 17, fontWeight: '600', color: Colors.textMuted },
  readoutTick: { ...Type.caption, color: Colors.textFaint, marginTop: 2 },
  readoutFoot: {
    ...Type.caption, color: Colors.textFaint,
    marginTop: Spacing.md, textAlign: 'center', lineHeight: 15,
  },
  stat: { flex: 1, minWidth: 90 },
  statValue: { ...Type.mono, color: Colors.text, fontSize: 17 },
  statLabel: { ...Type.caption, color: Colors.textFaint, marginTop: 2 },
  chip: {
    paddingHorizontal: Spacing.md, paddingVertical: 7,
    borderRadius: Radius.sm, borderWidth: 1,
    borderColor: Colors.border, backgroundColor: Colors.surfaceAlt,
  },
  chipActive: { borderColor: Colors.median, backgroundColor: 'rgba(94,158,255,0.14)' },
  chipText: { ...Type.label, color: Colors.textMuted },
  chipTextActive: { color: Colors.median },
});
