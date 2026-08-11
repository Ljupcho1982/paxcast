"""
Lane allocation: how many security lanes to open, hour by hour.

PaxCast already answers "how many passengers, and how wrong might that be".
This module answers the question that follows it -- "so how many lanes do I
open at 07:00?" -- by running the checkpoint queue model backwards.

TWO STAGES, AND ONLY ONE IS AN OPTIMISATION PROBLEM
---------------------------------------------------
1. Sizing one hour is a *search over a single integer*. Wait time falls as
   lanes are added, so the answer is the smallest lane count whose predicted
   wait meets the target. No solver, no metaheuristic -- a scan of at most
   MAX_LANES candidates, exact and microseconds.

2. Sizing the hours *independently* is what makes the answer unusable: it
   produces profiles like 6, 3, 7, 2, 8 that no one can staff. Coupling the
   hours -- "hold the service level everywhere, but change the lane count as
   little as possible" -- is a genuine integer program, and that is the only
   place a solver appears.

SERVICE LEVEL, NOT AVERAGE
--------------------------
The queue model returns a lognormal-ish (base, sig) pair: `base` is the median
wait, `sig` its dispersion. Sizing against the median means missing the target
half the time. So the constraint is on a *quantile*:

    base * exp(z(service_level) * sig) <= target_wait

which makes "hold 15 minutes 80% of the time" directly expressible, and uses
`sig` rather than discarding it.

WHAT THIS INHERITS
------------------
`suggest_prior_from_throughput` says of itself that every constant in it is an
engineering estimate rather than a fitted value. Running it backwards does not
launder that away: an exactly-optimal lane plan over an unvalidated queue model
is still an unvalidated lane plan. Every result therefore carries the basis it
was computed from, and callers must surface it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from calibration import MAX_BASE, suggest_prior_from_throughput

# A checkpoint with more lanes than this is not a checkpoint, it is a terminal.
# Matches the CheckpointCreate bound in routes_data.
MAX_LANES = 60

DEFAULT_THROUGHPUT_PER_LANE_HOUR = 150.0
DEFAULT_TARGET_WAIT_MIN = 15.0
DEFAULT_SERVICE_LEVEL = 0.80

# Cost, in equivalent lane-hours, of changing the lane count between two
# consecutive hours. Opening a lane is not free: staff must be briefed and
# moved, and a profile that churns every hour is ignored in practice. Tunable
# per request.
DEFAULT_CHANGE_PENALTY = 0.75


def _z(p: float) -> float:
    """Standard normal quantile.

    Inlined rather than pulled from scipy.stats: this is called inside the lane
    scan for every hour, and the import cost dwarfs the arithmetic. Abramowitz
    & Stegun 26.2.23, |error| < 4.5e-4 -- far below the precision the queue
    model's own constants justify.
    """
    if p <= 0.0:
        return -8.0
    if p >= 1.0:
        return 8.0
    if p < 0.5:
        return -_z(1.0 - p)
    t = math.sqrt(-2.0 * math.log(1.0 - p))
    return t - ((0.010328 * t + 0.802853) * t + 2.515517) / (
        ((0.001308 * t + 0.189269) * t + 1.432788) * t + 1.0
    )


def wait_quantile(base: float, sig: float, service_level: float) -> float:
    """Wait exceeded only (1 - service_level) of the time."""
    return float(base * math.exp(_z(service_level) * sig))


def predict_wait(
    hourly_pax: float,
    lanes: int,
    throughput_per_lane_hour: float = DEFAULT_THROUGHPUT_PER_LANE_HOUR,
    calibration: float = 1.0,
) -> tuple[float, float]:
    """Predicted (median wait, dispersion) for a load served by `lanes` lanes.

    `calibration` rescales the queue model's level to match what a checkpoint
    has actually been observed to do -- see `calibration_factor`. The model
    still supplies the *shape*: how the wait responds to opening another lane.
    That shape is unvalidated, and extrapolating far from the lane count the
    observations were collected at is the weakest step in this module.
    """
    base, sig, _ = suggest_prior_from_throughput(
        hourly_pax=hourly_pax,
        lanes=max(int(lanes), 1),
        throughput_per_lane_hour=throughput_per_lane_hour,
    )
    return min(base * calibration, MAX_BASE), sig


def calibration_factor(
    fit_base: float | None,
    fit_n: int,
    hourly_pax_at_fit: float,
    lanes_at_fit: int,
    throughput_per_lane_hour: float = DEFAULT_THROUGHPUT_PER_LANE_HOUR,
) -> float:
    """Ratio between a checkpoint's fitted wait and what the model predicts.

    Without this the lane plan ignores every report the checkpoint has ever
    received, which would waste the calibration machinery entirely. With it,
    the plan is anchored to observed reality at the operating point and uses
    the model only to move away from it.

    Returns 1.0 when there is nothing to calibrate against -- an unfitted
    checkpoint gets the raw model, honestly labelled.
    """
    if not fit_base or fit_n <= 0:
        return 1.0
    modelled, _, _ = suggest_prior_from_throughput(
        hourly_pax=hourly_pax_at_fit,
        lanes=max(int(lanes_at_fit), 1),
        throughput_per_lane_hour=throughput_per_lane_hour,
    )
    if modelled <= 0:
        return 1.0
    # Bounded: a wild ratio from a thin or mis-hour-ed sample should not send
    # the plan to zero lanes or to sixty.
    return float(min(max(fit_base / modelled, 0.25), 4.0))


def lanes_required(
    hourly_pax: float,
    target_wait_min: float = DEFAULT_TARGET_WAIT_MIN,
    service_level: float = DEFAULT_SERVICE_LEVEL,
    throughput_per_lane_hour: float = DEFAULT_THROUGHPUT_PER_LANE_HOUR,
    calibration: float = 1.0,
    max_lanes: int = MAX_LANES,
) -> int:
    """Fewest lanes whose predicted wait quantile meets the target.

    A linear scan rather than a bisection. Bisection assumes the wait is
    monotone in lane count, which it should be but which the queue model does
    not *prove* -- it is a piecewise expression with a Kingman term and a
    regime change at rho = 1. A scan of at most 60 candidates costs
    microseconds and is correct whether or not a kink exists, so the
    assumption is simply not made.

    Returns max_lanes when even that cannot meet the target; the caller is
    expected to notice and say so rather than present the number as adequate.
    """
    if hourly_pax <= 0:
        return 0
    for lanes in range(1, int(max_lanes) + 1):
        base, sig = predict_wait(
            hourly_pax, lanes, throughput_per_lane_hour, calibration
        )
        if wait_quantile(base, sig, service_level) <= target_wait_min:
            return lanes
    return int(max_lanes)


def smooth_profile(
    required: list[int],
    physical_lanes: int,
    change_penalty: float = DEFAULT_CHANGE_PENALTY,
) -> list[int]:
    """Cheapest implementable profile that never drops below `required`.

        minimise  sum_h x_h + lambda * sum_h |x_h - x_{h-1}|
        s.t.      x_h >= required_h,  x_h <= physical_lanes,  x integer

    The absolute value is linearised with an auxiliary d_h >= |x_h - x_{h-1}|,
    which is exact at the optimum because the objective pushes d down.

    24 integer variables and 23 auxiliaries: milliseconds via HiGHS through
    scipy.optimize.milp, and scipy is already a dependency.

    A dynamic program over (hour, lane count) would solve this identically and
    without a solver. The ILP is used because the next feature -- several
    checkpoints drawing on one pool of officers -- adds a coupling constraint
    that the DP's state space cannot absorb, and this formulation extends to it
    by adding rows.
    """
    n = len(required)
    if n == 0:
        return []
    cap = max(int(physical_lanes), 1)
    # Infeasible hours are clamped; the caller reports them rather than the
    # solver failing and taking the whole plan down with it.
    req = [min(max(int(r), 0), cap) for r in required]

    if change_penalty <= 0:
        # No smoothing requested: the per-hour minima already are the optimum.
        return req

    from scipy.optimize import Bounds, LinearConstraint, milp

    n_d = n - 1
    n_var = n + n_d

    c = np.concatenate([np.ones(n), np.full(n_d, float(change_penalty))])

    rows, lb = [], []
    for h in range(n_d):
        # d_h - x_{h+1} + x_h >= 0
        r1 = np.zeros(n_var)
        r1[n + h] = 1.0
        r1[h + 1] = -1.0
        r1[h] = 1.0
        # d_h + x_{h+1} - x_h >= 0
        r2 = np.zeros(n_var)
        r2[n + h] = 1.0
        r2[h + 1] = 1.0
        r2[h] = -1.0
        rows += [r1, r2]
        lb += [0.0, 0.0]

    lo = np.concatenate([np.array(req, dtype=float), np.zeros(n_d)])
    hi = np.concatenate([np.full(n, float(cap)), np.full(n_d, float(cap))])

    res = milp(
        c=c,
        constraints=LinearConstraint(np.array(rows), lb=np.array(lb), ub=np.inf),
        integrality=np.concatenate([np.ones(n), np.zeros(n_d)]),
        bounds=Bounds(lo, hi),
    )
    if not res.success or res.x is None:
        # Never fail the request over the smoothing step -- the unsmoothed
        # minima are a correct, if jagged, answer.
        return req
    return [int(round(v)) for v in res.x[:n]]


@dataclass
class LanePlan:
    hours: list[int]
    required: list[int]
    planned: list[int]
    lane_hours: int
    peak_hour: int
    understaffed_hours: list[int] = field(default_factory=list)
    basis: str = "prior"
    fit_n: int = 0

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)


def build_lane_plan(
    hourly_pax: list[float],
    physical_lanes: int,
    target_wait_min: float = DEFAULT_TARGET_WAIT_MIN,
    service_level: float = DEFAULT_SERVICE_LEVEL,
    throughput_per_lane_hour: float = DEFAULT_THROUGHPUT_PER_LANE_HOUR,
    change_penalty: float = DEFAULT_CHANGE_PENALTY,
    calibration: float = 1.0,
    basis: str = "prior",
    fit_n: int = 0,
) -> LanePlan:
    """Full plan for one day: per-hour minima, then a smoothed profile."""
    required = [
        lanes_required(
            pax, target_wait_min, service_level, throughput_per_lane_hour,
            calibration, max_lanes=MAX_LANES,
        )
        for pax in hourly_pax
    ]
    # Hours the checkpoint physically cannot serve at the requested standard.
    # Reported, not hidden: "open everything and still miss the target" is the
    # single most decision-relevant thing this endpoint can say.
    understaffed = [h for h, r in enumerate(required) if r > physical_lanes]

    planned = smooth_profile(required, physical_lanes, change_penalty)
    peak = int(np.argmax(hourly_pax)) if hourly_pax else 0

    return LanePlan(
        hours=list(range(len(hourly_pax))),
        required=required,
        planned=planned,
        lane_hours=int(sum(planned)),
        peak_hour=peak,
        understaffed_hours=understaffed,
        basis=basis,
        fit_n=fit_n,
    )
