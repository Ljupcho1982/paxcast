"""
Calibration: turning reported waits into model parameters.

The prototype hardcoded `base` and `sig` per checkpoint. Those were
illustrative. This module replaces them with values fitted from observations,
and it has to solve a specific problem: the first few reports for a new
checkpoint are almost worthless on their own.

Fit two observations by maximum likelihood and you get a confident, wrong
answer -- with n=2 the MLE of sigma can be near zero, which produces a
distribution claiming the wait is *certain*. That is the worst possible failure
for an app whose entire proposition is honest uncertainty.

So the fit is hierarchical, shrinking toward a lane-type prior:

    w_post = n / (n + kappa)                      shrinkage weight
    mu_hat = w_post * mu_obs + (1 - w_post) * mu_prior
    sig    = w_post * sig_obs + (1 - w_post) * sig_prior

At n=0 the prior governs entirely. At n=kappa the fit is half-and-half. As n
grows the checkpoint's own history takes over. kappa is set per parameter --
sigma needs more evidence than the median, because dispersion is harder to
estimate than location.

Observations are also:
  * de-peaked   -- divided by the hour's load multiplier before fitting, so a
                   sample skewed toward rush hour does not inflate the base
  * weighted    -- operator and sensor feeds count more than crowd reports
  * time-decayed -- a report from eight months ago says little about a
                   checkpoint that has since been reconfigured
  * winsorised  -- one 400-minute outlier should not dominate a 30-sample fit
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Lane-type priors: median wait in minutes at an unloaded hour, and dispersion.
LANE_PRIORS: dict[str, tuple[float, float]] = {
    "standard": (12.0, 0.52),
    "expedited": (6.0, 0.38),
    "premium": (4.0, 0.32),
}

# Shrinkage half-weights. sigma is harder to estimate than the median, so it
# needs roughly three times the evidence before the fit trusts it.
KAPPA_MU = 8.0
KAPPA_SIG = 25.0

# Reports older than this contribute nothing.
MAX_AGE_DAYS = 270.0
# Weight halves every this many days.
HALF_LIFE_DAYS = 60.0

SOURCE_WEIGHT = {"sensor": 3.0, "operator": 2.0, "user": 1.0}

MIN_SIG, MAX_SIG = 0.12, 1.40
MIN_BASE, MAX_BASE = 0.5, 180.0


def peak(h: float) -> float:
    """Hour-of-day load multiplier. Shared with the client's waitModel.ts.

    Kept identical on both sides deliberately: if the server de-peaks an
    observation with one curve and the client re-peaks the forecast with
    another, the round trip silently biases every fitted base.
    """
    return (
        1
        + 1.15 * math.exp(-((h - 7) ** 2) / 3.2)
        + 0.65 * math.exp(-((h - 17.5) ** 2) / 4.5)
        + 0.25 * math.exp(-((h - 11.5) ** 2) / 6)
    )


@dataclass
class Observation:
    wait_minutes: float
    hour_local: int
    observed_at: datetime
    source: str = "user"
    weight: float = 1.0


@dataclass
class FitResult:
    base: float
    sig: float
    n_used: int
    effective_n: float
    shrinkage_mu: float
    shrinkage_sig: float
    prior_base: float
    prior_sig: float
    note: str

    @property
    def is_prior_dominated(self) -> bool:
        return self.shrinkage_mu < 0.5


def _age_weight(observed_at: datetime, now: datetime) -> float:
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    age_days = (now - observed_at).total_seconds() / 86400.0
    if age_days < 0:
        age_days = 0.0
    if age_days > MAX_AGE_DAYS:
        return 0.0
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def _winsorise(values: list[float], lo_q: float = 0.05, hi_q: float = 0.95) -> list[float]:
    """Clamp to the 5th/95th percentile rather than dropping outliers.

    Dropping loses the information that an extreme day happened at all, which
    for a right-tailed wait distribution is exactly the information worth
    keeping. Clamping keeps the count and limits the leverage.
    """
    if len(values) < 8:
        return values
    s = sorted(values)
    lo = s[max(int(len(s) * lo_q) - 1, 0)]
    hi = s[min(int(len(s) * hi_q), len(s) - 1)]
    return [min(max(v, lo), hi) for v in values]


def fit_checkpoint(
    observations: list[Observation],
    lane_type: str = "standard",
    prior_base: float | None = None,
    prior_sig: float | None = None,
    now: datetime | None = None,
) -> FitResult:
    """Fit lognormal (base, sig) from observations with shrinkage to prior."""
    now = now or datetime.now(timezone.utc)
    p_base, p_sig = LANE_PRIORS.get(lane_type, LANE_PRIORS["standard"])
    if prior_base is not None:
        p_base = prior_base
    if prior_sig is not None:
        p_sig = prior_sig

    usable: list[tuple[float, float]] = []  # (log de-peaked wait, weight)
    raw_logs: list[float] = []
    weights: list[float] = []

    for o in observations:
        if o.wait_minutes <= 0:
            continue
        w = _age_weight(o.observed_at, now) * SOURCE_WEIGHT.get(o.source, 1.0) * o.weight
        if w <= 1e-6:
            continue
        # De-peak: recover the unloaded-hour equivalent of this observation.
        depeaked = o.wait_minutes / peak(o.hour_local)
        raw_logs.append(math.log(max(depeaked, 1e-3)))
        weights.append(w)

    n = len(raw_logs)
    if n == 0:
        return FitResult(
            base=p_base, sig=p_sig, n_used=0, effective_n=0.0,
            shrinkage_mu=0.0, shrinkage_sig=0.0,
            prior_base=p_base, prior_sig=p_sig,
            note="No usable observations; running on lane-type prior.",
        )

    clamped = _winsorise(raw_logs)
    eff_n = sum(weights)

    wsum = sum(weights)
    mu_obs = sum(l * w for l, w in zip(clamped, weights, strict=True)) / wsum

    if n >= 2:
        var = sum(w * (l - mu_obs) ** 2 for l, w in zip(clamped, weights, strict=True)) / wsum
        # Bessel-style correction using effective sample size, otherwise the
        # weighted variance is biased low and the band comes out too tight.
        denom = max(1.0 - sum(w * w for w in weights) / (wsum * wsum), 1e-6)
        sig_obs = math.sqrt(max(var / denom, 1e-9))
    else:
        sig_obs = p_sig

    w_mu = eff_n / (eff_n + KAPPA_MU)
    w_sig = eff_n / (eff_n + KAPPA_SIG) if n >= 2 else 0.0

    mu_post = w_mu * mu_obs + (1.0 - w_mu) * math.log(p_base)
    sig_post = w_sig * sig_obs + (1.0 - w_sig) * p_sig

    base = min(max(math.exp(mu_post), MIN_BASE), MAX_BASE)
    sig = min(max(sig_post, MIN_SIG), MAX_SIG)

    if w_mu < 0.25:
        note = f"Mostly prior ({n} reports); needs ~{int(KAPPA_MU)} for an even split."
    elif w_mu < 0.7:
        note = f"Blended: {w_mu:.0%} observed, {1 - w_mu:.0%} prior."
    else:
        note = f"Observation-led ({n} reports, {w_mu:.0%} weight)."

    return FitResult(
        base=base, sig=sig, n_used=n, effective_n=eff_n,
        shrinkage_mu=w_mu, shrinkage_sig=w_sig,
        prior_base=p_base, prior_sig=p_sig, note=note,
    )


def confidence_for_fit(fit: FitResult) -> str:
    """Confidence label shown next to a checkpoint forecast."""
    if fit.n_used == 0 or fit.shrinkage_mu < 0.25:
        return "LOW"
    if fit.shrinkage_mu >= 0.7 and fit.shrinkage_sig >= 0.4:
        return "HIGH"
    return "MEDIUM"


def suggest_prior_from_throughput(
    hourly_pax: float,
    lanes: int,
    throughput_per_lane_hour: float = 150.0,
    surge_share: float = 0.42,
    divest_floor_min: float = 2.5,
) -> tuple[float, float, str]:
    """Derive a checkpoint prior from modelled hourly passenger load.

    This is the join between the two halves of PaxCast: the throughput engine
    already produces expected passengers per hour, and a checkpoint is a queue
    served at a known rate.

    WHY NOT A STANDARD QUEUEING FORMULA
    -----------------------------------
    The obvious move is M/M/c, and it is wrong here. M/M/c assumes Poisson
    arrivals, and at a realistic 55-75% mean utilisation it predicts waits of a
    few seconds. Real security queues are not driven by the hourly mean -- they
    are driven by flight banks dumping a large share of the hour's passengers
    into a fifteen-minute window. The queue that forms is a transient overload,
    and a stationary formula cannot see it.

    So the dominant term here is a deterministic fluid surge:

        lambda_surge = hourly_pax * surge_share / 0.25       (arrivals/hr in the bank)
        excess       = max(lambda_surge - capacity, 0)
        queue_peak   = excess * 0.25                          (passengers)
        wait_mean    ~ queue_peak / capacity * 60 / 2         (minutes)

    A Kingman term is added for stochastic variability away from the surge, and
    a floor for divest and screening time that exists even with no queue.

    STATUS: UNVALIDATED. Every constant here is a plausible engineering
    estimate, not a fitted value, and the returned pair is a *prior only*. Once
    a checkpoint accumulates reports, fit_checkpoint() shrinks away from this
    and it stops mattering. Do not present output derived solely from this
    function as a forecast.
    """
    capacity = max(lanes * throughput_per_lane_hour, 1.0)
    rho_mean = hourly_pax / capacity

    # Surge component. Computed in BOTH regimes -- a checkpoint at exactly 100%
    # utilisation still has a bank-driven queue, and an earlier version that
    # only computed the sustained overflow reported a *shorter* wait at
    # saturation than just below it. Keeping this term common makes the
    # function continuous across rho = 1.
    surge_h = 0.25
    lambda_surge = hourly_pax * surge_share / surge_h
    queue_peak = max(lambda_surge - capacity, 0.0) * surge_h
    wait_surge = (queue_peak / capacity * 60.0) / 2.0

    # Kingman (VUT) term for stochastic variability away from the bank.
    # Utilisation is capped at 0.95 inside this term because the formula
    # diverges at saturation; beyond that the surge and overflow terms are the
    # honest description of what is happening.
    ts = 60.0 / throughput_per_lane_hour          # minutes per passenger per lane
    ca2, cs2 = 4.0, 0.5
    c = max(lanes, 1)
    rho_k = min(rho_mean, 0.95)
    exponent = math.sqrt(2.0 * (c + 1)) - 1.0
    wait_var = ((ca2 + cs2) / 2.0) * (rho_k**exponent / (c * (1.0 - rho_k))) * ts

    if rho_mean >= 1.0:
        # Sustained demand above service capacity: the queue does not reach a
        # steady state within the hour, it grows for as long as the overload
        # lasts. Report the structural problem rather than a fake number.
        wait_sustained = (hourly_pax - capacity) / capacity * 60.0
        base = min(divest_floor_min + wait_surge + wait_var + wait_sustained, MAX_BASE)
        return (
            max(base, MIN_BASE),
            min(0.30 + 0.45 * min(rho_mean, 1.5), MAX_SIG),
            f"Structurally undersized: {hourly_pax:.0f} pax/hr against "
            f"{capacity:.0f} pax/hr of lane capacity. Queue grows without bound "
            f"while the overload lasts.",
        )

    base = divest_floor_min + wait_surge + wait_var
    base = min(max(base, MIN_BASE), MAX_BASE)

    # A busier checkpoint is not merely slower, it is less predictable.
    sig = min(max(0.28 + 0.50 * rho_mean, MIN_SIG), MAX_SIG)

    note = (
        f"Prior from modelled load: {rho_mean:.0%} mean utilisation, "
        f"{queue_peak:.0f} pax peak queue. Unvalidated -- replaced by "
        f"observations once reports arrive."
    )
    return base, sig, note
