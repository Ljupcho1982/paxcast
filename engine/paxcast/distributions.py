"""
Stochastic parameter models for the PaxCast Monte Carlo engine.

Every uncertain quantity in the passenger-throughput model is described here.
Distributions are chosen for their support, not for convenience:

  * load factor      -> Beta          (bounded [0,1], left-skewed in practice)
  * show-up rate     -> Beta          (bounded, tight, high mean)
  * cancellation     -> Bernoulli     (discrete event, weather-conditioned)
  * weather state    -> Markov chain  (persistent, not i.i.d.)
  * demand shock     -> Poisson x LogNormal x Geometric  (rare, fat-tailed, sticky)
  * growth factor    -> Student-t     (fat tails; Normal badly understates aviation shocks)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# --------------------------------------------------------------------------
# Beta reparameterisation
# --------------------------------------------------------------------------


def beta_from_moments(mean: float, sd: float) -> tuple[float, float]:
    """Convert (mean, sd) to Beta(alpha, beta).

    Operators think in "mean load factor 82%, +/- 7 points", not in shape
    parameters. This keeps the calibration layer human-readable.
    """
    if not 0.0 < mean < 1.0:
        raise ValueError(f"mean must be in (0,1), got {mean}")
    max_sd = np.sqrt(mean * (1.0 - mean))
    if sd <= 0 or sd >= max_sd:
        # Clamp rather than raise: thin-data airports produce silly sd estimates.
        sd = min(max(sd, 1e-4), max_sd * 0.99)
    nu = mean * (1.0 - mean) / (sd * sd) - 1.0
    return mean * nu, (1.0 - mean) * nu


def beta_moments(alpha: float, beta: float) -> tuple[float, float]:
    m = alpha / (alpha + beta)
    v = (alpha * beta) / ((alpha + beta) ** 2 * (alpha + beta + 1.0))
    return m, float(np.sqrt(v))


# --------------------------------------------------------------------------
# Load factor model
# --------------------------------------------------------------------------


@dataclass(slots=True)
class LoadFactorModel:
    """Beta load-factor model with carrier-type and seasonal adjustment.

    Empirical anchors (IATA/ICAO published averages):
      LCC short-haul    ~0.90 mean, sd 0.06   (yield-managed to high LF)
      FSC short-haul    ~0.80 mean, sd 0.09
      FSC long-haul     ~0.83 mean, sd 0.07
      Regional          ~0.72 mean, sd 0.12
      Charter           ~0.92 mean, sd 0.05
    """

    mean: float = 0.82
    sd: float = 0.09
    # multiplicative seasonal adjustment applied to the mean before sampling
    season_factor: float = 1.0

    def sample(self, u: np.ndarray) -> np.ndarray:
        """Inverse-CDF sample from uniforms `u`.

        Taking uniforms as input (rather than sampling internally) is what
        allows the copula layer to inject correlation: we hand it correlated
        uniforms and it hands back correlated load factors.
        """
        from scipy.stats import beta as beta_dist

        m = float(np.clip(self.mean * self.season_factor, 0.02, 0.985))
        a, b = beta_from_moments(m, self.sd)
        return beta_dist.ppf(u, a, b)


CARRIER_LF_PRIORS: dict[str, tuple[float, float]] = {
    "LCC": (0.90, 0.060),
    "FSC_SHORT": (0.80, 0.090),
    "FSC_LONG": (0.83, 0.070),
    "REGIONAL": (0.72, 0.120),
    "CHARTER": (0.92, 0.050),
}


# --------------------------------------------------------------------------
# Weather: a persistent Markov chain, not i.i.d. draws
# --------------------------------------------------------------------------


@dataclass(slots=True)
class WeatherChain:
    """Three-state daily weather chain: 0 = benign, 1 = degraded, 2 = severe.

    Persistence matters enormously. A storm that closes an airport on Tuesday
    is highly likely to still be disrupting it on Wednesday, and i.i.d.
    sampling would wash that out and understate the tail of the multi-day
    throughput distribution.

    Cancellation probability multipliers by state are applied downstream.
    """

    # rows = from-state, cols = to-state
    transition: np.ndarray = field(
        default_factory=lambda: np.array(
            [
                [0.90, 0.08, 0.02],
                [0.45, 0.45, 0.10],
                [0.30, 0.35, 0.35],
            ]
        )
    )
    initial: np.ndarray = field(default_factory=lambda: np.array([0.85, 0.12, 0.03]))

    def simulate(self, n_iter: int, n_days: int, rng: np.random.Generator) -> np.ndarray:
        """Return integer state array of shape (n_iter, n_days)."""
        states = np.empty((n_iter, n_days), dtype=np.int8)
        cum_init = np.cumsum(self.initial)
        u0 = rng.random(n_iter)
        states[:, 0] = np.searchsorted(cum_init, u0)

        cum_trans = np.cumsum(self.transition, axis=1)
        for d in range(1, n_days):
            u = rng.random(n_iter)
            prev = states[:, d - 1]
            # vectorised categorical draw conditioned on previous state
            thresholds = cum_trans[prev]  # (n_iter, 3)
            states[:, d] = (u[:, None] > thresholds).sum(axis=1)
        return states

    @staticmethod
    def for_climate(climate: str) -> "WeatherChain":
        """Coarse climatology presets until METAR history is ingested."""
        presets = {
            "mild": np.array([[0.94, 0.05, 0.01], [0.55, 0.40, 0.05], [0.40, 0.35, 0.25]]),
            "temperate": np.array([[0.90, 0.08, 0.02], [0.45, 0.45, 0.10], [0.30, 0.35, 0.35]]),
            "harsh_winter": np.array([[0.82, 0.14, 0.04], [0.35, 0.48, 0.17], [0.22, 0.33, 0.45]]),
            "monsoon": np.array([[0.80, 0.15, 0.05], [0.30, 0.50, 0.20], [0.20, 0.35, 0.45]]),
        }
        return WeatherChain(transition=presets.get(climate, presets["temperate"]))


# cancellation probability multiplier by weather state
WEATHER_CANCEL_MULTIPLIER = np.array([1.0, 3.5, 14.0])


# --------------------------------------------------------------------------
# Demand shocks: rare, severe, persistent
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ShockProcess:
    """Compound Poisson demand-shock process.

    Events arrive at rate `lam` per year. Each event has a severity (fractional
    demand loss) drawn from a LogNormal and a duration in days drawn from a
    Geometric distribution. Severity is capped at 0.98 -- even a pandemic did
    not take global traffic to literally zero.

    This is the component that makes the output distribution fat-tailed, and
    it is the honest answer to "your model would have missed 2020".
    """

    lam_per_year: float = 0.45
    severity_mu: float = -2.0     # log-space; exp(-2.0) ~ 0.135 median loss
    severity_sigma: float = 1.10
    mean_duration_days: float = 25.0
    severity_cap: float = 0.98

    def simulate(self, n_iter: int, n_days: int, rng: np.random.Generator) -> np.ndarray:
        """Return multiplicative demand factor of shape (n_iter, n_days), <= 1.0."""
        factor = np.ones((n_iter, n_days), dtype=np.float32)
        lam_window = self.lam_per_year * (n_days / 365.0)
        n_events = rng.poisson(lam_window, size=n_iter)
        max_events = int(n_events.max()) if n_events.size else 0
        if max_events == 0:
            return factor

        p_end = 1.0 / max(self.mean_duration_days, 1.0)
        for k in range(max_events):
            active = n_events > k
            n_active = int(active.sum())
            if n_active == 0:
                continue
            start = rng.integers(0, n_days, size=n_active)
            dur = rng.geometric(p_end, size=n_active)
            sev = np.minimum(
                rng.lognormal(self.severity_mu, self.severity_sigma, size=n_active),
                self.severity_cap,
            )
            idx = np.flatnonzero(active)
            day_grid = np.arange(n_days)[None, :]
            mask = (day_grid >= start[:, None]) & (day_grid < (start + dur)[:, None])
            factor[idx] *= np.where(mask, (1.0 - sev)[:, None], 1.0).astype(np.float32)
        return factor


# --------------------------------------------------------------------------
# Hierarchical growth factor
# --------------------------------------------------------------------------


@dataclass(slots=True)
class GrowthModel:
    """Hierarchical log-growth: global + regional + airport idiosyncratic.

        log G(a,t) = mu_global*t + eps_global + eps_region + eps_airport

    Student-t innovations, because aviation demand has visibly fatter tails
    than a Normal permits and a Normal-based P95 is dangerously optimistic.
    """

    annual_drift: float = 0.035        # long-run global pax growth ~3.5%/yr
    global_sigma: float = 0.030
    region_sigma: float = 0.025
    airport_sigma: float = 0.045
    df: float = 4.0                    # degrees of freedom -> tail weight

    def simulate(self, n_iter: int, n_days: int, rng: np.random.Generator) -> np.ndarray:
        years = np.arange(n_days) / 365.0
        scale = np.sqrt((self.df - 2.0) / self.df)  # unit-variance standardisation

        def t_draw(sigma: float) -> np.ndarray:
            return rng.standard_t(self.df, size=(n_iter, 1)) * scale * sigma

        # uncertainty in the drift itself compounds with horizon
        eps = t_draw(self.global_sigma) + t_draw(self.region_sigma) + t_draw(self.airport_sigma)
        drift_noise = t_draw(self.global_sigma * 0.6) * years[None, :]
        log_g = self.annual_drift * years[None, :] + eps * np.sqrt(np.maximum(years, 1 / 365.0))[None, :] + drift_noise
        return np.exp(log_g).astype(np.float32)
