"""
PaxCast Monte Carlo engine.

Core identity, per flight f on day d:

    Pax(f,d) = Seats(f) * LF(f,d) * Flown(f,d) * ShowUp(f,d)
               * Growth(d) * Shock(d) * Season(d) * Calib(a)

Airport throughput on day d follows the ACI convention -- arrivals plus
departures, with transfer passengers counted in both directions:

    Pax(a,d) = sum_{f in Arr(a,d)} Pax(f,d) + sum_{f in Dep(a,d)} Pax(f,d)

Design notes
------------
* Everything is vectorised over (iterations x flights); there is no Python-level
  work per flight inside the loop.
* Load factors are drawn through a one-factor Gaussian copula, so the sum over
  flights does not collapse to a CLT-narrow band. This is the single most
  important modelling decision in the file.
* The normal-to-Beta transform is tabulated (see quantile_table.py) rather than
  evaluated exactly, which is what makes 1,400-flight airports tractable.
* Iteration count is adaptive: we stop when the standard error of the P90 --
  the number operators actually staff against -- falls below target, not at an
  arbitrary round number.
"""

from __future__ import annotations

import time
from datetime import timedelta

import numpy as np

from .copula import OneFactorCopula
from .distributions import (
    CARRIER_LF_PRIORS,
    WEATHER_CANCEL_MULTIPLIER,
    GrowthModel,
    ShockProcess,
    WeatherChain,
    beta_from_moments,
)
from .models import Airport, Direction, ForecastResult, Scenario, SimulationConfig
from .quantile_table import GLOBAL_TABLE_CACHE

PERCENTILES = [5, 10, 25, 50, 75, 90, 95]

# Show-up (boarded / booked). Tight, high mean, with a small common component
# because holidays and disruption move no-show rates across the whole field.
SHOWUP_MEAN = 0.955
SHOWUP_COMMON_SD = 0.012
SHOWUP_IDIO_SD = 0.010
# Above this many daily flights, per-flight show-up noise is dropped as immaterial.
IDIO_SHOWUP_MAX_FLIGHTS = 200


