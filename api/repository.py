"""
Repository layer: the boundary between stored rows and engine domain objects.

The forecasting engine works with `Airport`/`Flight` dataclasses and knows
nothing about SQLAlchemy. This module owns the translation in both directions,
so the engine stays a pure library and the storage schema can change without
touching the simulation.

It also owns two pieces of behaviour that do not belong in either layer:

  * **Seeding.** The hardcoded catalogue is imported once into the database on
    first run, tagged `source="seed"`, after which it is just data like anything
    a user adds.
  * **Recalibration.** Adding flights or observations invalidates cached
    forecasts and fitted checkpoint parameters. Those recomputations are
    triggered here rather than being left to call sites to remember.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from paxcast.models import Airport, CarrierType, Direction, Flight  # noqa: E402
from paxcast.seed import CATALOGUE, build_airport  # noqa: E402

from calibration import (  # noqa: E402
    LANE_PRIORS,
    Observation,
    confidence_for_fit,
    fit_checkpoint,
)
from db import AirportRow, CheckpointRow, FlightRow, ObservationRow, SessionLocal, init_db  # noqa: E402


# ---------------------------------------------------------------------------
# Row -> domain
# ---------------------------------------------------------------------------


def row_to_airport(row: AirportRow) -> Airport:
    """Build the engine's Airport from stored rows.

    Seasonality is regenerated rather than stored: it is a deterministic
    function of how leisure-weighted the airport is, and storing 53 floats per
    airport to hold a derived curve invites them drifting out of sync with the
    generator.
    """
    flights = [
        Flight(
            flight_no=f.flight_no,
            carrier=f.carrier,
            carrier_type=CarrierType(f.carrier_type),
            other_endpoint=f.other_endpoint,
            direction=Direction(f.direction),
            seats=f.seats,
            sched_minute=f.sched_minute,
            dow_mask=f.dow_mask,
            transfer_share=f.transfer_share,
            reliability=f.reliability,
        )
        for f in row.flights
    ]

    hub = _infer_hub_strength(flights)
    amp = 0.10 + 0.22 * (1.0 - hub)
    w = np.arange(53)
    seasonality = (1.0 + amp * np.sin(2 * np.pi * (w - 12) / 53.0)).astype(np.float32)

    airport = Airport(
        iata=row.iata,
        icao=row.icao,
        name=row.name,
        city=row.city,
        country=row.country,
        lat=row.lat,
        lon=row.lon,
        climate=row.climate,
        timezone=row.timezone,
        terminal_capacity_hourly=row.terminal_capacity_hourly,
        annual_pax_baseline=row.annual_pax_baseline,
        seasonality=seasonality,
        data_quality=row.data_quality,
        flights=flights,
    )
    airport.calibration_factor = solve_calibration(airport)
    return airport


def _infer_hub_strength(flights: list[Flight]) -> float:
    """Mean transfer share, which is what hub-ness means operationally."""
    if not flights:
        return 0.1
    return float(np.clip(np.mean([f.transfer_share for f in flights]), 0.0, 1.0))


def solve_calibration(airport: Airport) -> float:
    """Anchor the schedule to published annual throughput.

    Same closed-form solve as `seed.solve_calibration`, duplicated here because
    a user-supplied schedule needs anchoring too -- and a user is far more
    likely to enter a partial schedule than a complete one, which is exactly
    when the anchor matters most.
    """
    from paxcast.distributions import CARRIER_LF_PRIORS

    if not airport.annual_pax_baseline or not airport.flights:
        return 1.0
    expected = 0.0
    for f in airport.flights:
        lf_mean, _ = CARRIER_LF_PRIORS[f.carrier_type.value]
        expected += f.seats * bin(f.dow_mask).count("1") * lf_mean * f.reliability
    expected *= 52.0 * 0.955
    if airport.seasonality is not None:
        expected *= float(np.mean(airport.seasonality))
    if expected <= 0:
        return 1.0
    factor = airport.annual_pax_baseline / expected
    # A factor far from 1 means the stored schedule covers only a fraction of
    # real traffic. Scaling by 12x would be arithmetically valid and completely
    # misleading, so it is capped and the caller can surface the shortfall.
    return float(np.clip(factor, 0.2, 5.0))


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def seed_if_empty(session: Session) -> int:
    """Import the built-in catalogue on first run. Idempotent."""
    existing = session.scalar(select(func.count()).select_from(AirportRow))
    if existing:
        return 0

    added = 0
    for spec in CATALOGUE:
        built = build_airport(spec)
        row = AirportRow(
            iata=built.iata,
            icao=built.icao,
            name=built.name,
            city=built.city,
            country=built.country,
            lat=built.lat,
            lon=built.lon,
            climate=built.climate,
            timezone=built.timezone,
            terminal_capacity_hourly=built.terminal_capacity_hourly,
            annual_pax_baseline=built.annual_pax_baseline,
            source="seed",
            data_quality=built.data_quality,
            verified=True,
        )
        seen: set[tuple[str, str, int]] = set()
        for f in built.flights:
            key = (f.flight_no, f.direction.value, f.sched_minute)
            if key in seen:
                continue
            seen.add(key)
            row.flights.append(
                FlightRow(
                    flight_no=f.flight_no,
                    carrier=f.carrier,
                    carrier_type=f.carrier_type.value,
                    other_endpoint=f.other_endpoint,
                    direction=f.direction.value,
                    seats=f.seats,
                    sched_minute=f.sched_minute,
                    dow_mask=f.dow_mask,
                    transfer_share=f.transfer_share,
                    reliability=f.reliability,
                    source="seed",
                )
            )
        session.add(row)
        added += 1

    session.commit()
    return added


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def get_airport_row(session: Session, iata: str) -> AirportRow | None:
    return session.scalar(select(AirportRow).where(AirportRow.iata == iata.upper()))


def load_airport(session: Session, iata: str) -> Airport | None:
    row = get_airport_row(session, iata)
    return row_to_airport(row) if row else None


def list_airport_rows(session: Session, q: str | None = None, limit: int = 100):
    stmt = select(AirportRow)
    if q:
        needle = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            func.lower(AirportRow.iata).like(needle)
            | func.lower(AirportRow.icao).like(needle)
            | func.lower(AirportRow.name).like(needle)
            | func.lower(AirportRow.city).like(needle)
            | func.lower(AirportRow.country).like(needle)
        )
    return list(session.scalars(stmt.order_by(AirportRow.iata).limit(limit)))


# ---------------------------------------------------------------------------
# Checkpoint calibration
# ---------------------------------------------------------------------------


def recalibrate_checkpoint(session: Session, cp: CheckpointRow) -> dict:
    """Refit a checkpoint from its observations and persist the result."""
    rows = list(
        session.scalars(
            select(ObservationRow).where(ObservationRow.checkpoint_id == cp.id)
        )
    )
    obs = [
        Observation(
            wait_minutes=r.wait_minutes,
            hour_local=r.hour_local,
            observed_at=r.observed_at,
            source=r.source,
            weight=r.weight,
        )
        for r in rows
    ]
    fit = fit_checkpoint(
        obs,
        lane_type=cp.lane_type,
        prior_base=cp.prior_base,
        prior_sig=cp.prior_sig,
    )
    cp.fit_base = fit.base
    cp.fit_sig = fit.sig
    cp.fit_n = fit.n_used
    cp.fit_updated_at = datetime.now(timezone.utc)
    session.commit()

    return {
        "base": round(fit.base, 2),
        "sig": round(fit.sig, 4),
        "n_observations": fit.n_used,
        "effective_n": round(fit.effective_n, 2),
        "shrinkage_mu": round(fit.shrinkage_mu, 3),
        "shrinkage_sig": round(fit.shrinkage_sig, 3),
        "prior_base": round(fit.prior_base, 2),
        "prior_sig": round(fit.prior_sig, 4),
        "confidence": confidence_for_fit(fit),
        "note": fit.note,
    }


def default_prior_for_lane(lane_type: str) -> tuple[float, float]:
    return LANE_PRIORS.get(lane_type, LANE_PRIORS["standard"])


def bootstrap() -> int:
    init_db()
    with SessionLocal() as session:
        return seed_if_empty(session)
