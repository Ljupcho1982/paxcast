/**
 * Scenario builder.
 *
 * This screen is the argument for Monte Carlo over a fitted point forecast: a
 * scenario is a re-parameterisation of the same model, so "what if load factors
 * drop 8 points and one carrier is grounded" is answerable in seconds without
 * refitting anything.
 *
 * Baseline and scenario are requested in a single call so both runs share a
 * seed -- otherwise the user would be looking at Monte Carlo noise between two
 * independent runs and mistaking it for scenario effect.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, Pressable,
  ActivityIndicator, Switch,
} from 'react-native';
import { useLocalSearchParams, Stack } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { api, formatFull, formatPax, type Forecast, type Preset, type ScenarioInput } from '@/lib/api';
import { FanChart } from '@/components/FanChart';
import { Card, SectionTitle, Chip } from '@/components/Ui';
import { Colors, Spacing, Type, Radius } from '@/constants/theme';

interface Result {
  baseline: Forecast;
  scenario: Forecast;
  delta: { total_p50_absolute: number; total_p50_percent: number };
}

const LF_STEPS = [-0.15, -0.10, -0.05, 0, 0.05];
const CAP_STEPS = [0.5, 0.65, 0.8, 1.0, 1.15];
const CANCEL_STEPS = [0, 0.1, 0.25, 0.4];

export default function ScenarioScreen() {
  const { iata } = useLocalSearchParams<{ iata: string }>();
  const [presets, setPresets] = useState<Preset[]>([]);
  const [lf, setLf] = useState(0);
  const [capMul, setCapMul] = useState(1);
  const [cancel, setCancel] = useState(0);
  const [noShocks, setNoShocks] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.presets().then((r) => setPresets(r.presets)).catch(() => setPresets([]));
  }, []);

  const applyPreset = (p: Preset) => {
    setLf(p.scenario.load_factor_delta ?? 0);
    setCapMul(p.scenario.capacity_multiplier ?? 1);
    setCancel(p.scenario.extra_cancel_prob ?? 0);
    setNoShocks(p.scenario.disable_shocks ?? false);
  };

  const run = useCallback(async () => {
    if (!iata) return;
    setRunning(true);
    setError(null);
    const scenario: ScenarioInput = {
      name: 'Custom scenario',
      load_factor_delta: lf,
      capacity_multiplier: capMul,
      extra_cancel_prob: cancel,
      disable_shocks: noShocks,
    };
    try {
      setResult(await api.scenario(iata, 30, scenario));
    } catch {
      setError('Could not run the scenario. Scenarios require a live connection.');
    } finally {
      setRunning(false);
    }
  }, [iata, lf, capMul, cancel, noShocks]);

  const dirty = lf !== 0 || capMul !== 1 || cancel !== 0 || noShocks;

  return (
    <ScrollView style={s.root} contentContainerStyle={{ padding: Spacing.lg }}>
      <Stack.Screen options={{ title: `${iata} scenario` }} />

      <Card>
        <SectionTitle hint="Tap to load a preset, then adjust below">Presets</SectionTitle>
        <View style={s.wrap}>
          {presets.map((p) => (
            <Chip key={p.id} label={p.label} onPress={() => applyPreset(p)} />
          ))}
        </View>
      </Card>

      <Card>
        <SectionTitle hint="Shift in mean load factor across every flight">
          Load factor {lf === 0 ? '(unchanged)' : `${lf > 0 ? '+' : ''}${(lf * 100).toFixed(0)} pts`}
        </SectionTitle>
        <View style={s.wrap}>
          {LF_STEPS.map((v) => (
            <Chip
              key={v}
              label={v === 0 ? 'base' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(0)}`}
              active={lf === v}
              onPress={() => setLf(v)}
            />
          ))}
        </View>
      </Card>

      <Card>
        <SectionTitle hint="Seat capacity, e.g. runway works or a fleet change">
          Capacity {capMul === 1 ? '(unchanged)' : `${(capMul * 100).toFixed(0)}%`}
        </SectionTitle>
        <View style={s.wrap}>
          {CAP_STEPS.map((v) => (
            <Chip
              key={v}
              label={v === 1 ? 'base' : `${(v * 100).toFixed(0)}%`}
              active={capMul === v}
              onPress={() => setCapMul(v)}
            />
          ))}
        </View>
      </Card>

      <Card>
        <SectionTitle hint="Flat additional cancellation probability, e.g. ATC strike">
          Disruption {cancel === 0 ? '(none)' : `+${(cancel * 100).toFixed(0)}%`}
        </SectionTitle>
        <View style={s.wrap}>
          {CANCEL_STEPS.map((v) => (
            <Chip
              key={v}
              label={v === 0 ? 'none' : `+${(v * 100).toFixed(0)}%`}
              active={cancel === v}
              onPress={() => setCancel(v)}
            />
          ))}
        </View>
      </Card>

      <Card>
        <View style={s.switchRow}>
          <View style={{ flex: 1 }}>
            <Text style={s.switchLabel}>Exclude exogenous shocks</Text>
            <Text style={s.switchHint}>
              Removes the rare-disruption process. Produces a narrower planning
              case — useful, but not a forecast of what will happen.
            </Text>
          </View>
          <Switch
            value={noShocks}
            onValueChange={setNoShocks}
            trackColor={{ true: Colors.median, false: Colors.border }}
          />
        </View>
      </Card>

      <Pressable
        style={[s.cta, (!dirty || running) && s.ctaDisabled]}
        onPress={run}
        disabled={!dirty || running}
      >
        {running ? (
          <ActivityIndicator color={Colors.bg} />
        ) : (
          <>
            <Ionicons name="play" size={16} color={Colors.bg} />
            <Text style={s.ctaText}>Run scenario</Text>
          </>
        )}
      </Pressable>

      {error && <Text style={s.error}>{error}</Text>}

      {result && (
        <>
          <Card>
            <SectionTitle hint="Scenario overlaid on the baseline, same random seed">
              Effect on daily throughput
            </SectionTitle>
            <FanChart
              dates={result.baseline.dates}
              percentiles={result.baseline.percentiles}
              scenarioPercentiles={result.scenario.percentiles}
              height={240}
            />
          </Card>

          <Card>
            <SectionTitle>30-day total</SectionTitle>
            <View style={s.deltaRow}>
              <View style={s.deltaCol}>
                <Text style={s.deltaVal}>{formatPax(result.baseline.total_percentiles.p50)}</Text>
                <Text style={s.deltaKey}>baseline</Text>
              </View>
              <Ionicons name="arrow-forward" size={17} color={Colors.textFaint} />
              <View style={s.deltaCol}>
                <Text style={[s.deltaVal, { color: Colors.scenario }]}>
                  {formatPax(result.scenario.total_percentiles.p50)}
                </Text>
                <Text style={s.deltaKey}>scenario</Text>
              </View>
            </View>
            <Text
              style={[
                s.deltaHeadline,
                { color: result.delta.total_p50_percent < 0 ? Colors.danger : Colors.success },
              ]}
            >
              {result.delta.total_p50_percent > 0 ? '+' : ''}
              {result.delta.total_p50_percent.toFixed(1)}% ·{' '}
              {formatFull(Math.abs(result.delta.total_p50_absolute))} passengers{' '}
              {result.delta.total_p50_percent < 0 ? 'lost' : 'gained'}
            </Text>
          </Card>

          <Card>
            <SectionTitle hint="Under the scenario">Capacity risk shift</SectionTitle>
            <View style={s.deltaRow}>
              <View style={s.deltaCol}>
                <Text style={s.deltaVal}>
                  {(result.baseline.exceedance.p_exceed_daily_capacity * 100).toFixed(1)}%
                </Text>
                <Text style={s.deltaKey}>baseline exceedance</Text>
              </View>
              <Ionicons name="arrow-forward" size={17} color={Colors.textFaint} />
              <View style={s.deltaCol}>
                <Text style={[s.deltaVal, { color: Colors.scenario }]}>
                  {(result.scenario.exceedance.p_exceed_daily_capacity * 100).toFixed(1)}%
                </Text>
                <Text style={s.deltaKey}>scenario exceedance</Text>
              </View>
            </View>
          </Card>
        </>
      )}

      <View style={{ height: Spacing.xxl }} />
    </ScrollView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.bg },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.sm, marginTop: Spacing.sm },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.md },
  switchLabel: { ...Type.body, color: Colors.text, fontWeight: '600' },
  switchHint: { ...Type.caption, color: Colors.textFaint, marginTop: 3, lineHeight: 15 },
  cta: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: Spacing.sm,
    backgroundColor: Colors.median, borderRadius: Radius.md,
    paddingVertical: 13, marginBottom: Spacing.md,
  },
  ctaDisabled: { opacity: 0.35 },
  ctaText: { ...Type.heading, color: Colors.bg },
  error: { ...Type.caption, color: Colors.danger, marginBottom: Spacing.md },
  deltaRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: Spacing.lg, marginTop: Spacing.sm,
  },
  deltaCol: { alignItems: 'center' },
  deltaVal: { ...Type.title, color: Colors.text },
  deltaKey: { ...Type.caption, color: Colors.textFaint, marginTop: 2 },
  deltaHeadline: {
    ...Type.label, textAlign: 'center', marginTop: Spacing.lg,
  },
});
