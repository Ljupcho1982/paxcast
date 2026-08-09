/**
 * Report an observed wait.
 *
 * This screen is where the app stops being a simulation and starts being a
 * measurement instrument, so it is worth getting the feedback right.
 *
 * After submitting, the user is shown *how much their report moved the model* —
 * the shrinkage weight, the number of reports so far, and the before/after
 * median. Two reasons:
 *
 *   - It is honest. With three reports the fit barely moves, and saying so is
 *     better than implying a single tap rewrote the forecast.
 *   - It is motivating in the right direction. "17 reports, 68% observation-led"
 *     tells a user that more reports genuinely help, which a bare thank-you
 *     does not.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  Pressable,
} from 'react-native';
import { useLocalSearchParams, Stack } from 'expo-router';
import { C, F, T, S, ink, RADIUS } from '@/constants/modernist';
import { Btn, Kicker, SectionRule, Segmented } from '@/components/Modernist';
import { ApiError, contribute, type CheckpointRecord, type FitResult } from '@/lib/api';

const QUICK = [5, 10, 15, 20, 30, 45, 60, 90];

export default function ReportWaitScreen() {
  const { iata } = useLocalSearchParams<{ iata: string }>();
  const [checkpoints, setCheckpoints] = useState<CheckpointRecord[]>([]);
  const [selected, setSelected] = useState<CheckpointRecord | null>(null);
  const [minutes, setMinutes] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [fit, setFit] = useState<FitResult | null>(null);
  const [before, setBefore] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!iata) return;
    setLoading(true);
    try {
      const res = await contribute.listCheckpoints(iata);
      setCheckpoints(res.checkpoints);
      setSelected(res.checkpoints[0] ?? null);
    } catch {
      setError('Could not load checkpoints for this airport.');
    } finally {
      setLoading(false);
    }
  }, [iata]);

  useEffect(() => {
    load();
  }, [load]);

  async function submit() {
    if (!selected || minutes === null) return;
    setBusy(true);
    setError(null);
    setBefore(selected.base);
    try {
      const res = await contribute.reportWait(selected.id, minutes);
      setFit(res.fit);
      setCheckpoints((prev) =>
        prev.map((c) =>
          c.id === selected.id
            ? { ...c, base: res.fit.base, sig: res.fit.sig, fit_n: res.fit.n_observations }
            : c,
        ),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not submit the report.');
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <View style={s.center}>
        <ActivityIndicator color={C.accent} />
      </View>
    );
  }

  if (!checkpoints.length) {
    return (
      <View style={s.center}>
        <Stack.Screen options={{ title: 'Report a wait' }} />
        <Text style={s.emptyTitle}>No checkpoints yet</Text>
        <Text style={s.emptyBody}>
          {iata} has no security checkpoints recorded, so there is nothing to
          report against. Add one first.
        </Text>
      </View>
    );
  }

  /* ── Result state ─────────────────────────────────────────────────── */
  if (fit) {
    const moved = before !== null ? fit.base - before : 0;
    return (
      <ScrollView style={s.root} contentContainerStyle={{ padding: S.s4, paddingTop: 32 }}>
        <Stack.Screen options={{ title: 'Report recorded' }} />
        <View style={s.doneMark} />
        <Text style={s.doneTitle}>Recorded</Text>

        <Text style={s.doneBody}>
          {fit.note} Your report is one of {fit.n_observations} for this
          checkpoint.
        </Text>

        <View style={s.movePanel}>
          <Kicker>WHAT YOUR REPORT CHANGED</Kicker>
          <View style={s.moveRow}>
            <View style={s.moveCol}>
              <Text style={s.moveVal}>{before?.toFixed(1) ?? '—'}</Text>
              <Text style={s.moveKey}>before</Text>
            </View>
            <Text style={s.moveArrow}>→</Text>
            <View style={s.moveCol}>
              <Text style={[s.moveVal, { color: C.accent700 }]}>
                {fit.base.toFixed(1)}
              </Text>
              <Text style={s.moveKey}>after (min)</Text>
            </View>
          </View>
          <Text style={s.moveDelta}>
            {Math.abs(moved) < 0.05
              ? 'Essentially unchanged — with this many reports the lane-type prior still dominates.'
              : `Median moved ${moved > 0 ? 'up' : 'down'} ${Math.abs(moved).toFixed(1)} min.`}
          </Text>
        </View>

        <View style={s.weightPanel}>
          <Text style={s.weightLabel}>
            {Math.round(fit.shrinkage_mu * 100)}% of the current estimate comes from
            observations, {Math.round((1 - fit.shrinkage_mu) * 100)}% from the prior.
          </Text>
          <View style={s.weightTrack}>
            <View style={[s.weightFill, { width: `${fit.shrinkage_mu * 100}%` }]} />
          </View>
        </View>

        <View style={{ marginTop: 26 }}>
          <Btn
            label="Report another"
            block
            onPress={() => {
              setFit(null);
              setMinutes(null);
            }}
          />
        </View>
      </ScrollView>
    );
  }

  /* ── Entry state ──────────────────────────────────────────────────── */
  return (
    <ScrollView style={s.root} contentContainerStyle={{ paddingBottom: 40 }}>
      <Stack.Screen options={{ title: 'Report a wait' }} />

      <View style={s.block}>
        <Kicker>WHICH CHECKPOINT</Kicker>
        {checkpoints.map((cp) => (
          <Pressable
            key={cp.id}
            onPress={() => setSelected(cp)}
            style={[s.cpRow, selected?.id === cp.id && { backgroundColor: C.neutral200 }]}
          >
            <View style={{ flex: 1 }}>
              <Text style={T.rowName}>{cp.name}</Text>
              <Text style={s.cpZone}>
                {cp.zone || cp.lane_type} ·{' '}
                {cp.fit_n > 0
                  ? `${cp.fit_n} report${cp.fit_n === 1 ? '' : 's'}`
                  : 'no reports yet'}
              </Text>
            </View>
            <Text
              style={[
                T.rowValue,
                { color: selected?.id === cp.id ? C.accent700 : C.text },
              ]}
            >
              {cp.base.toFixed(0)}
            </Text>
            <Text style={s.cpUnit}>MIN</Text>
          </Pressable>
        ))}
      </View>
      <SectionRule />

      <View style={s.block}>
        <Kicker>HOW LONG DID YOU QUEUE</Kicker>
        <View style={s.quickGrid}>
          {QUICK.map((q) => (
            <Pressable
              key={q}
              onPress={() => setMinutes(q)}
              style={[s.quick, minutes === q && s.quickActive]}
            >
              <Text style={[s.quickText, minutes === q && s.quickTextActive]}>{q}</Text>
            </Pressable>
          ))}
        </View>
        <Text style={s.hint}>
          Time from joining the queue to clearing screening. Reports are weighted
          by age — recent ones count for more.
        </Text>
      </View>

      {error && (
        <View style={s.banner}>
          <Text style={s.bannerText}>{error}</Text>
        </View>
      )}

      <View style={s.block}>
        {busy ? (
          <View style={s.busyRow}>
            <ActivityIndicator color={C.accent} />
            <Text style={s.busyText}>Submitting…</Text>
          </View>
        ) : (
          <Btn
            label={minutes === null ? 'Pick a wait time' : `Report ${minutes} minutes`}
            block
            onPress={submit}
          />
        )}
      </View>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  center: {
    flex: 1,
    backgroundColor: C.bg,
    alignItems: 'center',
    justifyContent: 'center',
    padding: S.s6,
  },
  block: { paddingHorizontal: S.s4, paddingTop: 18, paddingBottom: 20 },
  hint: { fontFamily: F.body, fontSize: 12, color: ink(55), marginTop: 14, lineHeight: 17 },

  emptyTitle: { fontFamily: F.heading, fontSize: 22, color: C.text },
  emptyBody: {
    fontFamily: F.body,
    fontSize: 14,
    color: ink(65),
    textAlign: 'center',
    marginTop: 10,
    lineHeight: 20,
  },

  cpRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 10,
    paddingVertical: 14,
    borderTopWidth: 1,
    borderTopColor: C.divider,
    marginTop: 8,
  },
  cpZone: { fontFamily: F.body, fontSize: 12, color: ink(60), marginTop: 4 },
  cpUnit: { fontFamily: F.headingSemi, fontSize: 11, color: ink(55) },

  quickGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 12 },
  quick: {
    width: 62,
    paddingVertical: 12,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: C.divider,
    borderRadius: RADIUS,
  },
  quickActive: { backgroundColor: C.accent, borderColor: C.accent },
  quickText: { fontFamily: F.heading, fontSize: 16, color: C.text },
  quickTextActive: { color: '#fff' },

  busyRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 12 },
  busyText: { fontFamily: F.headingSemi, fontSize: 13, color: ink(60) },

  banner: {
    backgroundColor: C.accent200,
    borderTopWidth: 2,
    borderBottomWidth: 2,
    borderColor: C.accent,
    padding: S.s4,
  },
  bannerText: { fontFamily: F.body, fontSize: 13, color: C.accent800, lineHeight: 18 },

  doneMark: { width: 24, height: 24, backgroundColor: C.accent, marginBottom: 16 },
  doneTitle: { fontFamily: F.heading, fontSize: 34, letterSpacing: -1, color: C.text },
  doneBody: {
    fontFamily: F.body,
    fontSize: 14,
    lineHeight: 21,
    color: C.text,
    marginTop: 10,
  },

  movePanel: {
    marginTop: 26,
    paddingTop: 18,
    borderTopWidth: 2,
    borderTopColor: C.divider,
  },
  moveRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    justifyContent: 'center',
    gap: 20,
    marginTop: 14,
  },
  moveCol: { alignItems: 'center' },
  moveVal: { fontFamily: F.heading, fontSize: 40, letterSpacing: -1.6, color: C.text },
  moveKey: { fontFamily: F.body, fontSize: 11, color: ink(55), marginTop: 2 },
  moveArrow: { fontFamily: F.heading, fontSize: 20, color: ink(45), paddingBottom: 12 },
  moveDelta: {
    fontFamily: F.body,
    fontSize: 12,
    color: ink(60),
    marginTop: 14,
    textAlign: 'center',
    lineHeight: 17,
  },

  weightPanel: { marginTop: 24 },
  weightLabel: { fontFamily: F.body, fontSize: 13, color: C.text, lineHeight: 19 },
  weightTrack: { height: 10, backgroundColor: C.neutral300, marginTop: 10 },
  weightFill: { height: 10, backgroundColor: C.accent },
});
