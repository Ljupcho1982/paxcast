"""Tests for lane allocation.

The properties tested here are the ones that would silently produce a wrong
roster rather than an obvious crash: an off-by-one in the lane scan, a
smoothing step that quietly drops below the service level, or a service level
that is applied to the median instead of a quantile.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "engine"))

from lanes import (  # noqa: E402
    DEFAULT_THROUGHPUT_PER_LANE_HOUR,
    MAX_LANES,
    build_lane_plan,
    calibration_factor,
    lanes_required,
    predict_wait,
    smooth_profile,
    wait_quantile,
)

TARGET = 15.0
SL = 0.80


# --------------------------------------------------------------------------
# lanes_required
# --------------------------------------------------------------------------


def test_no_demand_needs_no_lanes():
    assert lanes_required(0.0, TARGET, SL) == 0


def test_more_demand_never_needs_fewer_lanes():
    """Monotone in load. A profile that dips as the terminal fills would send
    staff home into a queue."""
    previous = 0
    for pax in range(0, 3000, 50):
        need = lanes_required(float(pax), TARGET, SL)
        assert need >= previous, f"{pax} pax needed fewer lanes than {pax - 50}"
        previous = need


def test_tighter_target_never_needs_fewer_lanes():
    previous = 0
    for target in (60.0, 45.0, 30.0, 20.0, 15.0, 10.0, 5.0):
        need = lanes_required(900.0, target, SL)
        assert need >= previous, f"target {target} needed fewer lanes than a looser one"
        previous = need


def test_higher_service_level_never_needs_fewer_lanes():
    previous = 0
    for sl in (0.50, 0.60, 0.70, 0.80, 0.90, 0.95):
        need = lanes_required(900.0, TARGET, sl)
        assert need >= previous
        previous = need


@pytest.mark.parametrize("pax", [200.0, 600.0, 1200.0, 2000.0])
def test_returned_lane_count_is_the_smallest_that_works(pax):
    """The defining property: n lanes meet the target and n-1 do not.

    This is what catches an off-by-one in the scan, which would look entirely
    reasonable in the output while understaffing every hour by one lane.
    """
    n = lanes_required(pax, TARGET, SL)
    if n in (0, MAX_LANES):
        return  # boundary cases carry no "n-1 fails" guarantee

    base, sig = predict_wait(pax, n, DEFAULT_THROUGHPUT_PER_LANE_HOUR)
    assert wait_quantile(base, sig, SL) <= TARGET

    base_less, sig_less = predict_wait(pax, n - 1, DEFAULT_THROUGHPUT_PER_LANE_HOUR)
    assert wait_quantile(base_less, sig_less, SL) > TARGET


def test_service_level_is_a_quantile_not_the_median():
    """A higher service level must bite. If sizing used `base` directly, the
    service level would be inert and this would fail."""
    lo = lanes_required(1200.0, TARGET, 0.50)
    hi = lanes_required(1200.0, TARGET, 0.95)
    assert hi > lo


def test_impossible_demand_saturates_rather_than_looping():
    assert lanes_required(500_000.0, 1.0, 0.99) == MAX_LANES


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------


def test_unfitted_checkpoint_gets_the_raw_model():
    assert calibration_factor(None, 0, 800.0, 4) == 1.0
    assert calibration_factor(20.0, 0, 800.0, 4) == 1.0


def test_calibration_shifts_lane_count_in_the_right_direction():
    """A checkpoint observed to be slower than modelled needs more lanes."""
    fast = lanes_required(900.0, TARGET, SL, calibration=0.5)
    slow = lanes_required(900.0, TARGET, SL, calibration=2.0)
    assert slow > fast


def test_calibration_factor_is_bounded():
    """A thin or mis-houred sample must not drive the plan to an absurd size."""
    assert calibration_factor(10_000.0, 5, 800.0, 4) <= 4.0
    assert calibration_factor(0.01, 5, 800.0, 4) >= 0.25


# --------------------------------------------------------------------------
# smoothing (the ILP)
# --------------------------------------------------------------------------


def test_smoothing_never_drops_below_requirement():
    required = [1, 1, 6, 2, 7, 1, 8, 2, 1]
    planned = smooth_profile(required, physical_lanes=10, change_penalty=1.0)
    assert all(p >= r for p, r in zip(planned, required))


def test_smoothing_reduces_churn():
    required = [1, 6, 1, 6, 1, 6, 1]
    planned = smooth_profile(required, physical_lanes=8, change_penalty=2.0)
    changes_raw = sum(1 for a, b in zip(required, required[1:]) if a != b)
    changes_new = sum(1 for a, b in zip(planned, planned[1:]) if a != b)
    assert changes_new <= changes_raw


def test_zero_penalty_reproduces_the_minima():
    """With no cost on change, the per-hour minima are already optimal. Guards
    against the smoothing quietly inflating lane-hours for free."""
    required = [1, 4, 2, 6, 3]
    assert smooth_profile(required, 10, change_penalty=0.0) == required


def test_smoothing_respects_physical_lanes():
    planned = smooth_profile([2, 9, 3], physical_lanes=4, change_penalty=1.0)
    assert max(planned) <= 4


def test_smoothing_is_never_worse_than_doing_nothing():
    """The objective at the smoothed profile must beat the raw one."""
    required = [1, 5, 1, 5, 1]
    lam = 1.5
    planned = smooth_profile(required, 8, change_penalty=lam)

    def objective(x):
        return sum(x) + lam * sum(abs(a - b) for a, b in zip(x, x[1:]))

    assert objective(planned) <= objective(required) + 1e-6


def test_empty_profile():
    assert smooth_profile([], 4) == []


# --------------------------------------------------------------------------
# full plan
# --------------------------------------------------------------------------


def test_plan_flags_hours_it_cannot_serve():
    """Open everything and still miss the target -- the most decision-relevant
    thing this can report, so it must never be silent."""
    hourly = [50.0] * 6 + [4000.0] + [50.0] * 17
    plan = build_lane_plan(hourly, physical_lanes=3, target_wait_min=10.0)
    assert 6 in plan.understaffed_hours
    assert max(plan.planned) <= 3


def test_plan_shape_and_peak():
    hourly = [0.0] * 5 + [300.0, 900.0, 1200.0, 600.0] + [200.0] * 15
    plan = build_lane_plan(hourly, physical_lanes=12)
    assert len(plan.planned) == 24
    assert len(plan.required) == 24
    assert plan.peak_hour == 7
    assert plan.lane_hours == sum(plan.planned)
    assert plan.planned[7] >= plan.planned[0]


def test_quiet_hours_need_fewer_lanes_than_the_bank():
    hourly = [20.0] * 24
    hourly[7] = 1500.0
    plan = build_lane_plan(hourly, physical_lanes=20, change_penalty=0.0)
    assert plan.required[7] > plan.required[3]
