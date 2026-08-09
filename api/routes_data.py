"""
Write endpoints: adding airports, schedules, checkpoints and observations.

Validation philosophy here is that a rejected submission with a specific reason
is far more useful than a silently accepted bad one. A forecasting product
lives or dies on input quality, and the failure mode of user-contributed data
is not malice -- it is a mistyped IATA code, a duplicated CSV import, or a
timestamp in the wrong timezone. Each of those gets its own check.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from db import AirportRow, CheckpointRow, FlightRow, ObservationRow, SessionLocal
from repository import (
    default_prior_for_lane,
    get_airport_row,
    recalibrate_checkpoint,
    row_to_airport,
    solve_calibration,
)

router = APIRouter(tags=["data"])

IATA_RE = re.compile(r"^[A-Z]{3}$")
ICAO_RE = re.compile(r"^[A-Z]{4}$")
VALID_CARRIER_TYPES = {"LCC", "FSC_SHORT", "FSC_LONG", "REGIONAL", "CHARTER"}
VALID_CLIMATES = {"mild", "temperate", "harsh_winter", "monsoon"}
VALID_LANES = {"standard", "expedited", "premium"}


def get_session():
    with SessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AirportCreate(BaseModel):
    iata: str
    icao: str = ""
    name: str = Field(min_length=2, max_length=160)
    city: str = ""
    country: str = ""
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    climate: str = "temperate"
    timezone: str = "UTC"
    terminal_capacity_hourly: int = Field(3000, gt=0, le=100_000)
    annual_pax_baseline: int | None = Field(None, ge=0, le=300_000_000)

    @field_validator("iata")
    @classmethod
    def _iata(cls, v: str) -> str:
        v = v.strip().upper()
        if not IATA_RE.match(v):
            raise ValueError("IATA code must be exactly three letters, e.g. SKP")
        return v

    @field_validator("icao")
    @classmethod
    def _icao(cls, v: str) -> str:
        v = v.strip().upper()
        if v and not ICAO_RE.match(v):
            raise ValueError("ICAO code must be exactly four letters, e.g. LWSK")
        return v

    @field_validator("climate")
    @classmethod
    def _climate(cls, v: str) -> str:
        if v not in VALID_CLIMATES:
            raise ValueError(f"climate must be one of {sorted(VALID_CLIMATES)}")
        return v


class AirportUpdate(BaseModel):
    name: str | None = None
    city: str | None = None
    country: str | None = None
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    climate: str | None = None
    timezone: str | None = None
    terminal_capacity_hourly: int | None = Field(None, gt=0, le=100_000)
    annual_pax_baseline: int | None = Field(None, ge=0, le=300_000_000)


class FlightCreate(BaseModel):
    flight_no: str = Field(min_length=2, max_length=10)
    carrier: str = Field(min_length=2, max_length=3)
    carrier_type: str = "FSC_SHORT"
    other_endpoint: str = ""
    direction: str = "DEP"
    seats: int = Field(180, gt=0, le=900)
    sched_minute: int = Field(480, ge=0, lt=1440)
    dow_mask: int = Field(0b1111111, gt=0, le=127)
    transfer_share: float = Field(0.15, ge=0, le=1)
    reliability: float = Field(0.985, gt=0, le=1)

    @field_validator("carrier_type")
    @classmethod
    def _ct(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in VALID_CARRIER_TYPES:
            raise ValueError(f"carrier_type must be one of {sorted(VALID_CARRIER_TYPES)}")
        return v

    @field_validator("direction")
    @classmethod
    def _dir(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in {"ARR", "DEP"}:
            raise ValueError("direction must be ARR or DEP")
        return v


class CheckpointCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    zone: str = ""
    lane_type: str = "standard"
    lanes: int = Field(4, gt=0, le=60)
    prior_base: float | None = Field(None, gt=0, le=180)
    prior_sig: float | None = Field(None, gt=0, lt=3)

    @field_validator("lane_type")
    @classmethod
    def _lane(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VALID_LANES:
            raise ValueError(f"lane_type must be one of {sorted(VALID_LANES)}")
        return v


class ObservationCreate(BaseModel):
    wait_minutes: float = Field(gt=0, le=480)
    observed_at: datetime | None = None
    source: str = "user"

    @field_validator("source")
    @classmethod
    def _src(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"user", "operator", "sensor"}:
            raise ValueError("source must be user, operator or sensor")
        return v


# ---------------------------------------------------------------------------
# Airports
# ---------------------------------------------------------------------------


@router.post("/airports", status_code=201)
def create_airport(payload: AirportCreate, session: Session = Depends(get_session)) -> dict:
    if get_airport_row(session, payload.iata):
        raise HTTPException(409, f"Airport {payload.iata} already exists")

    # Coordinates at the null island are almost always an unfilled form rather
    # than a genuine position in the Gulf of Guinea.
    if abs(payload.lat) < 0.01 and abs(payload.lon) < 0.01:
        raise HTTPException(422, "Coordinates look unset (0, 0). Provide a real position.")

    near = session.scalars(
        select(AirportRow).where(
            func.abs(AirportRow.lat - payload.lat) < 0.05,
            func.abs(AirportRow.lon - payload.lon) < 0.05,
        )
    ).first()
    if near:
        raise HTTPException(
            409,
            f"An airport already exists within ~5 km of that position: "
            f"{near.iata} ({near.name}). Edit it instead of adding a duplicate.",
        )

    row = AirportRow(
        **payload.model_dump(),
        source="user",
        # A brand-new airport with no schedule cannot be forecast well, and the
        # confidence badge must say so from the start.
        data_quality=0.35,
        verified=False,
    )
    session.add(row)
    session.commit()
    return _airport_detail(session, row)


@router.patch("/airports/{iata}")
def update_airport(
    iata: str, payload: AirportUpdate, session: Session = Depends(get_session)
) -> dict:
    row = get_airport_row(session, iata)
    if not row:
        raise HTTPException(404, f"Unknown airport: {iata}")
    if row.source == "seed":
        raise HTTPException(
            409,
            "Built-in catalogue airports are read-only. Copy it to a new IATA "
            "code if you need a variant.",
        )
    data = payload.model_dump(exclude_none=True)
    if "climate" in data and data["climate"] not in VALID_CLIMATES:
        raise HTTPException(422, f"climate must be one of {sorted(VALID_CLIMATES)}")
    for k, v in data.items():
        setattr(row, k, v)
    session.commit()
    return _airport_detail(session, row)


@router.delete("/airports/{iata}", status_code=204)
def delete_airport(iata: str, session: Session = Depends(get_session)) -> None:
    row = get_airport_row(session, iata)
    if not row:
        raise HTTPException(404, f"Unknown airport: {iata}")
    if row.source == "seed":
        raise HTTPException(409, "Built-in catalogue airports cannot be deleted.")
    session.delete(row)
    session.commit()


# ---------------------------------------------------------------------------
# Flights
# ---------------------------------------------------------------------------


@router.post("/airports/{iata}/flights", status_code=201)
def add_flights(
    iata: str, payload: list[FlightCreate], session: Session = Depends(get_session)
) -> dict:
    row = get_airport_row(session, iata)
    if not row:
        raise HTTPException(404, f"Unknown airport: {iata}")
    if not payload:
        raise HTTPException(422, "No flights supplied")

    existing = {(f.flight_no, f.direction, f.sched_minute) for f in row.flights}
    added, skipped = 0, 0
    for f in payload:
        key = (f.flight_no.upper(), f.direction, f.sched_minute)
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        row.flights.append(
            FlightRow(
                **{**f.model_dump(), "flight_no": f.flight_no.upper(),
                   "carrier": f.carrier.upper(),
                   "other_endpoint": f.other_endpoint.upper()},
                source="user",
            )
        )
        added += 1

    _bump_quality(row)
    session.commit()
    return {
        "iata": row.iata,
        "added": added,
        "skipped_duplicates": skipped,
        "total_flights": len(row.flights),
        **_calibration_report(row),
    }


@router.post("/airports/{iata}/flights/csv", status_code=201)
def import_flights_csv(
    iata: str, body: dict, session: Session = Depends(get_session)
) -> dict:
    """Bulk import from CSV text.

    Expected header:
        flight_no,carrier,carrier_type,other_endpoint,direction,seats,sched_time,dow

    `sched_time` accepts HH:MM or minutes-past-midnight. `dow` accepts either a
    7-bit mask or a string like "1234567"/"Mo,Tu,We", because schedule exports
    in the wild use all of these and rejecting a file over its day format
    wastes everyone's time.
    """
    row = get_airport_row(session, iata)
    if not row:
        raise HTTPException(404, f"Unknown airport: {iata}")

    text = body.get("csv", "")
    if not text.strip():
        raise HTTPException(422, "Empty CSV body")

    reader = csv.DictReader(io.StringIO(text), restkey="_overflow", restval="")
    if not reader.fieldnames:
        raise HTTPException(422, "CSV has no header row")

    required = {"flight_no", "carrier", "direction", "seats"}
    missing = required - {(f or "").strip().lower() for f in reader.fieldnames}
    if missing:
        raise HTTPException(422, f"CSV missing required columns: {sorted(missing)}")

    existing = {(f.flight_no, f.direction, f.sched_minute) for f in row.flights}
    added, skipped, errors = 0, 0, []

    for i, raw in enumerate(reader, start=2):
        # A row with more fields than the header means an unquoted comma
        # somewhere -- very common in exported schedules, where day lists get
        # written as Mo,Tu,We. Reject the row with a specific message rather
        # than guessing which column overflowed: guessing could silently shift
        # a seat count into the time column, which is far worse than a
        # rejection the user can act on.
        if raw.get("_overflow"):
            errors.append({
                "line": i,
                "error": "Row has more fields than the header. A value probably "
                         "contains an unquoted comma -- wrap it in double quotes, "
                         'e.g. "Mo,Tu,We".',
            })
            continue

        rec = {}
        for k, v in raw.items():
            if k is None:
                continue
            if isinstance(v, list):
                v = ",".join(str(x) for x in v)
            rec[k.strip().lower()] = (v or "").strip()

        missing_vals = [c for c in ("flight_no", "carrier", "direction", "seats") if not rec.get(c)]
        if missing_vals:
            errors.append({"line": i, "error": f"Empty required field(s): {missing_vals}"})
            continue

        try:
            flight = FlightCreate(
                flight_no=rec["flight_no"].upper(),
                carrier=rec["carrier"].upper(),
                carrier_type=(rec.get("carrier_type") or "FSC_SHORT").upper(),
                other_endpoint=(rec.get("other_endpoint") or "").upper(),
                direction=rec["direction"].upper(),
                seats=int(float(rec["seats"])),
                sched_minute=_parse_time(rec.get("sched_time") or "08:00"),
                dow_mask=_parse_dow(rec.get("dow") or "127"),
            )
        except HTTPException:
            raise
        except Exception as exc:
            errors.append({"line": i, "error": str(exc)[:180]})
            continue

        key = (flight.flight_no, flight.direction, flight.sched_minute)
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        row.flights.append(FlightRow(**flight.model_dump(), source="import"))
        added += 1

    if added:
        _bump_quality(row)
    session.commit()
    return {
        "iata": row.iata,
        "added": added,
        "skipped_duplicates": skipped,
        "rejected": len(errors),
        "errors": errors[:25],
        "total_flights": len(row.flights),
        **_calibration_report(row),
    }


@router.delete("/airports/{iata}/flights", status_code=200)
def clear_flights(
    iata: str,
    source: str | None = Query(None, description="Only delete rows from this source"),
    session: Session = Depends(get_session),
) -> dict:
    row = get_airport_row(session, iata)
    if not row:
        raise HTTPException(404, f"Unknown airport: {iata}")
    stmt = delete(FlightRow).where(FlightRow.airport_id == row.id)
    if source:
        stmt = stmt.where(FlightRow.source == source)
    removed = session.execute(stmt).rowcount
    session.commit()
    session.refresh(row)
    return {"iata": row.iata, "removed": removed, "remaining": len(row.flights)}


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------


@router.post("/airports/{iata}/checkpoints", status_code=201)
def add_checkpoint(
    iata: str, payload: CheckpointCreate, session: Session = Depends(get_session)
) -> dict:
    row = get_airport_row(session, iata)
    if not row:
        raise HTTPException(404, f"Unknown airport: {iata}")
    if any(c.name.lower() == payload.name.lower() for c in row.checkpoints):
        raise HTTPException(409, f"{row.iata} already has a checkpoint named {payload.name}")

    base, sig = payload.prior_base, payload.prior_sig
    prior_note = "Lane-type prior."

    if base is None or sig is None:
        # Prefer a prior derived from this airport's own modelled peak load over
        # a generic lane-type constant -- it is the one piece of information we
        # actually have about a brand-new checkpoint.
        derived = _prior_from_engine(session, row, payload.lanes)
        if derived:
            base, sig, prior_note = derived
        else:
            d_base, d_sig = default_prior_for_lane(payload.lane_type)
            base, sig = base or d_base, sig or d_sig

    cp = CheckpointRow(
        airport_id=row.id,
        name=payload.name,
        zone=payload.zone,
        lane_type=payload.lane_type,
        prior_base=round(float(base), 3),
        prior_sig=round(float(sig), 4),
    )
    session.add(cp)
    session.commit()
    return {
        "id": cp.id,
        "airport": row.iata,
        "name": cp.name,
        "zone": cp.zone,
        "lane_type": cp.lane_type,
        "prior_base": cp.prior_base,
        "prior_sig": cp.prior_sig,
        "prior_source": prior_note,
        "fit_n": 0,
        "confidence": "LOW",
    }


@router.get("/airports/{iata}/checkpoints")
def list_checkpoints(iata: str, session: Session = Depends(get_session)) -> dict:
    row = get_airport_row(session, iata)
    if not row:
        raise HTTPException(404, f"Unknown airport: {iata}")
    return {
        "iata": row.iata,
        "checkpoints": [
            {
                "id": c.id,
                "name": c.name,
                "zone": c.zone,
                "lane_type": c.lane_type,
                "base": round(c.effective_base, 2),
                "sig": round(c.effective_sig, 4),
                "prior_base": c.prior_base,
                "prior_sig": c.prior_sig,
                "fit_n": c.fit_n,
                "fitted": c.fit_base is not None,
                "fit_updated_at": c.fit_updated_at.isoformat() if c.fit_updated_at else None,
            }
            for c in row.checkpoints
            if c.active
        ],
    }


@router.delete("/checkpoints/{cp_id}", status_code=204)
def delete_checkpoint(cp_id: int, session: Session = Depends(get_session)) -> None:
    cp = session.get(CheckpointRow, cp_id)
    if not cp:
        raise HTTPException(404, f"Unknown checkpoint: {cp_id}")
    session.delete(cp)
    session.commit()


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


@router.post("/checkpoints/{cp_id}/observations", status_code=201)
def add_observation(
    cp_id: int, payload: ObservationCreate, session: Session = Depends(get_session)
) -> dict:
    cp = session.get(CheckpointRow, cp_id)
    if not cp:
        raise HTTPException(404, f"Unknown checkpoint: {cp_id}")

    when = payload.observed_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if when > now + timedelta(minutes=5):
        raise HTTPException(422, "Observation timestamp is in the future.")
    if when < now - timedelta(days=365):
        raise HTTPException(422, "Observation is over a year old and would carry no weight.")

    session.add(
        ObservationRow(
            checkpoint_id=cp.id,
            wait_minutes=payload.wait_minutes,
            observed_at=when,
            hour_local=when.hour,
            weekday=when.weekday(),
            source=payload.source,
        )
    )
    session.commit()

    # Refit immediately. The alternative -- a nightly batch -- means a user who
    # reports a 50-minute queue sees the old forecast when they check back,
    # which reads as the app ignoring them.
    fit = recalibrate_checkpoint(session, cp)
    return {"checkpoint_id": cp.id, "recorded": True, "fit": fit}


@router.get("/checkpoints/{cp_id}/observations")
def list_observations(
    cp_id: int, limit: int = Query(100, ge=1, le=1000), session: Session = Depends(get_session)
) -> dict:
    cp = session.get(CheckpointRow, cp_id)
    if not cp:
        raise HTTPException(404, f"Unknown checkpoint: {cp_id}")
    rows = session.scalars(
        select(ObservationRow)
        .where(ObservationRow.checkpoint_id == cp_id)
        .order_by(ObservationRow.observed_at.desc())
        .limit(limit)
    )
    return {
        "checkpoint_id": cp_id,
        "observations": [
            {
                "id": r.id,
                "wait_minutes": r.wait_minutes,
                "observed_at": r.observed_at.isoformat(),
                "hour_local": r.hour_local,
                "weekday": r.weekday,
                "source": r.source,
            }
            for r in rows
        ],
    }


@router.post("/checkpoints/{cp_id}/recalibrate")
def force_recalibrate(cp_id: int, session: Session = Depends(get_session)) -> dict:
    cp = session.get(CheckpointRow, cp_id)
    if not cp:
        raise HTTPException(404, f"Unknown checkpoint: {cp_id}")
    return {"checkpoint_id": cp_id, "fit": recalibrate_checkpoint(session, cp)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _airport_detail(session: Session, row: AirportRow) -> dict:
    """Summary returned after any write, so the client sees the new state."""
    session.refresh(row)
    return {
        "iata": row.iata,
        "icao": row.icao,
        "name": row.name,
        "city": row.city,
        "country": row.country,
        "lat": row.lat,
        "lon": row.lon,
        "climate": row.climate,
        "timezone": row.timezone,
        "terminal_capacity_hourly": row.terminal_capacity_hourly,
        "annual_pax_baseline": row.annual_pax_baseline,
        "source": row.source,
        "verified": row.verified,
        "data_quality": row.data_quality,
        "flights": len(row.flights),
        "checkpoints": len(row.checkpoints),
        "forecastable": len(row.flights) > 0,
    }


def _parse_time(value: str) -> int:
    value = value.strip()
    if ":" in value:
        h, _, m = value.partition(":")
        minute = int(h) * 60 + int(m[:2])
    else:
        minute = int(float(value))
    if not 0 <= minute < 1440:
        raise ValueError(f"time out of range: {value}")
    return minute


DOW_TOKENS = {
    "mo": 0, "mon": 0, "tu": 1, "tue": 1, "we": 2, "wed": 2, "th": 3, "thu": 3,
    "fr": 4, "fri": 4, "sa": 5, "sat": 5, "su": 6, "sun": 6,
}


def _parse_dow(value: str) -> int:
    """Accept a bitmask, a digit string (IATA 1=Mon..7=Sun), or day names."""
    v = value.strip()
    if not v:
        return 0b1111111
    if v.isdigit():
        n = int(v)
        # A bare 1-127 is ambiguous: "127" is a valid mask and also a valid
        # Mon/Tue/Sun digit string. Length disambiguates -- masks are written as
        # plain integers, day strings as sequences of 1-7 with no zeroes.
        if len(v) <= 3 and 0 < n <= 127 and "0" in v:
            return n
        if all(c in "1234567" for c in v):
            mask = 0
            for c in v:
                mask |= 1 << (int(c) - 1)
            return mask
        if 0 < n <= 127:
            return n
        raise ValueError(f"unparseable dow: {value}")
    mask = 0
    for tok in re.split(r"[,\s/]+", v.lower()):
        if tok in DOW_TOKENS:
            mask |= 1 << DOW_TOKENS[tok]
    if mask == 0:
        raise ValueError(f"unparseable dow: {value}")
    return mask


def _bump_quality(row: AirportRow) -> None:
    """Data quality rises with schedule completeness, capped for unverified data.

    A user-supplied schedule can be excellent, but it has not been checked
    against a licensed source, so it never reaches the confidence of a verified
    catalogue entry no matter how many rows are added.
    """
    n = len(row.flights)
    if row.source == "seed":
        return
    ceiling = 0.75 if row.verified else 0.62
    row.data_quality = round(min(0.35 + 0.02 * min(n, 200) ** 0.5 * 3, ceiling), 3)


def _calibration_report(row: AirportRow) -> dict:
    airport = row_to_airport(row)
    factor = solve_calibration(airport)
    out: dict = {"calibration_factor": round(factor, 3)}
    if row.annual_pax_baseline and factor >= 4.9:
        out["warning"] = (
            "The stored schedule accounts for far less traffic than the published "
            "annual total, so the anchor hit its cap. Forecasts will understate "
            "throughput until more of the schedule is loaded."
        )
    elif row.annual_pax_baseline and factor <= 0.25:
        out["warning"] = (
            "The stored schedule implies far more traffic than the published "
            "annual total. Check for duplicated imports or wrong seat counts."
        )
    return out


def _prior_from_engine(session: Session, row: AirportRow, lanes: int):
    """Derive a checkpoint prior from the airport's own modelled peak load."""
    if not row.flights:
        return None
    try:
        from datetime import date

        from paxcast import PaxCastEngine, SimulationConfig

        airport = row_to_airport(row)
        result = PaxCastEngine().simulate(
            airport,
            SimulationConfig(start_date=date.today(), horizon_days=7, n_iterations=1000),
        )
        peak_hour_pax = max((max(r) for r in result.peak_hour_grid), default=0.0)
        if peak_hour_pax <= 0:
            return None
        # Only departing passengers pass through security; the grid counts both
        # directions, so halve it.
        departing = peak_hour_pax / 2.0
        from calibration import suggest_prior_from_throughput

        base, sig, note = suggest_prior_from_throughput(departing, lanes)
        return base, sig, f"Derived from modelled peak load. {note}"
    except Exception:
        # A prior is a convenience, never a hard dependency. If the engine
        # cannot run for this airport, fall back to the lane-type constant
        # rather than failing the checkpoint creation.
        return None
