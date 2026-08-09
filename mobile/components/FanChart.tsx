/**
 * Fan chart: the core visual of PaxCast.
 *
 * Design rules enforced here, not left to callers:
 *  - The bands are drawn first and are the dominant visual element. The median
 *    is a thin line on top, never a bold hero line, because a bold median
 *    invites the reader to treat it as "the answer".
 *  - Band opacity decreases outward, so the eye reads width as uncertainty.
 *  - A scenario overlay uses a different hue, so baseline-vs-scenario is never
 *    ambiguous.
 *  - Declared terminal capacity is drawn as a hard rule, since the question
 *    operators actually ask is "do we breach capacity", not "what's the mean".
 */

import React, { useMemo } from 'react';
import { View, Text, StyleSheet, Pressable } from 'react-native';
import Svg, { Path, Line, Circle, Text as SvgText, G } from 'react-native-svg';
import { Colors, Spacing, Type, Radius } from '@/constants/theme';
import type { Percentiles } from '@/lib/api';
import { formatPax } from '@/lib/api';

interface Props {
  dates: string[];
  percentiles: Percentiles;
  scenarioPercentiles?: Percentiles;
  capacityLine?: number;
  height?: number;
  selectedIndex?: number | null;
  onSelectIndex?: (i: number | null) => void;
}

const PAD = { top: 16, right: 12, bottom: 26, left: 46 };

