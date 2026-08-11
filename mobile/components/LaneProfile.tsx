/**
 * Lane profile: how many security lanes to open, hour by hour.
 *
 * Two quantities are drawn together on purpose. `required` is the bare minimum
 * for each hour; `planned` is the smoothed profile that is actually staffable.
 * Showing only the smoothed one would hide why an hour holds five lanes when it
 * needs two — the answer being that closing three lanes for sixty minutes and
 * reopening them costs more than leaving them open.
 *
 * The basis is stated rather than implied. With no reported waits these numbers
 * come from a queue model whose own docstring calls its constants engineering
 * estimates, and a staffing figure shown without that caveat would be the most
 * consequential unlabelled number in the app.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { C, F, S, T, ink } from '@/constants/modernist';
import { fetchLanePlan, type LanePlan } from '@/lib/api';

const TARGETS = [10, 15, 20, 30];

// Grey for lanes the model demands, lighter grey for lanes held open only to
// avoid churn, accent red reserved for hours the checkpoint cannot serve at
// all. Red means "a decision is needed here", not "this bar is important".
const AT_MINIMUM = C.neutral700;
const CONTINUITY = C.neutral400;
const MISSED = C.accent;

export function LaneProfile({ checkpointId }: { checkpointId: number }) {
  const [plan, setPlan] = useState<LanePlan | null>(null);
  const [target, setTarget] = useState(15);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPlan(await fetchLanePlan(checkpointId, { targetWait: target }));
    } catch {
      setError('Could not build a lane plan for this checkpoint.');
    } finally {
      setLoading(false);
    }
  }, [checkpointId, target]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && !plan) {
    return (
      <View style={s.centre}>
        <ActivityIndicator color={C.accent} />
        <Text style={s.muted}>Modelling hourly demand…</Text>
      </View>
    );
  }
  if (error || !plan) {
    return <Text style={s.error}>{error ?? 'No plan available.'}</Text>;
  }

  const maxLanes = Math.max(plan.physical_lanes, ...plan.planned, 1);

  return (
    <View>
      <Text style={s.kicker}>
        HOLD {plan.target_wait_min} MIN FOR {Math.round(plan.service_level * 100)}% OF PASSENGERS
      </Text>

      <View style={s.targets}>
        {TARGETS.map((t) => (
          <Pressable
            key={t}
            onPress={() => setTarget(t)}
            style={[s.chip, t === target && s.chipOn]}
            accessibilityRole="button"
          >
            <Text style={[s.chipText, t === target && s.chipTextOn]}>{t}m</Text>
          </Pressable>
        ))}
      </View>

      <View style={s.summaryRow}>
        <Summary label="LANE-HOURS" value={String(plan.lane_hours)} />
        <Summary label="BUSIEST" value={`${String(plan.peak_hour).padStart(2, '0')}:00`} />
        <Summary label="PEAK LANES" value={`${Math.max(...plan.planned)}/${plan.physical_lanes}`} />
      </View>

      <View style={s.chart}>
        {plan.hours.map((h) => {
          const planned = plan.planned[h];
          const required = plan.required[h];
          const short = plan.understaffed_hours.includes(h);
          return (
            <View key={h} style={s.col}>
              <View style={s.barBox}>
                <View
                  style={[
                    s.bar,
                    {
                      height: `${(planned / maxLanes) * 100}%`,
                      backgroundColor: short
                        ? MISSED
                        : planned > required
                          ? CONTINUITY
                          : AT_MINIMUM,
                    },
                  ]}
                />
              </View>
              <Text style={s.hourLabel}>{h % 6 === 0 ? String(h).padStart(2, '0') : ''}</Text>
            </View>
          );
        })}
      </View>

      <View style={s.legend}>
        <LegendDot colour={AT_MINIMUM} label="at the minimum" />
        <LegendDot colour={CONTINUITY} label="held open for continuity" />
        {plan.understaffed_hours.length > 0 && <LegendDot colour={MISSED} label="target missed" />}
      </View>

      {plan.caveat_capacity ? <Text style={s.warn}>{plan.caveat_capacity}</Text> : null}

      <Text style={s.basis}>
        {plan.basis === 'fitted'
          ? `Calibrated against ${plan.fit_n} reported wait${plan.fit_n === 1 ? '' : 's'} at this checkpoint.`
          : plan.caveat}
      </Text>
    </View>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.summary}>
      <Text style={s.summaryValue}>{value}</Text>
      <Text style={s.summaryLabel}>{label}</Text>
    </View>
  );
}

function LegendDot({ colour, label }: { colour: string; label: string }) {
  return (
    <View style={s.legendItem}>
      <View style={[s.dot, { backgroundColor: colour }]} />
      <Text style={s.legendText}>{label}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  centre: { paddingVertical: S.s6, alignItems: 'center', gap: S.s2 },
  muted: { ...T.small, color: ink(55) },
  error: { ...T.small, color: C.accent, paddingVertical: S.s3 },
  kicker: { ...T.kicker, marginBottom: S.s2 },

  targets: { flexDirection: 'row', gap: S.s1, marginBottom: S.s3 },
  chip: {
    paddingHorizontal: S.s3,
    paddingVertical: S.s1,
    borderWidth: 1,
    borderColor: ink(18),
  },
  chipOn: { backgroundColor: C.text, borderColor: C.text },
  chipText: { ...T.small, color: ink(70), fontFamily: F.headingSemi },
  chipTextOn: { color: C.bg },

  summaryRow: { flexDirection: 'row', gap: S.s4, marginBottom: S.s3 },
  summary: { flex: 1 },
  summaryValue: { ...T.rowValue, color: C.text },
  summaryLabel: { ...T.kicker },

  chart: { flexDirection: 'row', height: 120, alignItems: 'flex-end', gap: 2 },
  col: { flex: 1, height: '100%', justifyContent: 'flex-end' },
  barBox: { flex: 1, justifyContent: 'flex-end' },
  bar: { width: '100%', minHeight: 1 },
  hourLabel: { ...T.tiny, color: ink(45), textAlign: 'center', marginTop: 2 },

  legend: { flexDirection: 'row', flexWrap: 'wrap', gap: S.s3, marginTop: S.s2 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  dot: { width: 8, height: 8 },
  legendText: { ...T.tiny, color: ink(55) },

  warn: { ...T.small, color: C.accent, marginTop: S.s2 },
  basis: { ...T.tiny, color: ink(50), marginTop: S.s2, lineHeight: 15 },
});
