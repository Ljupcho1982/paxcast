/**
 * First-run limitations disclosure.
 *
 * BUILD.md's pre-flight checklist requires a prototype warning visible on first
 * run, and until now the only disclaimer lived on the airport detail screen and
 * described *model* uncertainty ("a distribution, not a prediction"). That is a
 * different claim: it says the band might be wide, not that the underlying
 * schedule is invented and the checkpoint waits are unvalidated priors.
 *
 * The distinction matters because the failure mode is not "the forecast was a
 * bit off" -- it is a traveller trusting a fabricated security wait to decide
 * when to leave for the airport, and missing a flight. So this blocks the UI
 * once rather than sitting passively in a corner of one screen.
 *
 * Acknowledgement is versioned. Bumping KEY re-shows the notice, which is what
 * you want if the limitations change materially rather than silently relying on
 * a tick someone made against different wording.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { useEffect, useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Colors, Radius, Spacing, Type } from '@/constants/theme';

const KEY = 'paxcast:disclosure:v1';

export function FirstRunNotice() {
  // `null` means "not yet known". Rendering nothing in that state avoids the
  // notice flashing up for a frame on every launch after the first.
  const [accepted, setAccepted] = useState<boolean | null>(null);

  useEffect(() => {
    AsyncStorage.getItem(KEY)
      .then((v) => setAccepted(v === '1'))
      // A storage failure must not silently suppress the warning; show it.
      .catch(() => setAccepted(false));
  }, []);

  if (accepted === null || accepted) return null;

  const accept = () => {
    setAccepted(true);
    AsyncStorage.setItem(KEY, '1').catch(() => {});
  };

  return (
    <Modal visible transparent={false} animationType="fade" statusBarTranslucent>
      <View style={s.root}>
        <ScrollView contentContainerStyle={s.scroll}>
          <Text style={s.kicker}>Before you start</Text>
          <Text style={s.title}>This is a prototype</Text>

          <View style={s.item}>
            <Text style={s.itemTitle}>Flight schedules are synthetic</Text>
            <Text style={s.body}>
              Forecasts run on a synthesised schedule. The structure is realistic
              — carrier mix, banking and frequency are anchored to published
              annual totals — but the individual flights are invented. No
              licensed schedule feed is used.
            </Text>
          </View>

          <View style={s.item}>
            <Text style={s.itemTitle}>Security wait times are unvalidated</Text>
            <Text style={s.body}>
              A new checkpoint's numbers come from an engineering estimate, not
              from measurement. They converge on reality only after roughly 100
              reported waits.
            </Text>
          </View>

          <View style={s.warn}>
            <Text style={s.warnText}>
              Do not use this app to decide when to leave for a flight.
            </Text>
          </View>

          <Text style={s.footnote}>
            PaxCast reports a distribution, not a prediction. Bands describe
            modelled uncertainty and cannot account for events absent from the
            model.
          </Text>
        </ScrollView>

        <Pressable style={s.button} onPress={accept} accessibilityRole="button">
          <Text style={s.buttonText}>I understand</Text>
        </Pressable>
      </View>
    </Modal>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.bg, paddingHorizontal: Spacing.xl },
  scroll: { paddingTop: Spacing.xxl * 2, paddingBottom: Spacing.xl },
  kicker: {
    ...Type.caption,
    color: Colors.textFaint,
    textTransform: 'uppercase',
    marginBottom: Spacing.sm,
  },
  title: { ...Type.display, color: Colors.text, marginBottom: Spacing.xl },
  item: { marginBottom: Spacing.xl },
  itemTitle: { ...Type.heading, color: Colors.text, marginBottom: Spacing.xs },
  body: { ...Type.body, color: Colors.textMuted, lineHeight: 21 },
  warn: {
    borderLeftWidth: 3,
    borderLeftColor: Colors.danger,
    backgroundColor: Colors.surface,
    borderRadius: Radius.sm,
    padding: Spacing.lg,
    marginBottom: Spacing.xl,
  },
  warnText: { ...Type.heading, color: Colors.text },
  footnote: { ...Type.label, color: Colors.textFaint, lineHeight: 19 },
  button: {
    backgroundColor: Colors.median,
    borderRadius: Radius.md,
    paddingVertical: Spacing.lg,
    alignItems: 'center',
    marginBottom: Spacing.xxl,
  },
  buttonText: { ...Type.heading, color: Colors.bg },
});
