/**
 * PaxCast API client.
 *
 * Every response is written to AsyncStorage so watchlisted airports remain
 * readable offline -- an airport duty manager on a ramp with no signal is a
 * core user, not an edge case. Cached responses are returned immediately and
 * flagged `stale` so the UI can say so rather than silently showing old bands.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import Constants from 'expo-constants';

const BASE =
  // Build-time override first: EAS profiles set EXPO_PUBLIC_API_BASE_URL, which
  // is inlined at bundle time. Without this, a release APK would ship pointing
  // at 10.0.2.2 -- the emulator's alias for the host loopback -- and would fail
  // on every real device with an opaque network error.
  process.env.EXPO_PUBLIC_API_BASE_URL ??
  (Constants.expoConfig?.extra as { apiBaseUrl?: string })?.apiBaseUrl ??
  'http://10.0.2.2:8000';

const CACHE_PREFIX = 'paxcast:cache:';
const CACHE_TTL_MS = 6 * 60 * 60 * 1000; // 6 hours

export type Percentiles = Record<'p5' | 'p10' | 'p25' | 'p50' | 'p75' | 'p90' | 'p95', number[]>;

export interface AirportSummary {
  iata: string;
  icao: string;
  name: string;
  city: string;
  country: string;
  lat: number;
  lon: number;
  daily_movements: number;
  annual_pax: number;
  data_quality: number;
}

export interface Forecast {
  iata: string;
  scenario: string;
  dates: string[];
  percentiles: Percentiles;
  mean: number[];
  total_percentiles: Record<string, number>;
  peak_hour_grid: number[][];
  exceedance: {
    daily_capacity_pax: number;
    p_exceed_daily_capacity: number;
    peak_hour_median_pax: number;
    peak_hour_capacity: number;
    peak_hour_utilisation: number;
  };
  n_iterations: number;
  converged: boolean;
  p90_rel_se: number;
  runtime_ms: number;
  data_quality: number;
  confidence: 'HIGH' | 'MEDIUM' | 'LOW';
  airport?: {
    iata: string;
    name: string;
    city: string;
    country: string;
    lat: number;
    lon: number;
    annual_pax_baseline: number;
    terminal_capacity_hourly: number;
    daily_flights: number;
  };
  stale?: boolean;
}

export interface ScenarioInput {
  name: string;
  load_factor_delta?: number;
  capacity_multiplier?: number;
  demand_multiplier?: number;
  grounded_carriers?: string[];
  closed_routes?: string[];
  extra_cancel_prob?: number;
  disable_shocks?: boolean;
}

export interface Preset {
  id: string;
  label: string;
  description: string;
  scenario: ScenarioInput;
}

async function readCache<T>(key: string): Promise<{ value: T; age: number } | null> {
  try {
    const raw = await AsyncStorage.getItem(CACHE_PREFIX + key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { t: number; v: T };
    return { value: parsed.v, age: Date.now() - parsed.t };
  } catch {
    return null;
  }
}

async function writeCache(key: string, value: unknown): Promise<void> {
  try {
    await AsyncStorage.setItem(CACHE_PREFIX + key, JSON.stringify({ t: Date.now(), v: value }));
  } catch {
    // A full cache must never break a forecast; fail silently.
  }
}

async function request<T>(path: string, init?: RequestInit, cacheKey?: string): Promise<T> {
  const key = cacheKey ?? path;
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    const res = await fetch(BASE + path, {
      ...init,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
    clearTimeout(timer);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const data = (await res.json()) as T;
    await writeCache(key, data);
    return data;
  } catch (err) {
    const cached = await readCache<T>(key);
    if (cached) {
      if (cached.age > CACHE_TTL_MS) {
        return { ...(cached.value as object), stale: true } as T;
      }
      return cached.value;
    }
    throw err;
  }
}

export const api = {
  health: () => request<{ status: string; airports: number }>('/health'),

  airports: (q?: string) =>
    request<{ count: number; airports: AirportSummary[] }>(
      `/airports${q ? `?q=${encodeURIComponent(q)}` : ''}`,
      undefined,
      `airports:${q ?? 'all'}`,
    ),

  forecast: (iata: string, horizon = 30, iterations = 20000) =>
    request<Forecast>(
      `/forecast/${iata}?horizon=${horizon}&iterations=${iterations}`,
      undefined,
      `forecast:${iata}:${horizon}`,
    ),

  scenario: (iata: string, horizon: number, scenario: ScenarioInput, iterations = 8000) =>
    request<{ baseline: Forecast; scenario: Forecast; delta: { total_p50_absolute: number; total_p50_percent: number } }>(
      '/compare',
      {
        method: 'POST',
        body: JSON.stringify({ iata, horizon_days: horizon, iterations, scenario }),
      },
      `compare:${iata}:${horizon}:${JSON.stringify(scenario)}`,
    ),

  presets: () => request<{ presets: Preset[] }>('/presets'),

  validation: (iata: string) =>
    request<{
      iata: string;
      coverage: Record<string, number>;
      pit_ks_pvalue: number;
      skill_vs_point_forecast: number;
      verdict: string;
    }>(`/validate/${iata}`, undefined, `validate:${iata}`),
};

/** Watchlist persistence. */
export const watchlist = {
  async get(): Promise<string[]> {
    try {
      const raw = await AsyncStorage.getItem('paxcast:watchlist');
      return raw ? (JSON.parse(raw) as string[]) : ['SKP', 'VIE'];
    } catch {
      return ['SKP'];
    }
  },
  async toggle(iata: string): Promise<string[]> {
    const list = await watchlist.get();
    const next = list.includes(iata) ? list.filter((x) => x !== iata) : [...list, iata];
    await AsyncStorage.setItem('paxcast:watchlist', JSON.stringify(next));
    return next;
  },
};

