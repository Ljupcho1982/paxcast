/**
 * Screen 1a — "Picker: every checkpoint carries its own spread".
 *
 * The design decision worth preserving: each row shows a median *and* a
 * 14-segment spread strip built from that checkpoint's own quantiles. Two
 * lanes with the same median can have very different strips, and the strip is
 * what tells you which one to walk to. A list of bare medians would rank them
 * identically and would be actively misleading.
 */

import React, { useMemo, useState } from 'react';
import { View, Text, ScrollView, StyleSheet, Pressable } from 'react-native';
import { useRouter } from 'expo-router';
import { C, F, T, S, ink, waitColor } from '@/constants/modernist';
import { Btn, InputDisplay, Kicker, Segmented, SectionRule } from '@/components/Modernist';
import { CHECKPOINTS, HOURS, formatHour, quantile } from '@/lib/waitModel';

const STRIP_SEGMENTS = 14;

export default function CheckpointPicker() {
  const router = useRouter();
  const [hourIdx, setHourIdx] = useState(2); // 07:00, the morning bank
  const [cpIdx, setCpIdx] = useState(2);

  const hour = HOURS[hourIdx] ?? 7;

  const rows = useMemo(
    () =>
      CHECKPOINTS.map((cp, i) => ({
        cp,
        selected: i === cpIdx,
        med: Math.round(quantile(cp, hour, 0.5)),
        lo: Math.round(quantile(cp, hour, 0.05)),
        hi: Math.round(quantile(cp, hour, 0.95)),
        strip: Array.from({ length: STRIP_SEGMENTS }, (_, k) =>
          waitColor(quantile(cp, hour, (k + 0.5) / STRIP_SEGMENTS)),
        ),
      })),
    [hour, cpIdx],
  );

  const hourItems = useMemo(
    () => HOURS.map((h, i) => ({ key: String(i), label: formatHour(h) })),
    [],
  );

  return (
    <View style={s.root}>
      {/* ── App bar ─────────────────────────────────────────────────── */}
      <View style={s.appBar}>
        <View style={s.mark} />
        <Text style={T.barTitle}>PaxCast</Text>
        <View style={{ flex: 1 }} />
        <Text style={s.barMeta}>FRI 8 AUG</Text>
      </View>

      <ScrollView contentContainerStyle={{ flexGrow: 1 }}>
        <View style={{ paddingHorizontal: S.s4, paddingTop: S.s4 }}>
          <InputDisplay value="SEA — Seattle–Tacoma Intl" />
        </View>

        <View style={s.kickerRow}>
          <Kicker>ENTERING SECURITY AT</Kicker>
        </View>

        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{ paddingHorizontal: S.s4, paddingBottom: S.s4 }}
        >
          <Segmented
            items={hourItems}
            activeKey={String(hourIdx)}
            onSelect={(k) => setHourIdx(Number(k))}
            minWidth={52}
          />
        </ScrollView>
        <SectionRule />

        <View style={s.kickerRowTight}>
          <Kicker>CHECKPOINTS · TYPICAL WAIT AND SPREAD</Kicker>
        </View>

        {rows.map((r) => (
          <Pressable
            key={r.cp.id}
            onPress={() => setCpIdx(r.cp.id)}
            style={[s.row, r.selected && { backgroundColor: C.neutral200 }]}
          >
            <View style={s.rowTop}>
              <Text style={T.rowName}>{r.cp.name}</Text>
              <View style={{ flex: 1 }} />
              <Text
                style={[
                  T.rowValue,
                  { color: r.selected ? C.accent700 : C.text },
                ]}
              >
                {r.med}
              </Text>
              <Text style={s.unit}>MIN</Text>
            </View>

            <Text style={s.zone}>{r.cp.zone}</Text>

            {/* Spread strip: 14 quantile segments, gap 3, height 10 */}
            <View style={s.strip}>
              {r.strip.map((color, k) => (
                <View
                  key={k}
                  style={[
                    s.stripCell,
                    { backgroundColor: color, marginLeft: k === 0 ? 0 : 3 },
                  ]}
                />
              ))}
            </View>

            <View style={s.rowFoot}>
              <Text style={T.tiny}>{r.lo} min on the best days</Text>
              <Text style={T.tiny}>{r.hi} min on the worst</Text>
            </View>
          </Pressable>
        ))}

        <View style={{ flex: 1 }} />
      </ScrollView>

      {/* ── Sticky footer ───────────────────────────────────────────── */}
      <View style={s.footer}>
        <Btn
          label="Open full distribution"
          block
          onPress={() =>
            router.push({
              pathname: '/checkpoint/[cp]',
              params: { cp: String(cpIdx), hour: String(hourIdx) },
            })
          }
        />
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },

  appBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: S.s4,
    paddingTop: 14,
    paddingBottom: S.s3,
    borderBottomWidth: 2,
    borderBottomColor: C.divider,
  },
  mark: { width: 18, height: 18, backgroundColor: C.accent },
  barMeta: {
    fontFamily: F.headingSemi,
    fontSize: 11,
    letterSpacing: 1.1,
    color: ink(55),
  },

  kickerRow: {
    paddingHorizontal: S.s4,
    paddingTop: S.s4,
    paddingBottom: S.s3,
  },
  kickerRowTight: {
    paddingHorizontal: S.s4,
    paddingTop: S.s4,
    paddingBottom: S.s2,
  },

  row: {
    borderTopWidth: 1,
    borderTopColor: C.divider,
    paddingHorizontal: S.s4,
    paddingVertical: 14,
  },
  rowTop: { flexDirection: 'row', alignItems: 'baseline', gap: 10 },
  unit: { fontFamily: F.headingSemi, fontSize: 11, color: ink(55) },
  zone: { fontFamily: F.body, fontSize: 12, color: ink(60), marginTop: 6 },

  strip: { flexDirection: 'row', marginTop: 10 },
  stripCell: { flex: 1, height: 10 },

  rowFoot: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: 5,
  },

  footer: {
    borderTopWidth: 2,
    borderTopColor: C.divider,
    paddingHorizontal: S.s4,
    paddingTop: S.s3,
    paddingBottom: S.s4,
    backgroundColor: C.bg,
  },
});
