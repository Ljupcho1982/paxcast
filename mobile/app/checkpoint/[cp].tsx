/**
 * Screen 1b — "Detail: three readings of the same forecast".
 *
 * The premise is that one distribution needs three renderings because people
 * read probability in different ways:
 *
 *   FREQUENCY — 100 squares, one per day. Natural-frequency framing, which the
 *               risk-communication literature consistently finds is understood
 *               better than percentages by non-specialists.
 *   RANGE     — nested interval bars. Best for "is my buffer inside the middle?"
 *   CURVE     — the density with the tail past the buffer filled. Best for
 *               seeing that the right tail is long, which the other two hide.
 *
 * The buffer selector then converts whichever view you prefer into the only
 * number that decides anything: how often you miss the flight.
 */

import React, { useMemo, useState } from 'react';
import { View, Text, ScrollView, StyleSheet } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import Svg, { Path, Line } from 'react-native-svg';
import { C, F, T, S, ink, waitColor, WAIT_LEGEND } from '@/constants/modernist';
import { Btn, Kicker, Segmented, SectionRule, Table } from '@/components/Modernist';
import {
  BUFFERS,
  CHECKPOINTS,
  HOURS,
  density,
  formatHour,
  hundredDays,
  mu,
  oneInLabel,
  pExceed,
  quantile,
} from '@/lib/waitModel';

type Mode = 'F' | 'B' | 'C';

const CURVE_W = 348;
const CURVE_H = 150;

