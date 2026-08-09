/**
 * Security checkpoint wait-time model.
 *
 * Ported faithfully from the Claude Design prototype's `DCLogic` block in
 * PaxCast.dc.html. Wait time at a checkpoint is lognormal:
 *
 *     log W ~ Normal(mu, sigma),   mu = log(base x peak(hour))
 *
 * where `base` is the checkpoint's median wait at an unloaded hour and `sigma`
 * is its dispersion. Lognormal is the right family here: waits are strictly
 * positive, right-skewed, and occasionally catastrophic -- exactly the shape a
 * point forecast destroys.
 *
 * NOTE ON PROVENANCE: the parameters below are the prototype's illustrative
 * values, not calibrated ones. The `peak()` curve is a sum of three Gaussians
 * placed at the morning bank (07:00), the evening bank (17:30), and a shallow
 * midday bump. Replacing these with values derived from the PaxCast throughput
 * engine's hourly load grid is the obvious next step -- see notes in the
 * conversation.
 */

export interface Checkpoint {
  id: number;
  name: string;
  zone: string;
  /** Median wait in minutes at an unloaded hour. */
  base: number;
  /** Lognormal dispersion. Higher = a longer, nastier right tail. */
  sig: number;
}

export const CHECKPOINTS: Checkpoint[] = [
  { id: 0, name: 'Checkpoint 1', zone: 'Terminal A · standard lane', base: 13, sig: 0.52 },
  { id: 1, name: 'Checkpoint 2', zone: 'Terminal A · expedited lane', base: 6, sig: 0.38 },
  { id: 2, name: 'Checkpoint 3', zone: 'Main hall · standard lane', base: 18, sig: 0.56 },
  { id: 3, name: 'Checkpoint 4', zone: 'Terminal C · standard lane', base: 11, sig: 0.62 },
  { id: 4, name: 'Checkpoint 5', zone: 'North satellite · expedited', base: 5, sig: 0.34 },
];

export const HOURS = [5, 6, 7, 8, 9, 10, 11, 12, 14, 16, 17, 18, 19, 20];

export const BUFFERS = [30, 45, 60, 75];

/** Load multiplier by hour of day: morning bank, evening bank, midday bump. */
export function peak(h: number): number {
  return (
    1 +
    1.15 * Math.exp(-Math.pow(h - 7, 2) / 3.2) +
    0.65 * Math.exp(-Math.pow(h - 17.5, 2) / 4.5) +
    0.25 * Math.exp(-Math.pow(h - 11.5, 2) / 6)
  );
}

/**
 * Inverse standard normal CDF (Acklam's rational approximation).
 * Accurate to ~1.15e-9 in relative error, which is far beyond what a wait-time
 * forecast needs, but it is cheap and avoids a dependency.
 */
export function probit(p: number): number {
  const a = [
    -3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2,
    1.38357751867269e2, -3.066479806614716e1, 2.506628277459239,
  ];
  const b = [
    -5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2,
    6.680131188771972e1, -1.328068155288572e1,
  ];
  const c = [
    -7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838,
    -2.549732539343734, 4.374664141464968, 2.938163982698783,
  ];
  const d = [
    7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996,
    3.754408661907416,
  ];
  const pl = 0.02425;
  let q: number;
  let r: number;

  if (p < pl) {
    q = Math.sqrt(-2 * Math.log(p));
    return (
      (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    );
  }
  if (p <= 1 - pl) {
    q = p - 0.5;
    r = q * q;
    return (
      ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) /
      (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    );
  }
  q = Math.sqrt(-2 * Math.log(1 - p));
  return (
    -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
    ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
  );
}

/** Abramowitz & Stegun 7.1.26 error function. */
export function erf(x: number): number {
  const s = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * ax);
  return (
    s *
    (1 -
      ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t +
        0.254829592) *
        t *
        Math.exp(-ax * ax))
  );
}

export function mu(cp: Checkpoint, h: number): number {
  return Math.log(cp.base * peak(h));
}

/** Quantile of the wait distribution, in minutes. */
export function quantile(cp: Checkpoint, h: number, p: number): number {
  return Math.exp(mu(cp, h) + cp.sig * probit(p));
}

/** P(wait > x minutes). */
export function pExceed(cp: Checkpoint, h: number, x: number): number {
  return 1 - 0.5 * (1 + erf((Math.log(x) - mu(cp, h)) / cp.sig / Math.SQRT2));
}

/** Lognormal density, used for the CURVE view. */
export function density(cp: Checkpoint, h: number, x: number): number {
  if (x <= 0) return 0;
  const m = mu(cp, h);
  const s = cp.sig;
  return (
    Math.exp(-Math.pow(Math.log(x) - m, 2) / (2 * s * s)) /
    (x * s * Math.sqrt(2 * Math.PI))
  );
}

/**
 * 100 evenly spaced quantiles: "100 days like today, one square each".
 *
 * This is a quantile lattice rather than random sampling, which matters for the
 * frequency view: the counts in the legend are then exact rather than jittering
 * on every render, so a user who counts the red squares gets the number the
 * legend claims.
 */
export function hundredDays(cp: Checkpoint, h: number): number[] {
  return Array.from({ length: 100 }, (_, i) => quantile(cp, h, (i + 0.5) / 100));
}

export function formatHour(h: number): string {
  return `${h < 10 ? '0' : ''}${h}:00`;
}

export function oneInLabel(risk: number): string {
  return `1 in ${Math.round(1 / Math.max(risk, 0.004))}`;
}
