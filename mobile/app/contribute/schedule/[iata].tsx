/**
 * Schedule import.
 *
 * The design principle here is that a partial success must look like a partial
 * success. An import of 40 rows where 6 were rejected is the normal case, not
 * an error case, and hiding the 6 behind a green tick means the user never
 * fixes them. So the result panel reports added, skipped and rejected
 * separately, and lists the offending line numbers with the server's reason.
 *
 * Skipped-as-duplicate is deliberately shown as neutral rather than as a
 * warning: re-importing an updated schedule and having most rows skip is the
 * intended behaviour of an idempotent import, not a mistake.
 */

import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  Pressable,
} from 'react-native';
import { useLocalSearchParams, useRouter, Stack } from 'expo-router';
import { C, F, S, ink, RADIUS } from '@/constants/modernist';
import { Btn, Kicker, SectionRule } from '@/components/Modernist';
import { ApiError, contribute } from '@/lib/api';

const TEMPLATE = `flight_no,carrier,carrier_type,other_endpoint,direction,seats,sched_time,dow
W64301,W6,LCC,VIE,DEP,230,06:35,1234567
W64302,W6,LCC,VIE,ARR,230,22:10,1234567
JU0170,JU,REGIONAL,BEG,DEP,76,17:05,"Mo,Tu,We,Th,Fr"`;

interface ImportResult {
  added: number;
  skipped_duplicates: number;
  rejected: number;
  errors: { line: number; error: string }[];
  total_flights: number;
  calibration_factor: number;
  warning?: string;
}

export default function ScheduleImportScreen() {
  const { iata } = useLocalSearchParams<{ iata: string }>();
  const router = useRouter();
  const [csv, setCsv] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (!iata || !csv.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await contribute.importFlightsCsv(iata, csv));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reach the server.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <ScrollView style={s.root} contentContainerStyle={{ paddingBottom: 40 }}>
      <Stack.Screen options={{ title: `${iata} schedule` }} />

      <View style={s.block}>
        <Kicker>PASTE SCHEDULE CSV</Kicker>
        <Text style={s.hint}>
          Header must include flight_no, carrier, direction and seats. sched_time
          accepts HH:MM or minutes past midnight. dow accepts a bitmask, digits
          (1=Mon), or day names — quote it if it contains commas.
        </Text>
        <TextInput
          style={s.textarea}
          value={csv}
          onChangeText={setCsv}
          placeholder={TEMPLATE}
          placeholderTextColor={ink(30)}
          multiline
          autoCapitalize="none"
          autoCorrect={false}
          textAlignVertical="top"
        />
        <Pressable onPress={() => setCsv(TEMPLATE)} style={s.templateBtn}>
          <Text style={s.templateText}>Insert template</Text>
        </Pressable>
      </View>

      <View style={s.block}>
        {busy ? (
          <View style={s.busyRow}>
            <ActivityIndicator color={C.accent} />
            <Text style={s.busyText}>Importing…</Text>
          </View>
        ) : (
          <Btn label="Import schedule" block onPress={run} />
        )}
      </View>

      {error && (
        <View style={s.banner}>
          <Text style={s.bannerText}>{error}</Text>
        </View>
      )}

      {result && (
        <>
          <SectionRule />
          <View style={s.block}>
            <Kicker>RESULT</Kicker>
            <View style={s.tally}>
              <Tally value={result.added} label="added" tone={C.text} />
              <Tally value={result.skipped_duplicates} label="already present" tone={ink(55)} />
              <Tally
                value={result.rejected}
                label="rejected"
                tone={result.rejected ? C.accent700 : ink(55)}
              />
            </View>
            <Text style={s.summary}>
              {result.total_flights} schedule rows now stored. Anchor factor{' '}
              {result.calibration_factor.toFixed(2)}
              {result.calibration_factor > 1.5
                ? ' — the schedule accounts for less traffic than the published annual total, so it is being scaled up.'
                : result.calibration_factor < 0.7
                  ? ' — the schedule implies more traffic than the published annual total.'
                  : '.'}
            </Text>

            {result.warning && (
              <View style={s.warn}>
                <Text style={s.warnText}>{result.warning}</Text>
              </View>
            )}

            {result.errors.length > 0 && (
              <View style={{ marginTop: 18 }}>
                <Kicker>REJECTED LINES</Kicker>
                {result.errors.map((e) => (
                  <View key={e.line} style={s.errRow}>
                    <Text style={s.errLine}>{e.line}</Text>
                    <Text style={s.errText}>{e.error}</Text>
                  </View>
                ))}
              </View>
            )}

            {result.total_flights > 0 && (
              <View style={{ marginTop: 22 }}>
                <Btn
                  label="View forecast"
                  block
                  onPress={() => router.replace(`/airport/${iata}`)}
                />
              </View>
            )}
          </View>
        </>
      )}
    </ScrollView>
  );
}

function Tally({ value, label, tone }: { value: number; label: string; tone: string }) {
  return (
    <View style={{ flex: 1 }}>
      <Text style={[s.tallyValue, { color: tone }]}>{value}</Text>
      <Text style={s.tallyLabel}>{label}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  block: { paddingHorizontal: S.s4, paddingTop: 18, paddingBottom: 20 },
  hint: { fontFamily: F.body, fontSize: 12, color: ink(55), marginTop: 8, lineHeight: 17 },
  textarea: {
    marginTop: 12,
    minHeight: 190,
    padding: 10,
    fontFamily: 'monospace',
    fontSize: 12,
    color: C.text,
    backgroundColor: C.surface,
    borderWidth: 1,
    borderColor: C.divider,
    borderRadius: RADIUS,
  },
  templateBtn: { paddingVertical: 10 },
  templateText: { fontFamily: F.headingSemi, fontSize: 12, color: C.accent },

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

  tally: { flexDirection: 'row', gap: 12, marginTop: 14 },
  tallyValue: { fontFamily: F.heading, fontSize: 34, letterSpacing: -1 },
  tallyLabel: { fontFamily: F.body, fontSize: 11, color: ink(55), marginTop: 2 },
  summary: { fontFamily: F.body, fontSize: 13, color: C.text, marginTop: 16, lineHeight: 19 },

  warn: {
    marginTop: 14,
    padding: 12,
    backgroundColor: C.accent100,
    borderLeftWidth: 3,
    borderLeftColor: C.accent,
  },
  warnText: { fontFamily: F.body, fontSize: 12, color: C.accent800, lineHeight: 17 },

  errRow: { flexDirection: 'row', gap: 10, marginTop: 10 },
  errLine: {
    fontFamily: F.heading,
    fontSize: 12,
    color: C.accent700,
    minWidth: 26,
  },
  errText: { flex: 1, fontFamily: F.body, fontSize: 12, color: ink(70), lineHeight: 17 },
});
