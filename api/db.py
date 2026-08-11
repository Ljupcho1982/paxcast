"""
Persistence layer for PaxCast.

Until now the airport catalogue was a hardcoded list rebuilt on every process
start, which meant nothing a user added survived a restart and there was
nowhere to put observed data. This module replaces that with SQLite.

Four tables:

    airports        the estate: identity, geometry, declared capacity
    flights         schedule rows, either licensed or user-supplied
    checkpoints     security lanes, with their wait-time parameters
    observations    real reported waits -- the only ground truth in the system

The important relationship is the last one. `checkpoints` carries a prior
(`prior_base`, `prior_sig`) and a fitted posterior (`fit_base`, `fit_sig`,
`fit_n`). Observations flow in, the fit is recomputed with shrinkage toward the
prior, and the forecast improves as evidence accumulates. A checkpoint with two
reports should barely move off its prior; one with two hundred should be
governed almost entirely by its own history.

SQLite is deliberate for this stage: single file, zero operational burden,
handles the write volume of user-submitted observations comfortably. The schema
is plain enough to move to PostgreSQL/TimescaleDB unchanged when observation
volume justifies it.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Airports
# ---------------------------------------------------------------------------


class AirportRow(Base):
    __tablename__ = "airports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    iata: Mapped[str] = mapped_column(String(3), unique=True, index=True)
    icao: Mapped[str] = mapped_column(String(4), default="")
    name: Mapped[str] = mapped_column(String(160))
    city: Mapped[str] = mapped_column(String(90), default="")
    country: Mapped[str] = mapped_column(String(90), default="")
    lat: Mapped[float] = mapped_column(Float, default=0.0)
    lon: Mapped[float] = mapped_column(Float, default=0.0)
    climate: Mapped[str] = mapped_column(String(20), default="temperate")
    timezone: Mapped[str] = mapped_column(String(60), default="UTC")
    terminal_capacity_hourly: Mapped[int] = mapped_column(Integer, default=3000)
    annual_pax_baseline: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Provenance matters here. A user-submitted airport must never be presented
    # with the same confidence as one backed by published ACI/Eurostat totals,
    # so the source is recorded and feeds the confidence badge.
    source: Mapped[str] = mapped_column(String(20), default="user")  # seed | user | import
    data_quality: Mapped[float] = mapped_column(Float, default=0.45)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    flights: Mapped[list["FlightRow"]] = relationship(
        back_populates="airport", cascade="all, delete-orphan", lazy="selectin"
    )
    checkpoints: Mapped[list["CheckpointRow"]] = relationship(
        back_populates="airport", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("lat >= -90 AND lat <= 90", name="ck_airport_lat"),
        CheckConstraint("lon >= -180 AND lon <= 180", name="ck_airport_lon"),
        CheckConstraint("terminal_capacity_hourly > 0", name="ck_airport_capacity"),
        CheckConstraint("data_quality >= 0 AND data_quality <= 1", name="ck_airport_dq"),
    )


# ---------------------------------------------------------------------------
# Flights
# ---------------------------------------------------------------------------


class FlightRow(Base):
    __tablename__ = "flights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    airport_id: Mapped[int] = mapped_column(
        ForeignKey("airports.id", ondelete="CASCADE"), index=True
    )
    flight_no: Mapped[str] = mapped_column(String(10))
    carrier: Mapped[str] = mapped_column(String(3), index=True)
    carrier_type: Mapped[str] = mapped_column(String(12), default="FSC_SHORT")
    other_endpoint: Mapped[str] = mapped_column(String(3), default="")
    direction: Mapped[str] = mapped_column(String(3), default="DEP")  # ARR | DEP
    seats: Mapped[int] = mapped_column(Integer, default=180)
    sched_minute: Mapped[int] = mapped_column(Integer, default=480)
    dow_mask: Mapped[int] = mapped_column(Integer, default=0b1111111)
    transfer_share: Mapped[float] = mapped_column(Float, default=0.15)
    reliability: Mapped[float] = mapped_column(Float, default=0.985)
    source: Mapped[str] = mapped_column(String(20), default="user")

    airport: Mapped[AirportRow] = relationship(back_populates="flights")

    __table_args__ = (
        CheckConstraint("seats > 0 AND seats <= 900", name="ck_flight_seats"),
        CheckConstraint("sched_minute >= 0 AND sched_minute < 1440", name="ck_flight_minute"),
        CheckConstraint("dow_mask > 0 AND dow_mask <= 127", name="ck_flight_dow"),
        CheckConstraint("reliability > 0 AND reliability <= 1", name="ck_flight_rel"),
        # A carrier cannot operate the same flight number in the same direction
        # at the same minute twice. This is what makes re-importing a schedule
        # idempotent instead of doubling the airport's traffic.
        UniqueConstraint(
            "airport_id", "flight_no", "direction", "sched_minute", name="uq_flight_slot"
        ),
        Index("ix_flight_airport_dir", "airport_id", "direction"),
    )


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------


class CheckpointRow(Base):
    __tablename__ = "checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    airport_id: Mapped[int] = mapped_column(
        ForeignKey("airports.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    zone: Mapped[str] = mapped_column(String(120), default="")
    lane_type: Mapped[str] = mapped_column(String(20), default="standard")  # standard | expedited | premium

    # Physical capacity. Until lane allocation existed, `lanes` was accepted at
    # creation, used once to derive the prior, and then thrown away -- so the
    # database could not answer "how many lanes does this checkpoint have?",
    # which is the decision variable the lane planner solves for.
    lanes: Mapped[int] = mapped_column(Integer, default=4)
    throughput_per_lane_hour: Mapped[float] = mapped_column(Float, default=150.0)

    # Prior: where the checkpoint sits before it has any observations of its own.
    prior_base: Mapped[float] = mapped_column(Float, default=12.0)
    prior_sig: Mapped[float] = mapped_column(Float, default=0.50)

    # Posterior: recomputed by calibration.fit_checkpoint() as reports arrive.
    fit_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    fit_sig: Mapped[float | None] = mapped_column(Float, nullable=True)
    fit_n: Mapped[int] = mapped_column(Integer, default=0)
    fit_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    airport: Mapped[AirportRow] = relationship(back_populates="checkpoints")
    observations: Mapped[list["ObservationRow"]] = relationship(
        back_populates="checkpoint", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("prior_base > 0", name="ck_cp_base"),
        CheckConstraint("prior_sig > 0 AND prior_sig < 3", name="ck_cp_sig"),
        UniqueConstraint("airport_id", "name", name="uq_checkpoint_name"),
    )

    @property
    def effective_base(self) -> float:
        return self.fit_base if self.fit_base is not None else self.prior_base

    @property
    def effective_sig(self) -> float:
        return self.fit_sig if self.fit_sig is not None else self.prior_sig


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


class ObservationRow(Base):
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checkpoint_id: Mapped[int] = mapped_column(
        ForeignKey("checkpoints.id", ondelete="CASCADE"), index=True
    )
    wait_minutes: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    hour_local: Mapped[int] = mapped_column(Integer)
    weekday: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20), default="user")  # user | operator | sensor
    # Operator and sensor feeds are trusted more than crowd reports; the weight
    # is applied when fitting rather than by discarding data.
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    checkpoint: Mapped[CheckpointRow] = relationship(back_populates="observations")

    __table_args__ = (
        # A zero-minute wait is not an observation, it is a mis-tap; a wait over
        # eight hours is not a queue, it is a stuck timer. Both are rejected at
        # the schema level so bad data cannot reach the fit.
        CheckConstraint("wait_minutes > 0 AND wait_minutes <= 480", name="ck_obs_wait"),
        CheckConstraint("hour_local >= 0 AND hour_local < 24", name="ck_obs_hour"),
        CheckConstraint("weekday >= 0 AND weekday < 7", name="ck_obs_weekday"),
        Index("ix_obs_cp_time", "checkpoint_id", "observed_at"),
    )


# ---------------------------------------------------------------------------
# Engine / session
# ---------------------------------------------------------------------------

DB_PATH = os.environ.get("PAXCAST_DB", "paxcast.db")
_engine = create_engine(f"sqlite:///{DB_PATH}", future=True)


@event.listens_for(_engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    """SQLite ships with foreign keys off, which would silently defeat every
    ondelete=CASCADE above. WAL improves concurrent read behaviour while
    observations are being written."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(_engine)
    _add_missing_columns()


# Columns added after the first release. create_all() creates missing *tables*
# and silently ignores missing *columns*, so an existing database would keep
# working until the first query touched a new one and SQLite raised
# "no such column". The project has no migration tool and SQLite supports
# additive ALTERs cheaply, so the additions are applied directly.
#
# Additive only: no renames, no drops, no type changes. Anything beyond that
# needs a real migration story rather than this.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "checkpoints": {
        "lanes": "INTEGER NOT NULL DEFAULT 4",
        "throughput_per_lane_hour": "FLOAT NOT NULL DEFAULT 150.0",
    },
}


def _add_missing_columns() -> None:
    from sqlalchemy import text

    with _engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {
                row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            if not existing:
                continue  # table not created yet; create_all will include them
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def reset_db() -> None:
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)


def get_engine():
    return _engine
