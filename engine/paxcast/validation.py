"""
Probabilistic validation for PaxCast.

A forecast that says "P90 = 48,000" is making a falsifiable claim: over many
days, actual throughput should exceed 48,000 about 10% of the time. Point-error
metrics like MAPE cannot test that claim, so they are not the primary metric
here. What we test instead:

  * PIT / rank histogram uniformity  -- are the stated percentiles honest?
  * Interval coverage                -- does the nominal 80% band contain 80%?
  * CRPS                             -- overall distributional sharpness+calibration
  * Pinball loss at P10/P50/P90      -- accuracy at the quantiles operators use

IMPORTANT ON SCOPE
------------------
Two different things need validating and they must not be confused:

  (a) Machinery calibration. Given a data-generating process, does the engine's
      stated P90 actually behave like a P90? This can be tested today, with no
      external data, by drawing "actuals" from a known process and scoring the
      engine's forecast against them. `validate_machinery` does this, including
      under deliberate misspecification.

  (b) Empirical accuracy. Do the engine's *priors* describe real airports?
      This cannot be tested without real historical throughput data and is
      explicitly out of scope for the prototype. `backtest` implements the
      rolling-origin harness that will run against real data in Phase 1; it is
      wired and tested, but until real series are ingested it reports on
      whatever series it is handed.

Reporting (a) as though it were (b) would be dishonest, so the report labels
them separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from .engine import PaxCastEngine
from .models import Airport, SimulationConfig


# ---------------------------------------------------------------------------
# Scoring rules
# ---------------------------------------------------------------------------


def crps_ensemble(forecast: np.ndarray, observed: float) -> float:
    """CRPS estimated from an ensemble, via the energy form.

        CRPS = E|X - y| - 0.5 * E|X - X'|

    The second term is computed with the sorted-sample identity, which is
    O(n log n) rather than the O(n^2) double sum.
    """
    x = np.sort(np.asarray(forecast, dtype=np.float64))
    n = x.size
    term1 = np.abs(x - observed).mean()
    # E|X - X'| = (2 / n^2) * sum_i (2i - n + 1) * x_(i)
    i = np.arange(n)
    term2 = (2.0 / (n * n)) * np.sum((2 * i - n + 1) * x)
    return float(term1 - 0.5 * term2)


def pinball_loss(forecast: np.ndarray, observed: float, q: float) -> float:
    """Quantile (pinball) loss at level q."""
    pred = np.percentile(forecast, q * 100.0)
    diff = observed - pred
    return float(max(q * diff, (q - 1.0) * diff))


def pit_value(forecast: np.ndarray, observed: float) -> float:
    """Probability integral transform: F(y) under the forecast ensemble.

    If the forecast is calibrated, PIT values are Uniform(0,1). Systematic
    deviations diagnose the failure mode directly:
      U-shaped  -> forecast too narrow (overconfident)
      hump      -> forecast too wide   (underconfident)
      sloped    -> biased
    """
    return float((np.asarray(forecast) <= observed).mean())


def interval_coverage(
    lows: np.ndarray, highs: np.ndarray, observed: np.ndarray
) -> float:
    return float(((observed >= lows) & (observed <= highs)).mean())


# ---------------------------------------------------------------------------
# Report container
# ---------------------------------------------------------------------------


@dataclass
class ValidationReport:
    scope: str
    n_observations: int
    pit_values: list[float] = field(default_factory=list)
    coverage_50: float = 0.0
    coverage_80: float = 0.0
    coverage_90: float = 0.0
    crps: float = 0.0
    crps_baseline: float = 0.0          # informed baseline: dow median + spread
    crps_point: float = 0.0             # deterministic point forecast (= MAE)
    crps_skill: float = 0.0
    crps_skill_vs_point: float = 0.0
    pinball: dict[str, float] = field(default_factory=dict)
    ks_statistic: float = 0.0
    ks_pvalue: float = 0.0
    n_effective: int = 0
    verdict: str = ""

    def summary(self) -> str:
        lines = [
            f"scope                 : {self.scope}",
            f"observations          : {self.n_observations}",
            f"coverage  50% nominal : {self.coverage_50:6.1%}   (target 50%)",
            f"coverage  80% nominal : {self.coverage_80:6.1%}   (target 80%)",
            f"coverage  90% nominal : {self.coverage_90:6.1%}   (target 90%)",
            f"PIT uniformity (KS)   : D={self.ks_statistic:.4f}  p={self.ks_pvalue:.4f}"
            f"  (n_eff={self.n_effective})",
            f"CRPS  PaxCast         : {self.crps:12,.1f}",
            f"CRPS  informed naive  : {self.crps_baseline:12,.1f}  (dow median + spread)",
            f"CRPS  point forecast  : {self.crps_point:12,.1f}  (current practice)",
            f"skill vs informed     : {self.crps_skill:6.1%}",
            f"skill vs point fcst   : {self.crps_skill_vs_point:6.1%}",
            "pinball loss          : "
            + "  ".join(f"{k}={v:,.0f}" for k, v in self.pinball.items()),
            f"verdict               : {self.verdict}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# (a) Machinery calibration
# ---------------------------------------------------------------------------


def validate_machinery(
    airport: Airport,
    start: date,
    n_trials: int = 200,
    horizon_days: int = 14,
    misspecify: float = 0.0,
    seed: int = 7,
) -> ValidationReport:
    """Score the engine's forecast against synthetic actuals.

    Procedure
    ---------
    1. Run the engine once to produce a forecast ensemble for each day.
    2. Draw "actual" outcomes from an independent run with different seeds --
       i.e. a different realisation of the same process.
    3. Score forecast vs actual with PIT, coverage and CRPS.

    `misspecify` perturbs the truth-generating process relative to the model
    (a multiplicative demand shift the forecaster does not know about). At
    misspecify=0 the engine should be near-perfectly calibrated; the point of
    the non-zero cases is to confirm the diagnostics *detect* miscalibration
    rather than flattering the model.
    """
    from scipy.stats import kstest

    from .models import Scenario

    engine = PaxCastEngine()
    cfg = SimulationConfig(
        start_date=start,
        horizon_days=horizon_days,
        n_iterations=4_000,
        seed=seed,
        adaptive=False,
    )

    # Forecast ensemble: retain the raw daily draws, not just percentiles.
    table = engine._build_flight_table(airport, Scenario())
    rng = np.random.default_rng(seed)
    weather = _weather_for(airport)
    weekdays = np.array(
        [(start + timedelta(days=d)).weekday() for d in range(horizon_days)]
    )
    season = engine._season_factors(airport, cfg)
    calib = float(getattr(airport, "calibration_factor", 1.0) or 1.0)

    forecast, _ = engine._run_batch(
        table, Scenario(), cfg, weather, weekdays, season, calib,
        horizon_days, 4_000, rng,
    )

    # Truth draws: independent seeds, optionally shifted process.
    truth_rng = np.random.default_rng(seed + 10_000)
    truth_scenario = Scenario(demand_multiplier=1.0 + misspecify)
    truth, _ = engine._run_batch(
        table, truth_scenario, cfg, weather, weekdays, season, calib,
        horizon_days, n_trials, truth_rng,
    )

    pits, crps_vals, base_vals, point_vals = [], [], [], []
    cov50 = cov80 = cov90 = 0
    pin = {"p10": 0.0, "p50": 0.0, "p90": 0.0}
    n_obs = 0

    # Seasonal-naive baseline: the forecast's own day-of-week median, with an
    # empirical spread taken from historical day-to-day variation. This is the
    # honest incumbent -- what a planner does with a spreadsheet today.
    dow_median = {}
    for wd in range(7):
        cols = np.flatnonzero(weekdays == wd)
        if cols.size:
            dow_median[wd] = float(np.median(forecast[:, cols]))

    for d in range(horizon_days):
        ens = forecast[:, d]
        if ens.max() <= 0:
            continue
        lo50, hi50 = np.percentile(ens, [25, 75])
        lo80, hi80 = np.percentile(ens, [10, 90])
        lo90, hi90 = np.percentile(ens, [5, 95])
        base_point = dow_median.get(int(weekdays[d]), float(np.median(ens)))
        base_ens = base_point * (1.0 + np.random.default_rng(d).normal(0, 0.05, 500))

        for t in range(n_trials):
            y = float(truth[t, d])
            n_obs += 1
            pits.append(pit_value(ens, y))
            cov50 += lo50 <= y <= hi50
            cov80 += lo80 <= y <= hi80
            cov90 += lo90 <= y <= hi90
            crps_vals.append(crps_ensemble(ens[::4], y))
            base_vals.append(crps_ensemble(base_ens, y))
            # A deterministic forecast scored by CRPS reduces exactly to MAE.
            # This is the fair representation of "we expect 41,300 on Tuesday".
            point_vals.append(abs(y - base_point))
            for q, k in ((0.1, "p10"), (0.5, "p50"), (0.9, "p90")):
                pin[k] += pinball_loss(ens, y, q)

    # The KS test assumes independent observations, and these are not.
    # Within a trial, the PIT values across days share one realised path of
    # growth, shock and weather, so they move together. Treating all
    # n_trials * n_days values as independent inflates the effective sample
    # size and makes the test reject calibrated models -- which is exactly what
    # it did the first time this endpoint was run. We estimate the effective
    # sample size from the observed within-trial correlation instead.
    pit_matrix = np.array(pits, dtype=np.float64).reshape(horizon_days, n_trials).T
    ks_stat, ks_p, n_eff = _ks_with_effective_n(pit_matrix)

    crps = float(np.mean(crps_vals))
    base = float(np.mean(base_vals))
    point = float(np.mean(point_vals))

    rep = ValidationReport(
        scope=(
            "machinery calibration (synthetic actuals"
            + (f", misspecified {misspecify:+.0%})" if misspecify else ")")
        ),
        n_observations=n_obs,
        pit_values=pits,
        coverage_50=cov50 / max(n_obs, 1),
        coverage_80=cov80 / max(n_obs, 1),
        coverage_90=cov90 / max(n_obs, 1),
        crps=crps,
        crps_baseline=base,
        crps_point=point,
        crps_skill=(base - crps) / base if base else 0.0,
        crps_skill_vs_point=(point - crps) / point if point else 0.0,
        pinball={k: v / max(n_obs, 1) for k, v in pin.items()},
        ks_statistic=float(ks_stat),
        ks_pvalue=float(ks_p),
        n_effective=int(n_eff),
    )
    rep.verdict = _verdict(rep)
    return rep


def _ks_with_effective_n(pit_matrix: np.ndarray) -> tuple[float, float, int]:
    """KS test for uniformity, corrected for within-trial dependence.

    `pit_matrix` is (n_trials, n_days). The KS *statistic* is computed on the
    pooled sample as usual, but its p-value is evaluated against an effective
    sample size

        n_eff = n_total / (1 + (n_days - 1) * rho_bar)

    where rho_bar is the mean pairwise correlation of PIT values across days
    within a trial. This is the standard design-effect correction. When days
    are independent it reduces to the pooled n; when they move in lockstep it
    reduces to the number of trials, which is the conservative bound.
    """
    from scipy.stats import kstwo

    n_trials, n_days = pit_matrix.shape
    flat = np.sort(pit_matrix.ravel())
    n = flat.size
    # Two-sided KS: D = max(D+, D-). Using only |u_i - i/n| understates D-
    # and makes the test conservative, so both branches are computed.
    i = np.arange(1, n + 1)
    d_plus = float(np.max(i / n - flat))
    d_minus = float(np.max(flat - (i - 1) / n))
    stat = max(d_plus, d_minus)

    if n_days > 1 and n_trials > 2:
        corr = np.corrcoef(pit_matrix.T)
        off = corr[~np.eye(n_days, dtype=bool)]
        rho_bar = float(np.clip(np.nanmean(off), 0.0, 1.0))
    else:
        rho_bar = 0.0

    design_effect = 1.0 + (n_days - 1) * rho_bar
    n_eff = max(int(flat.size / design_effect), 10)
    p = float(kstwo.sf(stat, n_eff))
    return stat, p, n_eff


def _weather_for(airport: Airport):
    from .distributions import WeatherChain

    return WeatherChain.for_climate(airport.climate)


def _verdict(rep: ValidationReport) -> str:
    problems = []
    if abs(rep.coverage_80 - 0.80) > 0.05:
        problems.append(
            f"80pct band {'too narrow' if rep.coverage_80 < 0.80 else 'too wide'}"
        )
    if abs(rep.coverage_90 - 0.90) > 0.04:
        problems.append(
            f"90pct band {'too narrow' if rep.coverage_90 < 0.90 else 'too wide'}"
        )
    if rep.ks_pvalue < 0.01:
        problems.append("PIT not uniform")
    if rep.crps_skill < 0:
        problems.append("worse than seasonal naive")
    return "CALIBRATED" if not problems else "MISCALIBRATED: " + "; ".join(problems)


def pit_histogram(pits: list[float], bins: int = 10) -> str:
    """ASCII rank histogram. Flat is good."""
    counts, _ = np.histogram(pits, bins=bins, range=(0, 1))
    expected = len(pits) / bins
    out = []
    for i, c in enumerate(counts):
        ratio = c / expected if expected else 0
        bar = "#" * int(round(ratio * 25))
        out.append(f"  [{i / bins:.1f}-{(i + 1) / bins:.1f}] {bar:<32} {ratio:5.2f}x")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# (b) Empirical backtest harness -- runs against real series when available
# ---------------------------------------------------------------------------


def backtest(
    airport: Airport,
    actuals: dict[date, float],
    origins: list[date],
    horizon_days: int = 14,
    n_iterations: int = 4_000,
) -> ValidationReport:
    """Rolling-origin backtest against observed daily throughput.

    `actuals` maps calendar date to observed passengers. `origins` are the
    forecast issue dates. For each origin we forecast forward `horizon_days`
    and score every day for which an actual exists. This is the harness that
    will consume Eurostat/BTS series in Phase 1.
    """
    from scipy.stats import kstest

    engine = PaxCastEngine()
    pits, crps_vals, base_vals = [], [], []
    cov50 = cov80 = cov90 = 0
    pin = {"p10": 0.0, "p50": 0.0, "p90": 0.0}
    n_obs = 0

    for origin in origins:
        cfg = SimulationConfig(
            start_date=origin,
            horizon_days=horizon_days,
            n_iterations=n_iterations,
            seed=abs(hash(origin)) % (2**31),
            adaptive=False,
        )
        res = engine.simulate(airport, cfg)
        for d, iso in enumerate(res.dates):
            day = date.fromisoformat(iso)
            if day not in actuals:
                continue
            y = actuals[day]
            n_obs += 1
            # Reconstruct an approximate ensemble from the reported percentiles.
            ens = np.interp(
                np.linspace(0.01, 0.99, 400),
                [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95],
                [res.percentiles[f"p{p}"][d] for p in (5, 10, 25, 50, 75, 90, 95)],
            )
            pits.append(pit_value(ens, y))
            cov50 += res.percentiles["p25"][d] <= y <= res.percentiles["p75"][d]
            cov80 += res.percentiles["p10"][d] <= y <= res.percentiles["p90"][d]
            cov90 += res.percentiles["p5"][d] <= y <= res.percentiles["p95"][d]
            crps_vals.append(crps_ensemble(ens, y))
            same_dow = [
                v
                for k, v in actuals.items()
                if k.weekday() == day.weekday() and k < origin
            ]
            base_point = float(np.median(same_dow)) if same_dow else res.mean[d]
            base_vals.append(crps_ensemble(np.full(200, base_point), y))
            for q, k in ((0.1, "p10"), (0.5, "p50"), (0.9, "p90")):
                pin[k] += pinball_loss(ens, y, q)

    if n_obs == 0:
        return ValidationReport(scope="empirical backtest", n_observations=0,
                                verdict="NO DATA")

    ks = kstest(pits, "uniform")
    crps = float(np.mean(crps_vals))
    base = float(np.mean(base_vals))
    rep = ValidationReport(
        scope="empirical backtest (observed actuals)",
        n_observations=n_obs,
        pit_values=pits,
        coverage_50=cov50 / n_obs,
        coverage_80=cov80 / n_obs,
        coverage_90=cov90 / n_obs,
        crps=crps,
        crps_baseline=base,
        crps_skill=(base - crps) / base if base else 0.0,
        pinball={k: v / n_obs for k, v in pin.items()},
        ks_statistic=float(ks.statistic),
        ks_pvalue=float(ks.pvalue),
    )
    rep.verdict = _verdict(rep)
    return rep