export default function DistributionDetail() {
  const router = useRouter();
  const params = useLocalSearchParams<{ cp?: string; hour?: string }>();
  const cpIdx = Math.min(Number(params.cp ?? 2) || 0, CHECKPOINTS.length - 1);
  const hourIdx = Math.min(Number(params.hour ?? 2) || 0, HOURS.length - 1);

  const [mode, setMode] = useState<Mode>('F');
  const [buffer, setBuffer] = useState(45);

  const cp = CHECKPOINTS[cpIdx];
  const hour = HOURS[hourIdx];

  const v = useMemo(() => {
    const r = Math.round;
    const p10 = r(quantile(cp, hour, 0.1));
    const p50 = r(quantile(cp, hour, 0.5));
    const p90 = r(quantile(cp, hour, 0.9));

    // Axis maxima snap to 15-minute steps so tick labels stay legible.
    const xmax = Math.max(60, Math.ceil(quantile(cp, hour, 0.99) / 15) * 15);
    const pct = (x: number) => `${((Math.min(x, xmax) / xmax) * 100).toFixed(1)}%`;

    const axis = Array.from({ length: 5 }, (_, i) => {
      const t = (i * xmax) / 4;
      return { pos: `${((t / xmax) * 100).toFixed(1)}%`, label: r(t) };
    });

    const samples = hundredDays(cp, hour);
    const legend = WAIT_LEGEND.map((l) => ({
      ...l,
      count: `${samples.filter((w) => w > l.lo && w <= l.hi).length} days`,
    }));

    // ── Density path for the CURVE view ──────────────────────────────
    const N = 90;
    const pts: [number, number][] = [];
    let fmax = 0;
    for (let i = 0; i <= N; i++) {
      const x = (i / N) * xmax;
      const f = density(cp, hour, x);
      if (f > fmax) fmax = f;
      pts.push([x, f]);
    }
    const X = (x: number) => ((x / xmax) * CURVE_W).toFixed(1);
    const Y = (f: number) => (CURVE_H - (f / fmax) * (CURVE_H - 8)).toFixed(1);
    const seg = (list: [number, number][]) =>
      list.map((p, i) => `${i ? 'L' : 'M'}${X(p[0])},${Y(p[1])}`).join(' ');

    const linePath = seg(pts);
    const areaPath = `M0,${CURVE_H} ${seg(pts).slice(1)} L${CURVE_W},${CURVE_H} Z`;
    const tailPts = pts.filter((p) => p[0] >= buffer);
    const tailPath =
      tailPts.length > 1
        ? `M${X(tailPts[0][0])},${CURVE_H} ${seg(tailPts).slice(1)} L${X(
            tailPts[tailPts.length - 1][0],
          )},${CURVE_H} Z`
        : '';

    const risk = Math.max(pExceed(cp, hour, buffer), 0.004);

    return {
      p10,
      p50,
      p90,
      xmax,
      axis,
      samples,
      legend,
      linePath,
      areaPath,
      tailPath,
      medX: X(Math.exp(mu(cp, hour))),
      medPos: pct(Math.exp(mu(cp, hour))),
      bandOuterL: pct(quantile(cp, hour, 0.1)),
      bandOuterW: `${((Math.min(quantile(cp, hour, 0.9), xmax) - quantile(cp, hour, 0.1)) / xmax) * 100}%`,
      bandInnerL: pct(quantile(cp, hour, 0.25)),
      bandInnerW: `${((quantile(cp, hour, 0.75) - quantile(cp, hour, 0.25)) / xmax) * 100}%`,
      oneIn: oneInLabel(risk),
      tableRows: [
        { label: '1 day in 2', value: `${r(quantile(cp, hour, 0.5))} min` },
        { label: '3 days in 4', value: `${r(quantile(cp, hour, 0.75))} min` },
        { label: '9 days in 10', value: `${r(quantile(cp, hour, 0.9))} min` },
        { label: '19 days in 20', value: `${r(quantile(cp, hour, 0.95))} min` },
        { label: '99 days in 100', value: `${r(quantile(cp, hour, 0.99))} min` },
      ],
    };
  }, [cp, hour, buffer]);

  return (
    <View style={s.root}>
      {/* ── App bar ─────────────────────────────────────────────────── */}
      <View style={s.appBar}>
        <Text style={T.barTitle}>{cp.name}</Text>
        <View style={{ flex: 1 }} />
        <Text style={s.barMeta}>SEA · {formatHour(hour)}</Text>
      </View>

      <ScrollView>
        {/* ── Hero ──────────────────────────────────────────────────── */}
        <View style={s.hero}>
          <Kicker>HALF OF DAYS LIKE THIS ARE UNDER</Kicker>
          <View style={s.heroRow}>
            <Text style={T.hero}>{v.p50}</Text>
            <Text style={s.heroUnit}>MIN</Text>
          </View>
          <Text style={s.heroBody}>
            Nine days in ten land between <Text style={s.strong}>{v.p10}</Text> and{' '}
            <Text style={s.strong}>{v.p90}</Text> minutes. The point forecast would
            have told you {v.p50} and stopped there.
          </Text>
        </View>
        <SectionRule />

        {/* ── Mode tabs ─────────────────────────────────────────────── */}
        <Segmented
          items={[
            { key: 'F', label: 'FREQUENCY' },
            { key: 'B', label: 'RANGE' },
            { key: 'C', label: 'CURVE' },
          ]}
          activeKey={mode}
          onSelect={(k) => setMode(k as Mode)}
          fill
          fontSize={12}
          letterSpacing={0.96}
          paddingVertical={12}
          paddingLeft={14}
          borderless
        />
        <SectionRule />

        {/* ── FREQUENCY ─────────────────────────────────────────────── */}
        {mode === 'F' && (
          <>
            <View style={s.block}>
              <Kicker>100 DAYS LIKE TODAY · ONE SQUARE EACH</Kicker>
              <View style={s.dotGrid}>
                {v.samples.map((w, i) => (
                  <View key={i} style={[s.dot, { backgroundColor: waitColor(w) }]} />
                ))}
              </View>
              <View style={s.legendWrap}>
                {v.legend.map((l) => (
                  <View key={l.label} style={s.legendItem}>
                    <View style={[s.legendSwatch, { backgroundColor: l.color }]} />
                    <Text style={s.legendLabel}>{l.label}</Text>
                    <Text style={s.legendCount}>{l.count}</Text>
                  </View>
                ))}
              </View>
            </View>
            <SectionRule />
          </>
        )}

        {/* ── RANGE ─────────────────────────────────────────────────── */}
        {mode === 'B' && (
          <>
            <View style={s.blockWide}>
              <Kicker>WHERE THE MIDDLE SITS</Kicker>
              <View style={s.bandArea}>
                <Text style={[s.bandMedLabel, { left: v.medPos as any }]}>
                  {v.p50} MIN
                </Text>
                {/* full range */}
                <View style={s.bandTrack} />
                {/* middle 80% */}
                <View
                  style={[
                    s.bandOuter,
                    { left: v.bandOuterL as any, width: v.bandOuterW as any },
                  ]}
                />
                {/* middle half */}
                <View
                  style={[
                    s.bandInner,
                    { left: v.bandInnerL as any, width: v.bandInnerW as any },
                  ]}
                />
                {/* median rule */}
                <View style={[s.bandMedian, { left: v.medPos as any }]} />
              </View>
              <View style={s.axis}>
                {v.axis.map((t) => (
                  <Text key={t.label} style={[s.axisLabel, { left: t.pos as any }]}>
                    {t.label}
                  </Text>
                ))}
              </View>
              <View style={s.bandLegend}>
                <LegendKey color={C.accent300} label="middle half" />
                <LegendKey color={C.neutral400} label="middle 80%" />
                <LegendKey color={C.neutral200} label="full range" />
              </View>
            </View>
            <SectionRule />
          </>
        )}

        {/* ── CURVE ─────────────────────────────────────────────────── */}
        {mode === 'C' && (
          <>
            <View style={s.block}>
              <Kicker>DENSITY · RED IS THE TAIL PAST {buffer} MIN</Kicker>
              <Svg
                viewBox={`0 0 ${CURVE_W} ${CURVE_H}`}
                width="100%"
                height={CURVE_H}
                style={{ marginTop: 12 }}
              >
                <Path d={v.areaPath} fill={C.neutral300} />
                <Path d={v.tailPath} fill={C.accent500} />
                <Path d={v.linePath} fill="none" stroke={C.text} strokeWidth={2} />
                <Line
                  x1={v.medX}
                  y1={0}
                  x2={v.medX}
                  y2={CURVE_H}
                  stroke={C.accent}
                  strokeWidth={2}
                />
              </Svg>
              <View style={s.axis}>
                {v.axis.map((t) => (
                  <Text key={t.label} style={[s.axisLabel, { left: t.pos as any }]}>
                    {t.label}
                  </Text>
                ))}
              </View>
            </View>
            <SectionRule />
          </>
        )}

        {/* ── Buffer / risk ─────────────────────────────────────────── */}
        <View style={s.block}>
          <Kicker>TIME YOU'VE ALLOWED FOR SECURITY</Kicker>
          <View style={{ marginTop: 10 }}>
            <Segmented
              items={BUFFERS.map((b) => ({ key: String(b), label: `${b} min` }))}
              activeKey={String(buffer)}
              onSelect={(k) => setBuffer(Number(k))}
              fill
              paddingVertical={10}
              paddingLeft={12}
            />
          </View>
          <View style={s.riskRow}>
            <Text style={T.risk}>{v.oneIn}</Text>
            <Text style={s.riskBody}>
              days like this, security runs longer than the {buffer} minutes you
              left it.
            </Text>
          </View>
        </View>
        <SectionRule />

        {/* ── Full quantile table ───────────────────────────────────── */}
        <View style={s.tableHead}>
          <Kicker>THE WHOLE FORECAST</Kicker>
        </View>
        <View style={{ paddingHorizontal: S.s4 }}>
          <Table head={['How often', 'Wait is under']} rows={v.tableRows} />
        </View>

        {/* ── Footer actions ────────────────────────────────────────── */}
        <View style={s.footer}>
          <Btn label="Alert me if this shifts" style={{ flex: 1, justifyContent: 'flex-start' }} />
          <Btn label="Share" variant="secondary" style={{ justifyContent: 'flex-start' }} />
        </View>
      </ScrollView>
    </View>
  );
}

