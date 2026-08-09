/**
 * Forecast screen.
 *
 * Layout order is deliberate and reflects the product thesis:
 *   1. band readout   -- the distribution, never a bare number
 *   2. fan chart      -- shape of uncertainty over the horizon
 *   3. capacity risk  -- the decision the operator actually faces
 *   4. peak hour grid -- when, not just how many
 *   5. diagnostics    -- how much to trust any of the above
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, ActivityIndicator,
  Pressable, RefreshControl,
} from 'react-native';
import { useLocalSearchParams, useRouter, Stack } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { api, formatFull, formatPax, type Forecast } from '@/lib/api';
import { FanChart } from '@/components/FanChart';
import { PeakHourGrid } from '@/components/PeakHourGrid';
import { Card, SectionTitle, ConfidenceBadge, BandReadout, Stat, Chip } from '@/components/Ui';
import { Colors, Spacing, Type, Radius } from '@/constants/theme';

const HORIZONS = [
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
  { label: '1y', days: 365 },
];

export default function AirportScreen() {
  const { iata } = useLocalSearchParams<{ iata: string }>();
  const router = useRouter();
  const [horizon, setHorizon] = useState(30);
  const [data, setData] = useState<Forecast | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (!iata) return;
    setLoading(true);
    setError(null);
    try {
      setData(await api.forecast(iata, horizon));
    } catch {
      setError('Forecast service unreachable and no cached forecast for this horizon.');
    } finally {
      setLoading(false);
    }
  }, [iata, horizon]);

  useEffect(() => {
    load();
    setSel(null);
  }, [load]);

  if (loading && !data) {
    return (
      <View style={s.center}>
        <ActivityIndicator color={Colors.median} />
        <Text style={s.loadingText}>Running {formatPax(20000)} simulations…</Text>
      </View>
    );
  }

  if (error && !data) {
    return (
      <View style={s.center}>
        <Ionicons name="cloud-offline-outline" size={34} color={Colors.textFaint} />
        <Text style={s.errorText}>{error}</Text>
        <Pressable style={s.retry} onPress={load}>
          <Text style={s.retryText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  if (!data) return null;

  const i = sel ?? 0;
  const p = data.percentiles;
  const cap = data.exceedance.daily_capacity_pax;
  const pExceed = data.exceedance.p_exceed_daily_capacity;
  const util = data.exceedance.peak_hour_utilisation;

  return (
    <ScrollView
      style={s.root}
      contentContainerStyle={{ padding: Spacing.lg }}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={Colors.median} />}
    >
      <Stack.Screen options={{ title: data.airport?.iata ?? iata }} />

      <View style={s.header}>
        <View style={{ flex: 1 }}>
          <Text style={s.title}>{data.airport?.name ?? iata}</Text>
          <Text style={s.subtitle}>
            {data.airport?.city}, {data.airport?.country} · {data.airport?.daily_flights} movements/day
          </Text>
        </View>
        <ConfidenceBadge level={data.confidence} />
      </View>

      {data.stale && (
        <View style={s.staleBanner}>
          <Ionicons name="time-outline" size={13} color={Colors.warning} />
          <Text style={s.staleText}>Offline — showing a cached forecast more than 6 hours old.</Text>
        </View>
      )}

      <View style={s.chips}>
        {HORIZONS.map((h) => (
          <Chip
            key={h.days}
            label={h.label}
            active={horizon === h.days}
            onPress={() => setHorizon(h.days)}
          />
        ))}
      </View>

      <Card>
        <BandReadout
          label={sel === null ? 'FIRST DAY OF HORIZON' : new Date(data.dates[i]).toDateString().toUpperCase()}
          low={p.p10[i]}
          mid={p.p50[i]}
          high={p.p90[i]}
        />
      </Card>

      <Card>
        <SectionTitle hint="Tap the chart to inspect any day">Daily throughput</SectionTitle>
        <FanChart
          dates={data.dates}
          percentiles={p}
          capacityLine={cap}
          selectedIndex={sel}
          onSelectIndex={setSel}
        />
      </Card>

      <Card>
        <SectionTitle hint="Against declared terminal capacity">Capacity risk</SectionTitle>
        <View style={s.statRow}>
          <Stat
            label="chance of exceeding daily capacity"
            value={`${(pExceed * 100).toFixed(1)}%`}
            tone={pExceed > 0.15 ? Colors.danger : pExceed > 0.03 ? Colors.warning : Colors.success}
          />
          <Stat
            label="peak-hour utilisation"
            value={`${(util * 100).toFixed(0)}%`}
            tone={util > 1 ? Colors.danger : util > 0.85 ? Colors.warning : Colors.success}
          />
        </View>
        <Text style={s.note}>
          Declared capacity {formatFull(data.exceedance.peak_hour_capacity)} pax/hour
          ({formatPax(cap)} per operating day). Peak-hour figure is the busiest
          modelled hour across the week.
        </Text>
      </Card>

      <Card>
        <SectionTitle hint="Expected terminal load by weekday and hour">Peak-hour profile</SectionTitle>
        <PeakHourGrid grid={data.peak_hour_grid} capacity={data.exceedance.peak_hour_capacity} />
      </Card>

      <Card>
        <SectionTitle>Horizon total</SectionTitle>
        <View style={s.statRow}>
          <Stat label="P10" value={formatPax(data.total_percentiles.p10)} />
          <Stat label="median" value={formatPax(data.total_percentiles.p50)} />
          <Stat label="P90" value={formatPax(data.total_percentiles.p90)} />
        </View>
      </Card>

      <Pressable style={s.cta} onPress={() => router.push(`/scenario/${iata}`)}>
        <Ionicons name="git-branch-outline" size={17} color={Colors.bg} />
        <Text style={s.ctaText}>Run a scenario</Text>
      </Pressable>

      <Card>
        <SectionTitle hint="Shown because a forecast you cannot audit is not worth acting on">
          Model diagnostics
        </SectionTitle>
        <View style={s.diagRow}>
          <Text style={s.diagKey}>Iterations</Text>
          <Text style={s.diagVal}>{formatFull(data.n_iterations)}</Text>
        </View>
        <View style={s.diagRow}>
          <Text style={s.diagKey}>P90 relative standard error</Text>
          <Text style={s.diagVal}>{(data.p90_rel_se * 100).toFixed(2)}%</Text>
        </View>
        <View style={s.diagRow}>
          <Text style={s.diagKey}>Converged</Text>
          <Text style={s.diagVal}>{data.converged ? 'yes' : 'no'}</Text>
        </View>
        <View style={s.diagRow}>
          <Text style={s.diagKey}>Schedule data quality</Text>
          <Text style={s.diagVal}>{(data.data_quality * 100).toFixed(0)}%</Text>
        </View>
        <View style={s.diagRow}>
          <Text style={s.diagKey}>Compute time</Text>
          <Text style={s.diagVal}>{data.runtime_ms.toFixed(0)} ms</Text>
        </View>
      </Card>

      <Text style={s.disclaimer}>
        PaxCast reports a distribution, not a prediction. Bands describe modelled
        uncertainty given the current schedule and historical variability; they do
        not account for events absent from the model.
      </Text>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.bg },
  center: { flex: 1, backgroundColor: Colors.bg, alignItems: 'center', justifyContent: 'center', gap: Spacing.md, padding: Spacing.xl },
  loadingText: { ...Type.caption, color: Colors.textFaint },
  errorText: { ...Type.body, color: Colors.textMuted, textAlign: 'center' },
  retry: { paddingHorizontal: Spacing.lg, paddingVertical: Spacing.sm, borderRadius: Radius.sm, borderWidth: 1, borderColor: Colors.border },
  retryText: { ...Type.label, color: Colors.median },
  header: { flexDirection: 'row', alignItems: 'flex-start', gap: Spacing.md, marginBottom: Spacing.lg },
  title: { ...Type.title, color: Colors.text },
  subtitle: { ...Type.caption, color: Colors.textFaint, marginTop: 3 },
  staleBanner: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: 'rgba(242,163,60,0.10)', borderColor: Colors.warning, borderWidth: 1,
    borderRadius: Radius.sm, padding: Spacing.sm, marginBottom: Spacing.md,
  },
  staleText: { ...Type.caption, color: Colors.warning, flex: 1 },
  chips: { flexDirection: 'row', gap: Spacing.sm, marginBottom: Spacing.md },
  statRow: { flexDirection: 'row', gap: Spacing.md, marginTop: Spacing.sm },
  note: { ...Type.caption, color: Colors.textFaint, marginTop: Spacing.md, lineHeight: 15 },
  cta: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: Spacing.sm,
    backgroundColor: Colors.median, borderRadius: Radius.md,
    paddingVertical: 13, marginBottom: Spacing.md,
  },
  ctaText: { ...Type.heading, color: Colors.bg },
  diagRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 5 },
  diagKey: { ...Type.caption, color: Colors.textFaint },
  diagVal: { ...Type.caption, color: Colors.textMuted, fontVariant: ['tabular-nums'] },
  disclaimer: {
    ...Type.caption, color: Colors.textFaint, lineHeight: 15,
    marginTop: Spacing.sm, marginBottom: Spacing.xxl,
  },
});