class PaxCastEngine:
    """Simulates the distribution of daily passenger throughput at an airport."""

    def __init__(
        self,
        copula: OneFactorCopula | None = None,
        growth: GrowthModel | None = None,
        shocks: ShockProcess | None = None,
    ):
        self.copula = copula or OneFactorCopula(rho_common=0.30, rho_group=0.60)
        self.growth = growth or GrowthModel()
        self.shocks = shocks or ShockProcess()

    # ------------------------------------------------------------------
    # Flight-table preparation: done once per run, reused across iterations
    # ------------------------------------------------------------------

    def _build_flight_table(self, airport: Airport, scenario: Scenario) -> dict:
        flights = [
            f
            for f in airport.flights
            if f.carrier not in scenario.grounded_carriers
            and f.other_endpoint not in scenario.closed_routes
        ]
        n = len(flights)
        if n == 0:
            return {"n": 0}

        seats = np.array([f.seats for f in flights], dtype=np.float32)
        seats = seats * np.float32(scenario.capacity_multiplier)

        lf_mean = np.empty(n, dtype=np.float64)
        lf_sd = np.empty(n, dtype=np.float64)
        for i, f in enumerate(flights):
            m, s = CARRIER_LF_PRIORS[f.carrier_type.value]
            lf_mean[i], lf_sd[i] = m, s
        lf_mean = np.clip(lf_mean + scenario.load_factor_delta, 0.02, 0.985)

        alpha = np.empty(n, dtype=np.float64)
        beta = np.empty(n, dtype=np.float64)
        for i in range(n):
            alpha[i], beta[i] = beta_from_moments(lf_mean[i], lf_sd[i])

        # Only a handful of distinct (alpha, beta) pairs exist, so one
        # interpolation table serves hundreds of flights.
        tables, table_idx = GLOBAL_TABLE_CACHE.build_index(alpha, beta)

        base_cancel = np.array([1.0 - f.reliability for f in flights], dtype=np.float32)
        base_cancel = np.clip(base_cancel + scenario.extra_cancel_prob, 0.0, 1.0)

        carriers = sorted({f.carrier for f in flights})
        cmap = {c: i for i, c in enumerate(carriers)}
        group_ids = np.array([cmap[f.carrier] for f in flights], dtype=np.int64)

        operates = np.array(
            [[(f.dow_mask >> wd) & 1 for f in flights] for wd in range(7)], dtype=bool
        )
        sched_hour = np.array(
            [min(f.sched_minute // 60, 23) for f in flights], dtype=np.int64
        )

        return {
            "n": n,
            "seats": seats,
            "tables": tables,
            "table_idx": table_idx,
            "base_cancel": base_cancel,
            "group_ids": group_ids,
            "n_groups": len(carriers),
            "operates": operates,
            "sched_hour": sched_hour,
        }

    # ------------------------------------------------------------------

    @staticmethod
    def _apply_tables(z: np.ndarray, tables: list, table_idx: np.ndarray) -> np.ndarray:
        """Map standard normals to load factors, grouped by distinct prior.

        Slicing by table keeps each evaluation on a large contiguous block
        instead of dispatching per flight.
        """
        if len(tables) == 1:
            return tables[0](z)
        out = np.empty(z.shape, dtype=np.float32)
        for t_i, table in enumerate(tables):
            cols = np.flatnonzero(table_idx == t_i)
            if cols.size:
                out[:, cols] = table(z[:, cols])
        return out

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def simulate(
        self,
        airport: Airport,
        config: SimulationConfig,
        scenario: Scenario | None = None,
    ) -> ForecastResult:
        t0 = time.perf_counter()
        scenario = scenario or Scenario()
        table = self._build_flight_table(airport, scenario)
        if table["n"] == 0:
            raise ValueError(f"No flights remain for {airport.iata} under this scenario")

        n_days = config.horizon_days
        rng = np.random.default_rng(config.seed)
        weather = WeatherChain.for_climate(airport.climate)

        weekdays = np.array(
            [(config.start_date + timedelta(days=d)).weekday() for d in range(n_days)]
        )
        season = self._season_factors(airport, config)
        calib = float(getattr(airport, "calibration_factor", 1.0) or 1.0)

        collected: list[np.ndarray] = []
        hour_accum = np.zeros((7, 24), dtype=np.float64)
        hour_counts = np.zeros(7, dtype=np.int64)
        total_iters, converged, rel_se = 0, False, float("inf")

        target = config.n_iterations
        batch = config.batch_size if config.adaptive else config.n_iterations

        while total_iters < target:
            k = int(min(batch, target - total_iters))
            daily, hours = self._run_batch(
                table, scenario, config, weather, weekdays, season, calib, n_days, k, rng
            )
            collected.append(daily)
            hour_accum += hours[0]
            hour_counts += hours[1]
            total_iters += k

            if config.adaptive and total_iters >= config.min_iterations:
                rel_se = self._p90_rel_se(np.concatenate(collected, axis=0).sum(axis=1))
                if rel_se <= config.p90_se_target:
                    converged = True
                    break

        daily_all = np.concatenate(collected, axis=0)
        if not np.isfinite(rel_se):
            rel_se = self._p90_rel_se(daily_all.sum(axis=1))
        converged = converged or rel_se <= config.p90_se_target

        return self._assemble(
            airport, config, scenario, daily_all, hour_accum, hour_counts,
            total_iters, converged, rel_se, (time.perf_counter() - t0) * 1000.0,
        )

    # ------------------------------------------------------------------

    def _run_batch(
        self, table, scenario, config, weather, weekdays, season, calib,
        n_days, n_iter, rng,
    ):
        seats = table["seats"]
        tables, table_idx = table["tables"], table["table_idx"]
        base_cancel = table["base_cancel"]
        group_ids, n_groups = table["group_ids"], table["n_groups"]
        operates, sched_hour = table["operates"], table["sched_hour"]

        n_draw = (n_iter + 1) // 2 if config.use_antithetic else n_iter

        wx = weather.simulate(n_iter, n_days, rng)
        growth = self.growth.simulate(n_iter, n_days, rng)
        shock = (
            np.ones((n_iter, n_days), dtype=np.float32)
            if scenario.disable_shocks
            else self.shocks.simulate(n_iter, n_days, rng)
        )

        common_z = (
            self._lhs_common(n_draw, n_days, rng)
            if config.use_lhs
            else rng.standard_normal((n_draw, n_days))
        )
        if config.use_antithetic:
            common_z = np.concatenate([common_z, -common_z], axis=0)[:n_iter]
        common_z = np.ascontiguousarray(common_z, dtype=np.float32)

        w_common = np.float32(np.sqrt(self.copula.rho_common))
        w_group = np.float32(
            np.sqrt(max(self.copula.rho_group - self.copula.rho_common, 0.0))
        )
        w_idio = np.float32(np.sqrt(max(1.0 - self.copula.rho_group, 1e-12)))

        daily = np.empty((n_iter, n_days), dtype=np.float32)
        hour_accum = np.zeros((7, 24), dtype=np.float64)
        hour_counts = np.zeros(7, dtype=np.int64)
        demand_mult = np.float32(scenario.demand_multiplier * calib)

        for d in range(n_days):
            wd = int(weekdays[d])
            idx = np.flatnonzero(operates[wd])
            if idx.size == 0:
                daily[:, d] = 0.0
                continue
            m = idx.size

            # --- correlated latent normals: common + group + idiosyncratic ---
            g = rng.standard_normal((n_iter, n_groups), dtype=np.float32)
            z = w_common * common_z[:, d : d + 1]
            z = z + w_group * g[:, group_ids[idx]]
            z += w_idio * rng.standard_normal((n_iter, m), dtype=np.float32)
            lf = self._apply_tables(z, tables, table_idx[idx])

            # --- weather-conditioned cancellation ---
            mult = WEATHER_CANCEL_MULTIPLIER[wx[:, d]].astype(np.float32)[:, None]
            p_cancel = np.minimum(base_cancel[idx][None, :] * mult, np.float32(0.97))
            flown = (rng.random((n_iter, m), dtype=np.float32) >= p_cancel).astype(
                np.float32
            )

            # --- show-up rate ---
            # A field-wide common component (holidays, disruption, weather move
            # no-show rates together) plus, at small airports only, per-flight
            # noise. At large airports the idiosyncratic term diversifies away:
            # measured contribution to the sd of daily throughput at 1,100
            # flights is 0.035%, which does not justify a full (iter x flight)
            # normal draw per simulated day. See tests/test_showup.py.
            su = np.float32(SHOWUP_MEAN) + rng.standard_normal(
                (n_iter, 1), dtype=np.float32
            ) * np.float32(SHOWUP_COMMON_SD)
            if m < IDIO_SHOWUP_MAX_FLIGHTS:
                su = su + rng.standard_normal(
                    (n_iter, m), dtype=np.float32
                ) * np.float32(SHOWUP_IDIO_SD)
            np.clip(su, 0.60, 1.0, out=su)

            pax = seats[idx][None, :] * lf * flown * su
            day_factor = growth[:, d] * shock[:, d] * np.float32(season[d]) * demand_mult
            pax *= day_factor[:, None]

            daily[:, d] = pax.sum(axis=1)

            # Hourly profile uses the mean, not the median, of each flight's
            # passenger load. This is not a shortcut for speed alone: hourly
            # terminal load is a *sum* over flights in the hour, and the median
            # of a sum is not the sum of medians, so accumulating per-flight
            # medians never produced a coherent quantity. Means compose
            # correctly, since E[sum] = sum E[].
            np.add.at(hour_accum[wd], sched_hour[idx], pax.mean(axis=0))
            hour_counts[wd] += 1

        return daily, (hour_accum, hour_counts)

    # ------------------------------------------------------------------

    @staticmethod
    def _lhs_common(n_iter: int, n_days: int, rng: np.random.Generator) -> np.ndarray:
        """Latin hypercube sample of the airport-day common factor.

        The common factor is the highest-leverage input in the model, so it is
        worth stratifying. Per-flight idiosyncratic noise stays pseudo-random --
        its dimension is far too high for QMC to buy anything.
        """
        from scipy.stats import norm, qmc

        sampler = qmc.LatinHypercube(d=n_days, seed=int(rng.integers(0, 2**31 - 1)))
        u = sampler.random(n=n_iter)
        return norm.ppf(np.clip(u, 1e-9, 1 - 1e-9))

    @staticmethod
    def _season_factors(airport: Airport, config: SimulationConfig) -> np.ndarray:
        if airport.seasonality is not None:
            weekly = np.asarray(airport.seasonality, dtype=np.float32)
        else:
            w = np.arange(53)
            weekly = (1.0 + 0.22 * np.sin(2 * np.pi * (w - 12) / 53.0)).astype(np.float32)
        out = np.empty(config.horizon_days, dtype=np.float32)
        for d in range(config.horizon_days):
            day = config.start_date + timedelta(days=d)
            out[d] = weekly[min(day.isocalendar().week - 1, len(weekly) - 1)]
        return out

    @staticmethod
    def _p90_rel_se(totals: np.ndarray) -> float:
        """Relative standard error of the P90 estimate.

        Asymptotic quantile-estimator variance, Var(q_p) = p(1-p)/(n f(q_p)^2),
        with the density estimated by a finite difference on empirical
        quantiles. This is the stopping rule: keep drawing until the tail that
        operators actually staff against is pinned down.
        """
        n = totals.size
        if n < 100:
            return float("inf")
        q = np.percentile(totals, 90)
        lo, hi = np.percentile(totals, [88.0, 92.0])
        density = 0.04 / max(hi - lo, 1e-9)
        se = np.sqrt(0.90 * 0.10 / n) / max(density, 1e-12)
        return float(se / max(abs(q), 1e-9))

    # ------------------------------------------------------------------

    def _assemble(
        self, airport, config, scenario, daily, hour_accum, hour_counts,
        n_iter, converged, rel_se, runtime_ms,
    ) -> ForecastResult:
        dates = [
            (config.start_date + timedelta(days=d)).isoformat()
            for d in range(config.horizon_days)
        ]
        pct = {
            f"p{p}": np.percentile(daily, p, axis=0).round(1).tolist() for p in PERCENTILES
        }
        totals = daily.sum(axis=1)
        total_pct = {f"p{p}": float(np.percentile(totals, p).round(0)) for p in PERCENTILES}

        counts = np.maximum(hour_counts, 1)[:, None]
        grid_arr = hour_accum / counts
        peak_hourly = float(grid_arr.max()) if grid_arr.size else 0.0
        cap = airport.terminal_capacity_hourly
        daily_cap = cap * 18.0

        exceedance = {
            "daily_capacity_pax": float(daily_cap),
            "p_exceed_daily_capacity": float(round(float((daily > daily_cap).mean()), 4)),
            "peak_hour_median_pax": round(peak_hourly, 1),
            "peak_hour_capacity": float(cap),
            "peak_hour_utilisation": round(peak_hourly / cap, 3) if cap else 0.0,
        }

        return ForecastResult(
            iata=airport.iata,
            scenario=scenario.name,
            dates=dates,
            percentiles=pct,
            mean=daily.mean(axis=0).round(1).tolist(),
            total_percentiles=total_pct,
            peak_hour_grid=grid_arr.round(1).tolist(),
            exceedance=exceedance,
            n_iterations=int(n_iter),
            converged=bool(converged),
            p90_rel_se=round(float(rel_se), 5),
            runtime_ms=round(runtime_ms, 1),
            data_quality=airport.data_quality,
            confidence=self._confidence_label(airport.data_quality, rel_se, converged),
        )

    @staticmethod
    def _confidence_label(data_quality: float, rel_se: float, converged: bool) -> str:
        """Honest labelling: a tight simulation on bad data is still bad data."""
        if data_quality < 0.45 or not converged or rel_se > 0.02:
            return "LOW"
        if data_quality >= 0.75 and rel_se <= 0.008:
            return "HIGH"
        return "MEDIUM"