export function FanChart({
  dates,
  percentiles,
  scenarioPercentiles,
  capacityLine,
  height = 260,
  selectedIndex = null,
  onSelectIndex,
}: Props) {
  const [width, setWidth] = React.useState(340);

  const geom = useMemo(() => {
    const n = dates.length;
    const plotW = Math.max(width - PAD.left - PAD.right, 10);
    const plotH = Math.max(height - PAD.top - PAD.bottom, 10);

    const pools = [percentiles.p5, percentiles.p95];
    if (scenarioPercentiles) pools.push(scenarioPercentiles.p5, scenarioPercentiles.p95);
    let lo = Math.min(...pools.map((a) => Math.min(...a)));
    let hi = Math.max(...pools.map((a) => Math.max(...a)));
    if (capacityLine) hi = Math.max(hi, capacityLine * 1.02);

    // Always include a little headroom, and never let a flat series collapse.
    const span = Math.max(hi - lo, 1);
    lo = Math.max(0, lo - span * 0.08);
    hi = hi + span * 0.08;

    const x = (i: number) => PAD.left + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    const y = (v: number) => PAD.top + plotH - ((v - lo) / (hi - lo)) * plotH;

    return { n, plotW, plotH, lo, hi, x, y };
  }, [dates.length, percentiles, scenarioPercentiles, capacityLine, width, height]);

  /** Closed polygon between an upper and lower percentile series. */
  const band = (upper: number[], lower: number[]): string => {
    const { x, y } = geom;
    const up = upper.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    const down = lower
      .map((v, i) => v)
      .reverse()
      .map((v, i) => `L${x(lower.length - 1 - i).toFixed(1)},${y(v).toFixed(1)}`);
    return `${up.join('')}${down.join('')}Z`;
  };

  const line = (series: number[]): string => {
    const { x, y } = geom;
    return series
      .map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`)
      .join('');
  };

  const ticks = useMemo(() => {
    const { lo, hi } = geom;
    return [lo, lo + (hi - lo) / 2, hi];
  }, [geom]);

  const dateLabels = useMemo(() => {
    const n = dates.length;
    const idx = n <= 1 ? [0] : [0, Math.floor(n / 2), n - 1];
    return idx.map((i) => ({
      i,
      label: new Date(dates[i]).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }),
    }));
  }, [dates]);

  const handlePress = (evt: { nativeEvent: { locationX: number } }) => {
    if (!onSelectIndex) return;
    const { locationX } = evt.nativeEvent;
    const rel = (locationX - PAD.left) / geom.plotW;
    const i = Math.round(rel * (geom.n - 1));
    if (i < 0 || i >= geom.n) return onSelectIndex(null);
    onSelectIndex(i === selectedIndex ? null : i);
  };

  return (
    <View onLayout={(e) => setWidth(e.nativeEvent.layout.width)}>
      <Pressable onPress={handlePress}>
        <Svg width="100%" height={height}>
          {/* horizontal gridlines */}
          {ticks.map((t, k) => (
            <G key={`t${k}`}>
              <Line
                x1={PAD.left}
                x2={width - PAD.right}
                y1={geom.y(t)}
                y2={geom.y(t)}
                stroke={Colors.border}
                strokeWidth={1}
                strokeDasharray="3 5"
              />
              <SvgText
                x={PAD.left - 6}
                y={geom.y(t) + 4}
                fill={Colors.textFaint}
                fontSize={10}
                textAnchor="end"
              >
                {formatPax(t)}
              </SvgText>
            </G>
          ))}

          {/* baseline bands, faintest outermost */}
          <Path d={band(percentiles.p95, percentiles.p5)} fill={Colors.band95} />
          <Path d={band(percentiles.p90, percentiles.p10)} fill={Colors.band90} />
          <Path d={band(percentiles.p75, percentiles.p25)} fill={Colors.band75} />

          {/* scenario overlay */}
          {scenarioPercentiles && (
            <>
              <Path
                d={band(scenarioPercentiles.p90, scenarioPercentiles.p10)}
                fill={Colors.scenarioBand}
              />
              <Path
                d={line(scenarioPercentiles.p50)}
                stroke={Colors.scenario}
                strokeWidth={1.75}
                fill="none"
                strokeDasharray="5 3"
              />
            </>
          )}

          {/* declared capacity */}
          {capacityLine && capacityLine <= geom.hi && (
            <>
              <Line
                x1={PAD.left}
                x2={width - PAD.right}
                y1={geom.y(capacityLine)}
                y2={geom.y(capacityLine)}
                stroke={Colors.capacity}
                strokeWidth={1.25}
                strokeDasharray="6 3"
              />
              <SvgText
                x={width - PAD.right}
                y={geom.y(capacityLine) - 5}
                fill={Colors.capacity}
                fontSize={9}
                textAnchor="end"
              >
                DECLARED CAPACITY
              </SvgText>
            </>
          )}

          {/* median: thin, deliberately not a hero line */}
          <Path d={line(percentiles.p50)} stroke={Colors.median} strokeWidth={1.75} fill="none" />

          {/* selection readout */}
          {selectedIndex !== null && selectedIndex < geom.n && (
            <G>
              <Line
                x1={geom.x(selectedIndex)}
                x2={geom.x(selectedIndex)}
                y1={PAD.top}
                y2={height - PAD.bottom}
                stroke={Colors.text}
                strokeWidth={1}
                opacity={0.35}
              />
              <Circle
                cx={geom.x(selectedIndex)}
                cy={geom.y(percentiles.p50[selectedIndex])}
                r={4}
                fill={Colors.median}
              />
            </G>
          )}

          {dateLabels.map(({ i, label }) => (
            <SvgText
              key={`d${i}`}
              x={geom.x(i)}
              y={height - 8}
              fill={Colors.textFaint}
              fontSize={10}
              textAnchor={i === 0 ? 'start' : i === geom.n - 1 ? 'end' : 'middle'}
            >
              {label}
            </SvgText>
          ))}
        </Svg>
      </Pressable>

      <View style={styles.legend}>
        <LegendSwatch color={Colors.band75} label="50%" />
        <LegendSwatch color={Colors.band90} label="80%" />
        <LegendSwatch color={Colors.band95} label="90%" />
        <View style={styles.legendItem}>
          <View style={[styles.legendLine, { backgroundColor: Colors.median }]} />
          <Text style={styles.legendText}>median</Text>
        </View>
        {scenarioPercentiles && (
          <View style={styles.legendItem}>
            <View style={[styles.legendLine, { backgroundColor: Colors.scenario }]} />
            <Text style={styles.legendText}>scenario</Text>
          </View>
        )}
      </View>
    </View>
  );
}

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <View style={styles.legendItem}>
      <View style={[styles.legendBox, { backgroundColor: color }]} />
      <Text style={styles.legendText}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  legend: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: Spacing.md,
    marginTop: Spacing.sm,
    paddingHorizontal: Spacing.sm,
  },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  legendBox: { width: 14, height: 10, borderRadius: 2 },
  legendLine: { width: 14, height: 2, borderRadius: 1 },
  legendText: { ...Type.caption, color: Colors.textFaint },
});
