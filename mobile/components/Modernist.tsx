/**
 * Modernist primitives — React Native equivalents of the `.btn`, `.input` and
 * `.table` classes in `modernist.css`, plus the segmented controls the
 * prototype builds inline.
 *
 * Segmented controls in this system are built from buttons with
 * `border-right: 0`, so adjacent cells share a single hairline and the group
 * reads as one ruled object rather than a row of separate chips. That means
 * the last cell needs its right border restored, which is handled here rather
 * than left to every call site.
 */

import React from 'react';
import { View, Text, Pressable, StyleSheet, ViewStyle, TextStyle } from 'react-native';
import { C, F, T, ink, RADIUS } from '@/constants/modernist';

/* ── Buttons ─────────────────────────────────────────────────────────── */

interface BtnProps {
  label: string;
  onPress?: () => void;
  variant?: 'primary' | 'secondary';
  block?: boolean;
  style?: ViewStyle;
}

export function Btn({ label, onPress, variant = 'primary', block, style }: BtnProps) {
  const isPrimary = variant === 'primary';
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        s.btn,
        // .btn-block sets justify-content: flex-start and text-align: left --
        // buttons in this system are left-aligned, not centred.
        block && s.btnBlock,
        isPrimary ? s.btnPrimary : s.btnSecondary,
        pressed && (isPrimary ? s.btnPrimaryActive : s.btnSecondaryActive),
        style,
      ]}
    >
      <Text style={[s.btnLabel, isPrimary ? s.btnLabelPrimary : s.btnLabelSecondary]}>
        {label}
      </Text>
    </Pressable>
  );
}

/* ── Segmented control ───────────────────────────────────────────────── */

export interface SegmentItem {
  key: string;
  label: string;
}

interface SegmentedProps {
  items: SegmentItem[];
  activeKey: string;
  onSelect: (key: string) => void;
  /** Prototype uses 13px for hour/buffer strips, 12px for the mode tabs. */
  fontSize?: number;
  letterSpacing?: number;
  /** Hour strips size to content; buffer and mode strips divide the width. */
  fill?: boolean;
  minWidth?: number;
  paddingVertical?: number;
  paddingLeft?: number;
  /** Mode tabs sit inside a bordered band and only need inner separators. */
  borderless?: boolean;
}

export function Segmented({
  items,
  activeKey,
  onSelect,
  fontSize = 13,
  letterSpacing = 0,
  fill = false,
  minWidth,
  paddingVertical = 9,
  paddingLeft = 10,
  borderless = false,
}: SegmentedProps) {
  return (
    <View style={{ flexDirection: 'row' }}>
      {items.map((item, i) => {
        const active = item.key === activeKey;
        const last = i === items.length - 1;
        return (
          <Pressable
            key={item.key}
            onPress={() => onSelect(item.key)}
            style={[
              {
                flex: fill ? 1 : undefined,
                minWidth,
                paddingVertical,
                paddingLeft,
                paddingRight: 0,
                backgroundColor: active ? C.accent : 'transparent',
                borderColor: C.divider,
                borderTopWidth: borderless ? 0 : 1,
                borderBottomWidth: borderless ? 0 : 1,
                borderLeftWidth: borderless ? 0 : 1,
                // border-right: 0 on every cell but the last, so neighbours
                // share one hairline instead of stacking two.
                borderRightWidth: borderless ? (last ? 0 : 1) : last ? 1 : 0,
                borderRadius: RADIUS,
              },
            ]}
          >
            <Text
              style={{
                fontFamily: F.heading,
                fontSize,
                letterSpacing,
                textAlign: 'left',
                color: active ? '#fff' : C.text,
              }}
            >
              {item.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/* ── Field labels and readonly input ─────────────────────────────────── */

export function InputDisplay({ value }: { value: string }) {
  return (
    <View style={s.input}>
      <Text style={s.inputText}>{value}</Text>
    </View>
  );
}

/* ── Kicker ──────────────────────────────────────────────────────────── */

export function Kicker({ children, style }: { children: React.ReactNode; style?: TextStyle }) {
  return <Text style={[T.kicker, style]}>{children}</Text>;
}

/* ── Table ───────────────────────────────────────────────────────────── */

export function Table({
  head,
  rows,
}: {
  head: [string, string];
  rows: { label: string; value: string }[];
}) {
  return (
    <View>
      <View style={s.tHeadRow}>
        <Text style={[s.tHead, { flex: 1 }]}>{head[0]}</Text>
        <Text style={s.tHead}>{head[1]}</Text>
      </View>
      {rows.map((r) => (
        <View key={r.label} style={s.tRow}>
          <Text style={[s.tCell, { flex: 1 }]}>{r.label}</Text>
          <Text style={[s.tCell, s.tCellValue]}>{r.value}</Text>
        </View>
      ))}
    </View>
  );
}

/* ── Rules ───────────────────────────────────────────────────────────── */

/** Section boundary: 2px. Distinct from the 1px used between list rows. */
export function SectionRule() {
  return <View style={{ height: 2, backgroundColor: C.divider }} />;
}

const s = StyleSheet.create({
  btn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderWidth: 1,
    borderColor: 'transparent',
    borderRadius: RADIUS,
  },
  btnBlock: { width: '100%', justifyContent: 'flex-start', marginTop: 8 },
  btnPrimary: { backgroundColor: C.accent },
  btnPrimaryActive: { backgroundColor: C.accent700 },
  btnSecondary: { borderColor: C.divider },
  btnSecondaryActive: { backgroundColor: ink(14) },
  btnLabel: { fontFamily: F.heading, fontSize: 14, lineHeight: 17 },
  btnLabelPrimary: { color: C.bg },
  btnLabelSecondary: { color: C.text },

  input: {
    minHeight: 36,
    paddingHorizontal: 10,
    paddingVertical: 6,
    justifyContent: 'center',
    backgroundColor: C.surface,
    borderWidth: 1,
    borderColor: C.divider,
    borderRadius: RADIUS,
  },
  inputText: { fontFamily: F.body, fontSize: 14, color: C.text },

  tHeadRow: {
    flexDirection: 'row',
    paddingHorizontal: 8,
    paddingVertical: 8,
    borderBottomWidth: 2,
    borderBottomColor: C.divider,
  },
  tHead: {
    fontFamily: F.headingSemi,
    fontSize: 11,
    letterSpacing: 0.88,
    textTransform: 'uppercase',
    color: ink(60),
  },
  tRow: {
    flexDirection: 'row',
    paddingHorizontal: 8,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: ink(15),
  },
  tCell: { fontFamily: F.body, fontSize: 14, color: C.text },
  tCellValue: { fontFamily: F.heading },
});
