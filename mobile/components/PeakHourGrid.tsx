/**
 * Peak-hour heatmap: weekday x hour expected terminal load.
 *
 * This is the screen that answers the operational question the fan chart
 * cannot -- not "how many passengers on Tuesday" but "when on Tuesday", which
 * is what actually determines queue length and roster shape. Cells are shaded
 * against declared hourly capacity, so anything at or above capacity reads red
 * without the user doing arithmetic.
 */
import React, { useMemo } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Colors, Spacing, Type, Radius } from '@/constants/theme';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

export function PeakHourGrid({ grid, capacity }: { grid: number[][]; capacity: number }) {
  const max = useMemo(() => Math.max(1, ...grid.flat()), [grid]);

  const cellColor = (v: number): string => {
    if (v <= 0) return 'rgba(255,255,255,0.03)';
    const utilisation = capacity > 0 ? v / capacity : v / max;
    if (utilisation >= 1.0) return 'rgba(255, 92, 122, 0.85)';
    if (utilisation >= 0.85) return 'rgba(242, 163, 60, 0.80)';
    const t = Math.min(v / max, 1);
    return `rgba(94, 158, 255, ${0.10 + t * 0.75})`;
  };

  return (
    <View>
      <View style={s.hourAxis}>
        {[0, 6, 12, 18, 23].map((h) => (
          <Text key={h} style={[s.hourLabel, { left: 34 + (h / 23) * 0.86 * 300 }]}>
            {String(h).padStart(2, '0')}
          </Text>
        ))}
      </View>
      {grid.map((row, d) => (
        <View key={d} style={s.row}>
          <Text style={s.dayLabel}>{DAYS[d]}</Text>
          <View style={s.cells}>
            {HOURS.map((h) => (
              <View key={h} style={[s.cell, { backgroundColor: cellColor(row[h] ?? 0) }]} />
            ))}
          </View>
        </View>
      ))}
      <View style={s.legend}>
        <View style={[s.swatch, { backgroundColor: 'rgba(94,158,255,0.30)' }]} />
        <Text style={s.legendText}>light</Text>
        <View style={[s.swatch, { backgroundColor: 'rgba(94,158,255,0.85)' }]} />
        <Text style={s.legendText}>busy</Text>
        <View style={[s.swatch, { backgroundColor: 'rgba(242,163,60,0.80)' }]} />
        <Text style={s.legendText}>&gt;85% cap</Text>
        <View style={[s.swatch, { backgroundColor: 'rgba(255,92,122,0.85)' }]} />
        <Text style={s.legendText}>over cap</Text>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  hourAxis: { height: 14, position: 'relative', marginBottom: 2 },
  hourLabel: { ...Type.caption, color: Colors.textFaint, position: 'absolute', fontSize: 9 },
  row: { flexDirection: 'row', alignItems: 'center', marginBottom: 3 },
  dayLabel: { ...Type.caption, color: Colors.textFaint, width: 30 },
  cells: { flexDirection: 'row', flex: 1, gap: 1.5 },
  cell: { flex: 1, height: 16, borderRadius: 2 },
  legend: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    marginTop: Spacing.md, flexWrap: 'wrap',
  },
  swatch: { width: 12, height: 10, borderRadius: 2 },
  legendText: { ...Type.caption, color: Colors.textFaint, marginRight: Spacing.sm },
});
