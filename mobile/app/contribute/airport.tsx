/**
 * Add an airport.
 *
 * Two decisions worth stating:
 *
 * 1. **The form does not pretend the airport is usable once saved.** An
 *    airport with no schedule cannot be forecast, and the success state says so
 *    and routes straight to schedule import rather than dropping the user back
 *    into a list where the new entry silently fails.
 *
 * 2. **Server validation is surfaced per field, not as a toast.** The API
 *    returns which field failed; throwing that away and showing "something went
 *    wrong" would waste the specificity the backend went to the trouble of
 *    producing.
 */

import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  ScrollView,
  StyleSheet,
  Pressable,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { useRouter, Stack } from 'expo-router';
import { C, F, T, S, ink, RADIUS } from '@/constants/modernist';
import { Btn, Kicker, SectionRule, Segmented } from '@/components/Modernist';
import { ApiError, contribute, type AirportInput } from '@/lib/api';

type Climate = 'mild' | 'temperate' | 'harsh_winter' | 'monsoon';

const CLIMATES: { key: Climate; label: string }[] = [
  { key: 'mild', label: 'MILD' },
  { key: 'temperate', label: 'TEMP' },
  { key: 'harsh_winter', label: 'WINTER' },
  { key: 'monsoon', label: 'MONSOON' },
];

interface FieldState {
  iata: string;
  icao: string;
  name: string;
  city: string;
  country: string;
  lat: string;
  lon: string;
  capacity: string;
  annual: string;
}

const EMPTY: FieldState = {
  iata: '',
  icao: '',
  name: '',
  city: '',
  country: '',
  lat: '',
  lon: '',
  capacity: '3000',
  annual: '',
};