export function formatPax(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1000).toFixed(1)}k`;
  return Math.round(n).toString();
}

export function formatFull(n: number): string {
  return Math.round(n).toLocaleString('en-US');
}

/* ────────────────────────────────────────────────────────────────────────
   Data contribution
   ────────────────────────────────────────────────────────────────────────
   Writes are never served from cache and never silently retried. A user who
   submits an airport on a flaky connection must be told it failed, because the
   alternative -- an optimistic success followed by a missing record -- is the
   fastest way to lose their trust in everything else the app says.
   ──────────────────────────────────────────────────────────────────────── */

export interface AirportInput {
  iata: string;
  icao?: string;
  name: string;
  city?: string;
  country?: string;
  lat: number;
  lon: number;
  climate?: 'mild' | 'temperate' | 'harsh_winter' | 'monsoon';
  timezone?: string;
  terminal_capacity_hourly?: number;
  annual_pax_baseline?: number | null;
}

export interface CheckpointInput {
  name: string;
  zone?: string;
  lane_type?: 'standard' | 'expedited' | 'premium';
  lanes?: number;
}

export interface CheckpointRecord {
  id: number;
  name: string;
  zone: string;
  lane_type: string;
  base: number;
  sig: number;
  prior_base: number;
  prior_sig: number;
  fit_n: number;
  fitted: boolean;
}

export interface FitResult {
  base: number;
  sig: number;
  n_observations: number;
  shrinkage_mu: number;
  shrinkage_sig: number;
  prior_base: number;
  confidence: 'HIGH' | 'MEDIUM' | 'LOW';
  note: string;
}

export interface LanePlan {
  checkpoint_id: number;
  checkpoint: string;
  airport: string;
  hours: number[];
  hourly_pax: number[];
  /** Minimum lanes per hour to hold the target. */
  required: number[];
  /** Smoothed, implementable profile — always >= required. */
  planned: number[];
  lane_hours: number;
  peak_hour: number;
  physical_lanes: number;
  target_wait_min: number;
  service_level: number;
  demand_percentile: number;
  day_of_week: number;
  /** Hours where every lane is open and the target is still missed. */
  understaffed_hours: number[];
  /** 'prior' means no reported waits exist yet and the queue model is unvalidated. */
  basis: 'prior' | 'fitted';
  fit_n: number;
  confidence: 'HIGH' | 'MEDIUM' | 'LOW';
  caveat?: string;
  caveat_capacity?: string;
}

export async function fetchLanePlan(
  checkpointId: number,
  opts: { targetWait?: number; serviceLevel?: number; dayOfWeek?: number } = {},
): Promise<LanePlan> {
  const q = new URLSearchParams({
    target_wait: String(opts.targetWait ?? 15),
    service_level: String(opts.serviceLevel ?? 0.8),
    day_of_week: String(opts.dayOfWeek ?? 0),
    demand_percentile: '90',
  });
  return request<LanePlan>(
    `/checkpoints/${checkpointId}/lane-plan?${q}`,
    undefined,
    `laneplan:${checkpointId}:${q}`,
  );
}

/** Surfaces the server's specific validation message instead of a generic failure. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly field?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function write<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    let field: string | undefined;
    try {
      const detail = (await res.json()).detail;
      if (typeof detail === 'string') {
        message = detail;
      } else if (Array.isArray(detail) && detail.length) {
        // FastAPI validation errors arrive as a list of {loc, msg}. Pull the
        // field name out so the form can highlight the offending input rather
        // than showing a wall of text.
        const first = detail[0];
        field = String(first.loc?.[first.loc.length - 1] ?? '');
        message = String(first.msg ?? message).replace(/^Value error,\s*/, '');
      }
    } catch {
      /* keep the status-code message */
    }
    throw new ApiError(message, res.status, field);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const contribute = {
  createAirport: (input: AirportInput) =>
    write<{ iata: string; data_quality: number; forecastable: boolean }>(
      '/airports',
      'POST',
      input,
    ),

  importFlightsCsv: (iata: string, csvText: string) =>
    write<{
      added: number;
      skipped_duplicates: number;
      rejected: number;
      errors: { line: number; error: string }[];
      total_flights: number;
      calibration_factor: number;
      warning?: string;
    }>(`/airports/${iata}/flights/csv`, 'POST', { csv: csvText }),

  addCheckpoint: (iata: string, input: CheckpointInput) =>
    write<{ id: number; prior_base: number; prior_sig: number; prior_source: string }>(
      `/airports/${iata}/checkpoints`,
      'POST',
      input,
    ),

  listCheckpoints: (iata: string) =>
    write<{ iata: string; checkpoints: CheckpointRecord[] }>(
      `/airports/${iata}/checkpoints`,
      'GET',
    ),

  reportWait: (checkpointId: number, waitMinutes: number, source = 'user') =>
    write<{ checkpoint_id: number; fit: FitResult }>(
      `/checkpoints/${checkpointId}/observations`,
      'POST',
      { wait_minutes: waitMinutes, source },
    ),
};