function LegendKey({ color, label }: { color: string; label: string }) {
  return (
    <View style={s.legendItem}>
      <View style={[s.legendSwatch, { width: 14, height: 10 }, { backgroundColor: color }]} />
      <Text style={s.legendLabel}>{label}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },

  appBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: S.s3,
    paddingHorizontal: S.s4,
    paddingTop: 14,
    paddingBottom: S.s3,
    borderBottomWidth: 2,
    borderBottomColor: C.divider,
  },
  barMeta: {
    fontFamily: F.headingSemi,
    fontSize: 11,
    letterSpacing: 1.1,
    color: ink(55),
  },

  hero: { paddingHorizontal: S.s4, paddingTop: 20, paddingBottom: 18 },
  heroRow: { flexDirection: 'row', alignItems: 'baseline', gap: S.s2, marginTop: 4 },
  heroUnit: { fontFamily: F.heading, fontSize: 20, color: C.text },
  heroBody: { ...T.body, marginTop: S.s3 },
  strong: { fontFamily: F.heading },

  block: { paddingHorizontal: S.s4, paddingTop: 18, paddingBottom: 20 },
  blockWide: { paddingHorizontal: S.s4, paddingTop: 22, paddingBottom: 20 },

  dotGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 4, marginTop: S.s3 },
  dot: { width: 14, height: 14 },

  legendWrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 14, marginTop: S.s4 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  legendSwatch: { width: 10, height: 10 },
  legendLabel: { fontFamily: F.headingSemi, fontSize: 11.5, color: C.text },
  legendCount: { fontFamily: F.body, fontSize: 11.5, color: ink(55) },

  bandArea: { height: 64, marginTop: 26, position: 'relative' },
  bandTrack: {
    position: 'absolute',
    left: 0,
    right: 0,
    top: 22,
    height: 20,
    backgroundColor: C.neutral200,
  },
  bandOuter: { position: 'absolute', top: 22, height: 20, backgroundColor: C.neutral400 },
  bandInner: { position: 'absolute', top: 16, height: 32, backgroundColor: C.accent300 },
  bandMedian: { position: 'absolute', top: 8, height: 48, width: 3, backgroundColor: C.accent },
  bandMedLabel: {
    position: 'absolute',
    top: -14,
    fontFamily: F.heading,
    fontSize: 12,
    color: C.accent700,
  },

  axis: {
    position: 'relative',
    height: 18,
    borderTopWidth: 1,
    borderTopColor: C.divider,
  },
  axisLabel: {
    position: 'absolute',
    top: 4,
    fontFamily: F.body,
    fontSize: 11,
    color: ink(55),
  },
  bandLegend: { flexDirection: 'row', gap: 18, marginTop: 18 },

  riskRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 14, marginTop: 18 },
  riskBody: { ...T.body, flex: 1, lineHeight: 20, paddingBottom: 4 },

  tableHead: { paddingHorizontal: S.s4, paddingTop: 18, paddingBottom: S.s2 },

  footer: {
    flexDirection: 'row',
    gap: 10,
    borderTopWidth: 2,
    borderTopColor: C.divider,
    paddingHorizontal: S.s4,
    paddingTop: 14,
    paddingBottom: 20,
    marginTop: 18,
  },
});
