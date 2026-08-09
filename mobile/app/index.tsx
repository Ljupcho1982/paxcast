/** Airport search and watchlist. */
import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, TextInput, FlatList,
  Pressable, ActivityIndicator, RefreshControl,
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { api, watchlist, formatPax, type AirportSummary } from '@/lib/api';
import { Colors, Spacing, Radius, Type } from '@/constants/theme';

export default function SearchScreen() {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<AirportSummary[]>([]);
  const [saved, setSaved] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (q: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.airports(q || undefined);
      setItems(res.airports);
    } catch (e) {
      setError('Could not reach the forecast service. Showing cached results if available.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    watchlist.get().then(setSaved);
  }, []);

  useEffect(() => {
    const t = setTimeout(() => load(query), 250);
    return () => clearTimeout(t);
  }, [query, load]);

  const onToggle = async (iata: string) => setSaved(await watchlist.toggle(iata));

  const ordered = [...items].sort((a, b) => {
    const aw = saved.includes(a.iata) ? 0 : 1;
    const bw = saved.includes(b.iata) ? 0 : 1;
    return aw - bw || b.annual_pax - a.annual_pax;
  });

  return (
    <SafeAreaView style={s.root} edges={['bottom']}>
      <View style={s.searchWrap}>
        <Ionicons name="search" size={17} color={Colors.textFaint} />
        <TextInput
          style={s.search}
          placeholder="Search airport, city or country"
          placeholderTextColor={Colors.textFaint}
          value={query}
          onChangeText={setQuery}
          autoCorrect={false}
          autoCapitalize="characters"
        />
        {query.length > 0 && (
          <Pressable onPress={() => setQuery('')} hitSlop={8}>
            <Ionicons name="close-circle" size={17} color={Colors.textFaint} />
          </Pressable>
        )}
      </View>

      {/* Offered inline rather than buried in a menu: the moment a search comes
          back empty is exactly when someone wants to add the missing airport. */}
      <Pressable style={s.addRow} onPress={() => router.push('/contribute/airport')}>
        <Ionicons name="add-circle-outline" size={18} color={Colors.median} />
        <Text style={s.addText}>
          {items.length === 0 && query.length > 0
            ? `Add "${query.toUpperCase()}" as a new airport`
            : 'Add an airport'}
        </Text>
      </Pressable>

      {error && <Text style={s.error}>{error}</Text>}

      {loading && items.length === 0 ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={Colors.median} />
      ) : (
        <FlatList
          data={ordered}
          keyExtractor={(a) => a.iata}
          contentContainerStyle={{ padding: Spacing.lg, paddingTop: 0 }}
          refreshControl={
            <RefreshControl refreshing={loading} onRefresh={() => load(query)} tintColor={Colors.median} />
          }
          ListEmptyComponent={<Text style={s.empty}>No airports matched.</Text>}
          renderItem={({ item }) => (
            <Pressable style={s.row} onPress={() => router.push(`/airport/${item.iata}`)}>
              <View style={s.code}>
                <Text style={s.codeText}>{item.iata}</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={s.name} numberOfLines={1}>{item.name}</Text>
                <Text style={s.meta}>
                  {item.city}, {item.country} · {formatPax(item.annual_pax)} pax/yr · {item.daily_movements} mov/day
                </Text>
              </View>
              <Pressable onPress={() => onToggle(item.iata)} hitSlop={10}>
                <Ionicons
                  name={saved.includes(item.iata) ? 'bookmark' : 'bookmark-outline'}
                  size={19}
                  color={saved.includes(item.iata) ? Colors.median : Colors.textFaint}
                />
              </Pressable>
            </Pressable>
          )}
        />
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.bg },
  searchWrap: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.sm,
    margin: Spacing.lg, paddingHorizontal: Spacing.md,
    backgroundColor: Colors.surface, borderRadius: Radius.md,
    borderWidth: 1, borderColor: Colors.border,
  },
  search: { flex: 1, color: Colors.text, paddingVertical: 11, ...Type.body },
  row: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.md,
    paddingVertical: Spacing.md, borderBottomWidth: 1, borderBottomColor: Colors.border,
  },
  code: {
    width: 46, height: 34, borderRadius: Radius.sm,
    backgroundColor: Colors.surfaceAlt, alignItems: 'center', justifyContent: 'center',
  },
  codeText: { ...Type.mono, color: Colors.median, fontSize: 13 },
  name: { ...Type.body, color: Colors.text, fontWeight: '600' },
  meta: { ...Type.caption, color: Colors.textFaint, marginTop: 2 },
  empty: { ...Type.body, color: Colors.textFaint, textAlign: 'center', marginTop: 40 },
  error: {
    ...Type.caption, color: Colors.warning,
    paddingHorizontal: Spacing.lg, paddingBottom: Spacing.sm,
  },
  addRow: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.sm,
    paddingHorizontal: Spacing.lg, paddingBottom: Spacing.md,
  },
  addText: { ...Type.label, color: Colors.median },
});