export default function AddAirportScreen() {
  const router = useRouter();
  const [f, setF] = useState<FieldState>(EMPTY);
  const [climate, setClimate] = useState<Climate>('temperate');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<{ iata: string; quality: number } | null>(null);

  const set = (k: keyof FieldState) => (v: string) => {
    setF((prev) => ({ ...prev, [k]: v }));
    if (errors[k]) setErrors((e) => ({ ...e, [k]: '' }));
  };

  /** Client-side checks mirror the server's, to save a round trip — the server
   *  remains the authority and its message wins if they ever disagree. */
  function validate(): boolean {
    const e: Record<string, string> = {};
    if (!/^[A-Za-z]{3}$/.test(f.iata.trim())) e.iata = 'Three letters, e.g. SKP';
    if (f.icao.trim() && !/^[A-Za-z]{4}$/.test(f.icao.trim()))
      e.icao = 'Four letters, e.g. LWSK';
    if (f.name.trim().length < 2) e.name = 'Required';

    const lat = Number(f.lat);
    const lon = Number(f.lon);
    if (!f.lat.trim() || Number.isNaN(lat) || lat < -90 || lat > 90)
      e.lat = 'Between -90 and 90';
    if (!f.lon.trim() || Number.isNaN(lon) || lon < -180 || lon > 180)
      e.lon = 'Between -180 and 180';
    if (!e.lat && !e.lon && Math.abs(lat) < 0.01 && Math.abs(lon) < 0.01)
      e.lat = 'That is the Gulf of Guinea — check the coordinates';

    const cap = Number(f.capacity);
    if (!Number.isFinite(cap) || cap <= 0) e.capacity = 'Must be above zero';

    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function submit() {
    setBanner(null);
    if (!validate()) return;
    setSaving(true);
    const payload: AirportInput = {
      iata: f.iata.trim().toUpperCase(),
      icao: f.icao.trim().toUpperCase(),
      name: f.name.trim(),
      city: f.city.trim(),
      country: f.country.trim(),
      lat: Number(f.lat),
      lon: Number(f.lon),
      climate,
      terminal_capacity_hourly: Number(f.capacity),
      annual_pax_baseline: f.annual.trim() ? Number(f.annual) : null,
    };
    try {
      const res = await contribute.createAirport(payload);
      setSaved({ iata: res.iata, quality: res.data_quality });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.field && err.field in EMPTY) {
          setErrors({ [err.field]: err.message });
        } else {
          setBanner(err.message);
        }
      } else {
        setBanner('Could not reach the server. Nothing was saved.');
      }
    } finally {
      setSaving(false);
    }
  }

  if (saved) {
    return (
      <View style={s.root}>
        <Stack.Screen options={{ title: 'Airport added' }} />
        <View style={s.doneWrap}>
          <View style={s.doneMark} />
          <Text style={s.doneCode}>{saved.iata}</Text>
          <Text style={s.doneBody}>
            Saved. It cannot be forecast yet — an airport needs a flight schedule
            before the model has anything to simulate. Data quality is currently{' '}
            {Math.round(saved.quality * 100)}% and rises as you load schedule rows.
          </Text>
          <Btn
            label="Import a schedule"
            block
            onPress={() => router.replace(`/contribute/schedule/${saved.iata}`)}
          />
          <Btn
            label="Later"
            variant="secondary"
            block
            onPress={() => router.replace('/')}
          />
        </View>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={s.root}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <Stack.Screen options={{ title: 'Add airport' }} />
      <ScrollView contentContainerStyle={{ paddingBottom: 40 }}>
        {banner && (
          <View style={s.banner}>
            <Text style={s.bannerText}>{banner}</Text>
          </View>
        )}

        <View style={s.block}>
          <Kicker>IDENTITY</Kicker>
          <Row>
            <Field
              label="IATA"
              value={f.iata}
              onChange={set('iata')}
              error={errors.iata}
              placeholder="SKP"
              autoCapitalize="characters"
              maxLength={3}
              flex={1}
            />
            <Field
              label="ICAO"
              value={f.icao}
              onChange={set('icao')}
              error={errors.icao}
              placeholder="LWSK"
              autoCapitalize="characters"
              maxLength={4}
              flex={1}
            />
          </Row>
          <Field
            label="Name"
            value={f.name}
            onChange={set('name')}
            error={errors.name}
            placeholder="Skopje International"
          />
          <Row>
            <Field label="City" value={f.city} onChange={set('city')} flex={1} />
            <Field label="Country" value={f.country} onChange={set('country')} flex={1} />
          </Row>
        </View>
        <SectionRule />

        <View style={s.block}>
          <Kicker>POSITION</Kicker>
          <Row>
            <Field
              label="Latitude"
              value={f.lat}
              onChange={set('lat')}
              error={errors.lat}
              placeholder="41.9616"
              keyboardType="numbers-and-punctuation"
              flex={1}
            />
            <Field
              label="Longitude"
              value={f.lon}
              onChange={set('lon')}
              error={errors.lon}
              placeholder="21.6214"
              keyboardType="numbers-and-punctuation"
              flex={1}
            />
          </Row>
          <Text style={s.hint}>
            Used to reject duplicates: an airport within about 5 km of an existing
            one will be refused.
          </Text>
        </View>
        <SectionRule />

        <View style={s.block}>
          <Kicker>CLIMATE</Kicker>
          <Text style={s.hint}>
            Drives the weather chain that governs cancellation risk. Pick the one
            that matches the disruption pattern, not the average temperature.
          </Text>
          <View style={{ marginTop: 10 }}>
            <Segmented
              items={CLIMATES.map((c) => ({ key: c.key, label: c.label }))}
              activeKey={climate}
              onSelect={(k) => setClimate(k as Climate)}
              fill
              fontSize={11}
              letterSpacing={0.88}
              paddingVertical={10}
              paddingLeft={10}
            />
          </View>
        </View>
        <SectionRule />

        <View style={s.block}>
          <Kicker>CAPACITY AND SCALE</Kicker>
          <Row>
            <Field
              label="Terminal capacity (pax/hour)"
              value={f.capacity}
              onChange={set('capacity')}
              error={errors.capacity}
              keyboardType="number-pad"
              flex={1}
            />
          </Row>
          <Field
            label="Published annual passengers (optional)"
            value={f.annual}
            onChange={set('annual')}
            placeholder="2600000"
            keyboardType="number-pad"
          />
          <Text style={s.hint}>
            If you supply this, the model anchors the schedule to it — which is
            what lets a partial schedule still produce sensible totals. Leave it
            blank rather than guessing.
          </Text>
        </View>

        <View style={s.actions}>
          {saving ? (
            <View style={s.savingRow}>
              <ActivityIndicator color={C.accent} />
              <Text style={s.savingText}>Saving…</Text>
            </View>
          ) : (
            <Btn label="Add airport" block onPress={submit} />
          )}
          <Pressable onPress={() => router.back()} style={s.cancel}>
            <Text style={s.cancelText}>Cancel</Text>
          </Pressable>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

/* ── Form primitives ─────────────────────────────────────────────────── */

function Row({ children }: { children: React.ReactNode }) {
  return <View style={s.row}>{children}</View>;
}

function Field({
  label,
  value,
  onChange,
  error,
  placeholder,
  keyboardType,
  autoCapitalize,
  maxLength,
  flex,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  error?: string;
  placeholder?: string;
  keyboardType?: 'default' | 'number-pad' | 'numbers-and-punctuation';
  autoCapitalize?: 'none' | 'characters' | 'words';
  maxLength?: number;
  flex?: number;
}) {
  return (
    <View style={[{ marginTop: 12 }, flex ? { flex } : null]}>
      <Text style={s.label}>{label}</Text>
      <TextInput
        style={[s.input, !!error && s.inputError]}
        value={value}
        onChangeText={onChange}
        placeholder={placeholder}
        placeholderTextColor={ink(35)}
        keyboardType={keyboardType ?? 'default'}
        autoCapitalize={autoCapitalize ?? 'sentences'}
        autoCorrect={false}
        maxLength={maxLength}
      />
      {!!error && <Text style={s.error}>{error}</Text>}
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  block: { paddingHorizontal: S.s4, paddingTop: 18, paddingBottom: 20 },
  row: { flexDirection: 'row', gap: 10 },

  label: { fontFamily: F.body, fontSize: 12, color: ink(70), marginBottom: 5 },
  input: {
    minHeight: 40,
    paddingHorizontal: 10,
    paddingVertical: 8,
    fontFamily: F.body,
    fontSize: 14,
    color: C.text,
    backgroundColor: C.surface,
    borderWidth: 1,
    borderColor: C.divider,
    borderRadius: RADIUS,
  },
  inputError: { borderColor: C.accent, borderWidth: 2 },
  error: { fontFamily: F.headingSemi, fontSize: 11, color: C.accent700, marginTop: 4 },
  hint: { fontFamily: F.body, fontSize: 12, color: ink(55), marginTop: 10, lineHeight: 17 },

  banner: {
    backgroundColor: C.accent200,
    borderBottomWidth: 2,
    borderBottomColor: C.accent,
    padding: S.s4,
  },
  bannerText: { fontFamily: F.body, fontSize: 13, color: C.accent800, lineHeight: 18 },

  actions: { paddingHorizontal: S.s4, paddingTop: 24 },
  savingRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 12 },
  savingText: { fontFamily: F.headingSemi, fontSize: 13, color: ink(60) },
  cancel: { paddingVertical: 14, alignItems: 'center' },
  cancelText: { fontFamily: F.body, fontSize: 13, color: ink(55) },

  doneWrap: { padding: S.s4, paddingTop: 40 },
  doneMark: { width: 24, height: 24, backgroundColor: C.accent, marginBottom: 18 },
  doneCode: { fontFamily: F.heading, fontSize: 56, letterSpacing: -2, color: C.text },
  doneBody: {
    fontFamily: F.body,
    fontSize: 14,
    lineHeight: 21,
    color: C.text,
    marginTop: 12,
    marginBottom: 24,
  },
});
