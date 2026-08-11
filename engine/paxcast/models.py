"""Domain model for PaxCast: airports, flights, scenarios, results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

import numpy as np


class CarrierType(str, Enum):
    LCC = "LCC"
    FSC_SHORT = "FSC_SHORT"
    FSC_LONG = "FSC_LONG"
    REGIONAL = "REGIONAL"
    CHARTER = "CHARTER"


class Direction(str, Enum):
    ARRIVAL = "ARR"
    DEPARTURE = "DEP"


@dataclass(slots=True)
class Flight:
    """One scheduled flight leg on one day of the week."""

    flight_no: str
    carrier: str
    carrier_type: CarrierType
    other_endpoint: str          # IATA code of the other airport
    direction: Direction
    seats: int
    sched_minute: int            # minutes past local midnight
    dow_mask: int = 0b1111111    # bit per weekday, Monday = bit 0
    transfer_share: float = 0.15  # fraction of pax connecting rather than O&D
    reliability: float = 0.985    # carrier-specific completion factor, benign wx

    def operates_on(self, weekday: int) -> bool:
        return bool(self.dow_mask & (1 << weekday))


@dataclass(slots=True)
class Airport:
    """An airport and everything the engine needs to model it."""

    iata: str
    icao: str
    name: str
    city: str
    country: str
    lat: float
    lon: float
    climate: str = "temperate"
    timezone: str = "UTC"
    # declared hourly terminal capacity in passengers; used for exceedance probs
    terminal_capacity_hourly: int = 3000
    annual_pax_baseline: int | None = None
    # week-of-year multiplier, length 53; peak summer >1, deep winter <1
    seasonality: np.ndarray | None = None
    # data-quality score in [0,1] driving the model-confidence badge
    data_quality: float = 0.7
    # multiplicative anchor so simulated annual throughput reconciles with the
    # published ACI/Eurostat baseline; solved once at build time, see seed.py
    calibration_factor: float = 1.0
    flights: list[Flight] = field(default_factory=list)

    def weekly_seats(self) -> int:
        return sum(f.seats * bin(f.dow_mask).count("1") for f in self.flights)

    def daily_flights(self, weekday: int) -> list[Flight]:
        return [f for f in self.flights if f.operates_on(weekday)]


@dataclass(slots=True)
class Scenario:
    """User-defined perturbation of the baseline model.

    This is the whole reason for choosing simulation over a fitted point
    forecast: a scenario is a re-parameterisation, not a new model.
    """

    name: str = "Baseline"
    load_factor_delta: float = 0.0        # additive shift in mean LF, e.g. -0.05
    capacity_multiplier: float = 1.0      # seats scaled, e.g. runway works -> 0.7
    demand_multiplier: float = 1.0        # exogenous demand shift
    grounded_carriers: tuple[str, ...] = ()
    closed_routes: tuple[str, ...] = ()   # IATA codes of suspended endpoints
    extra_cancel_prob: float = 0.0        # e.g. ATC strike adds flat 0.20
    disable_shocks: bool = False

    def is_baseline(self) -> bool:
        return (
            self.load_factor_delta == 0.0
            and self.capacity_multiplier == 1.0
            and self.demand_multiplier == 1.0
            and not self.grounded_carriers
            and not self.closed_routes
            and self.extra_cancel_prob == 0.0
            and not self.disable_shocks
        )


@dataclass(slots=True)
class SimulationConfig:
    start_date: date
    horizon_days: int = 30
    n_iterations: int = 10_000
    seed: int | None = 42
    use_lhs: bool = True                  # Latin hypercube on the low-dim factors
    use_antithetic: bool = True
    adaptive: bool = True                 # stop early once P90 SE target is met
    p90_se_target: float = 0.005          # 0.5% relative standard error
    min_iterations: int = 1_000
    batch_size: int = 1_000


@dataclass(slots=True)
class ForecastResult:
    """Output of a simulation run: percentile bands plus diagnostics."""

    iata: str
    scenario: str
    dates: list[str]
    percentiles: dict[str, list[float]]   # "p5" -> per-day values
    mean: list[float]
    total_percentiles: dict[str, float]   # horizon totals
    peak_hour_grid: list[list[float]]     # (7 weekdays x 24 hours) mean pax
    exceedance: dict[str, float]
    n_iterations: int
    converged: bool
    p90_rel_se: float
    runtime_ms: float
    data_quality: float
    confidence: str

    # Hourly load with uncertainty attached, same (7 x 24) shape as
    # peak_hour_grid. Staffing a checkpoint against the mean hour is the single
    # -number thinking this product exists to argue against, so lane sizing
    # reads these rather than the grid.
    #
    # These describe the *average* occurrence of a given weekday-hour over the
    # horizon, matching peak_hour_grid's semantics. For horizons of a week or
    # less -- the operational planning case -- each weekday occurs once and the
    # quantity is exact. Over longer horizons it smooths across repeats and
    # will understate the worst single Tuesday.
    peak_hour_p50: list[list[float]] = field(default_factory=list)
    peak_hour_p90: list[list[float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)
