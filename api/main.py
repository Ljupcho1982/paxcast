"""
PaxCast API.

Serves probabilistic passenger-throughput forecasts to the Android client.

Caching strategy mirrors the production design: baseline forecasts are
deterministic given (airport, start date, horizon, seed) and are therefore
cached; user-defined scenarios are computed on demand. In production the
baseline cache is warmed by a nightly batch job and stored in Redis, and this
in-process dict is the drop-in stand-in.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from datetime import date, timedelta
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

from paxcast import PaxCastEngine, SimulationConfig  # noqa: E402
from paxcast.models import Scenario  # noqa: E402

from db import AirportRow, SessionLocal  # noqa: E402
from repository import bootstrap, list_airport_rows, load_airport  # noqa: E402
from routes_data import router as data_router  # noqa: E402

app = FastAPI(
    title="PaxCast API",
    version="0.1.0",
    description="Probabilistic airport passenger throughput forecasting",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ENGINE = PaxCastEngine()
_CACHE: dict[str, dict] = {}
_CACHE_MAX = 256

# Create tables and import the built-in catalogue on first run.
_SEEDED = bootstrap()

app.include_router(data_router)


def invalidate_cache(iata: str | None = None) -> int:
    """Drop cached forecasts after data changes.

    Adding flights changes the forecast, so a cache keyed only on the request
    parameters would keep serving the pre-import answer and make the import
    look like it did nothing. Coarse invalidation is correct here -- forecasts
    are cheap to recompute and a stale band is a correctness bug, not a
    performance one.
    """
    global _CACHE
    if iata is None:
        n = len(_CACHE)
        _CACHE = {}
        return n
    victims = [k for k, v in _CACHE.items() if v.get("iata") == iata.upper()]
    for k in victims:
        _CACHE.pop(k, None)
    return len(victims)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ScenarioRequest(BaseModel):
    name: str = "Custom scenario"
    load_factor_delta: float = Field(0.0, ge=-0.6, le=0.3)
    capacity_multiplier: float = Field(1.0, gt=0.0, le=3.0)
    demand_multiplier: float = Field(1.0, gt=0.0, le=3.0)
    grounded_carriers: list[str] = Field(default_factory=list)
    closed_routes: list[str] = Field(default_factory=list)
    extra_cancel_prob: float = Field(0.0, ge=0.0, le=1.0)
    disable_shocks: bool = False

    def to_scenario(self) -> Scenario:
        return Scenario(
            name=self.name,
            load_factor_delta=self.load_factor_delta,
            capacity_multiplier=self.capacity_multiplier,
            demand_multiplier=self.demand_multiplier,
            grounded_carriers=tuple(c.upper() for c in self.grounded_carriers),
            closed_routes=tuple(r.upper() for r in self.closed_routes),
            extra_cancel_prob=self.extra_cancel_prob,
            disable_shocks=self.disable_shocks,
        )


class ForecastRequest(BaseModel):
    iata: str
    start_date: date | None = None
    horizon_days: int = Field(30, ge=1, le=730)
    iterations: int = Field(20_000, ge=500, le=200_000)
    scenario: ScenarioRequest | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cache_key(iata: str, start: date, horizon: int, iters: int, scenario: Scenario) -> str:
    raw = f"{iata}|{start}|{horizon}|{iters}|{scenario}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _run(iata: str, start: date, horizon: int, iters: int, scenario: Scenario) -> dict:
    key = _cache_key(iata, start, horizon, iters, scenario)
    if key in _CACHE:
        out = dict(_CACHE[key])
        out["cached"] = True
        return out

    with SessionLocal() as session:
        airport = load_airport(session, iata)
    if airport is None:
        raise HTTPException(404, f"Unknown airport: {iata}")
    if not airport.flights:
        raise HTTPException(
            422,
            f"{iata} has no schedule loaded. Add flights before requesting a "
            f"forecast: POST /airports/{iata}/flights or /flights/csv.",
        )

    cfg = SimulationConfig(
        start_date=start,
        horizon_days=horizon,
        n_iterations=iters,
        seed=42,
    )
    try:
        result = ENGINE.simulate(airport, cfg, scenario)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    payload = result.to_dict()
    payload["airport"] = {
        "iata": airport.iata,
        "icao": airport.icao,
        "name": airport.name,
        "city": airport.city,
        "country": airport.country,
        "lat": airport.lat,
        "lon": airport.lon,
        "annual_pax_baseline": airport.annual_pax_baseline,
        "terminal_capacity_hourly": airport.terminal_capacity_hourly,
        "daily_flights": len(airport.flights),
    }
    payload["cached"] = False

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = payload
    return payload


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    with SessionLocal() as session:
        from sqlalchemy import func, select

        n = session.scalar(select(func.count()).select_from(AirportRow))
    return {
        "status": "ok",
        "airports": n,
        "seeded_on_boot": _SEEDED,
        "cache_entries": len(_CACHE),
    }


@app.get("/airports")
def airports(
    q: str | None = Query(None, description="Search by IATA, name, city or country"),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    with SessionLocal() as session:
        rows = list_airport_rows(session, q, limit)
        return {
            "count": len(rows),
            "airports": [
                {
                    "iata": r.iata,
                    "icao": r.icao,
                    "name": r.name,
                    "city": r.city,
                    "country": r.country,
                    "lat": r.lat,
                    "lon": r.lon,
                    "daily_movements": len(r.flights),
                    "annual_pax": r.annual_pax_baseline or 0,
                    "data_quality": r.data_quality,
                    "source": r.source,
                    "verified": r.verified,
                    "checkpoints": len(r.checkpoints),
                }
                for r in rows
            ],
        }


@app.get("/airports/{iata}")
def airport_detail(iata: str) -> dict:
    with SessionLocal() as session:
        a = load_airport(session, iata)
        row = session.scalar(
            __import__("sqlalchemy").select(AirportRow).where(AirportRow.iata == iata.upper())
        )
    if a is None or row is None:
        raise HTTPException(404, f"Unknown airport: {iata}")

    carriers: dict[str, int] = {}
    for f in a.flights:
        carriers[f.carrier] = carriers.get(f.carrier, 0) + 1
    top = sorted(carriers.items(), key=lambda kv: -kv[1])[:12]
    return {
        "iata": a.iata,
        "icao": a.icao,
        "name": a.name,
        "city": a.city,
        "country": a.country,
        "lat": a.lat,
        "lon": a.lon,
        "climate": a.climate,
        "timezone": a.timezone,
        "annual_pax_baseline": a.annual_pax_baseline,
        "terminal_capacity_hourly": a.terminal_capacity_hourly,
        "data_quality": a.data_quality,
        "source": row.source,
        "verified": row.verified,
        "daily_flights": len(a.flights),
        "weekly_seats": a.weekly_seats(),
        "carriers": [{"code": c, "flights": n} for c, n in top],
    }


@app.get("/forecast/{iata}")
def forecast_get(
    iata: str,
    horizon: int = Query(30, ge=1, le=730),
    iterations: int = Query(20_000, ge=500, le=200_000),
    start: date | None = None,
) -> dict:
    return _run(iata.upper(), start or date.today(), horizon, iterations, Scenario())


@app.post("/forecast")
def forecast_post(req: ForecastRequest) -> dict:
    scenario = req.scenario.to_scenario() if req.scenario else Scenario()
    return _run(
        req.iata.upper(),
        req.start_date or date.today(),
        req.horizon_days,
        req.iterations,
        scenario,
    )


@app.post("/compare")
def compare(req: ForecastRequest) -> dict:
    """Run baseline and scenario together so the client can overlay them.

    Returning both from one call keeps the two runs on the same seed, so the
    difference the user sees is the scenario effect and not Monte Carlo noise
    between two independent runs.
    """
    if req.scenario is None:
        raise HTTPException(422, "A scenario is required for comparison")
    start = req.start_date or date.today()
    baseline = _run(req.iata.upper(), start, req.horizon_days, req.iterations, Scenario())
    variant = _run(
        req.iata.upper(), start, req.horizon_days, req.iterations, req.scenario.to_scenario()
    )
    b, v = baseline["total_percentiles"]["p50"], variant["total_percentiles"]["p50"]
    return {
        "baseline": baseline,
        "scenario": variant,
        "delta": {
            "total_p50_absolute": round(v - b, 0),
            "total_p50_percent": round((v - b) / b * 100.0, 2) if b else 0.0,
        },
    }


@app.get("/presets")
def presets() -> dict:
    """Scenario presets, so the user isn't facing a blank form."""
    return {
        "presets": [
            {
                "id": "atc_strike",
                "label": "ATC strike",
                "description": "French/Italian-style ATC industrial action",
                "scenario": {"name": "ATC strike", "extra_cancel_prob": 0.35},
            },
            {
                "id": "runway_closure",
                "label": "Runway closure",
                "description": "One of two runways out for maintenance",
                "scenario": {"name": "Runway closure", "capacity_multiplier": 0.65},
            },
            {
                "id": "demand_slump",
                "label": "Demand slump",
                "description": "Recession-driven booking weakness",
                "scenario": {"name": "Demand slump", "load_factor_delta": -0.08},
            },
            {
                "id": "peak_summer",
                "label": "Capacity surge",
                "description": "Carriers add 15% seats for peak season",
                "scenario": {"name": "Capacity surge", "capacity_multiplier": 1.15},
            },
            {
                "id": "no_shocks",
                "label": "Exclude shocks",
                "description": "Planning case with no exogenous disruption",
                "scenario": {"name": "No shocks", "disable_shocks": True},
            },
        ]
    }


@app.get("/validate/{iata}")
def validate(iata: str, trials: int = Query(40, ge=10, le=200)) -> dict:
    """Expose the calibration diagnostics rather than hiding them.

    An app that sells uncertainty has to be willing to show whether its
    uncertainty is honest.
    """
    from paxcast.validation import validate_machinery

    with SessionLocal() as session:
        a = load_airport(session, iata)
    if a is None:
        raise HTTPException(404, f"Unknown airport: {iata}")

    t0 = time.perf_counter()
    rep = validate_machinery(a, date.today(), n_trials=trials, horizon_days=10)
    return {
        "iata": a.iata,
        "scope": rep.scope,
        "observations": rep.n_observations,
        "coverage": {
            "nominal_50": round(rep.coverage_50, 4),
            "nominal_80": round(rep.coverage_80, 4),
            "nominal_90": round(rep.coverage_90, 4),
        },
        "pit_ks_statistic": round(rep.ks_statistic, 4),
        "pit_ks_pvalue": round(rep.ks_pvalue, 4),
        "crps": round(rep.crps, 1),
        "crps_point_forecast": round(rep.crps_point, 1),
        "skill_vs_point_forecast": round(rep.crps_skill_vs_point, 4),
        "verdict": rep.verdict,
        "runtime_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


@app.get("/")
def root() -> dict:
    return {
        "service": "PaxCast API",
        "version": "0.1.0",
        "endpoints": [
            "GET  /health",
            "GET  /airports?q=",
            "GET  /airports/{iata}",
            "GET  /forecast/{iata}?horizon=&iterations=",
            "POST /forecast",
            "POST /compare",
            "GET  /presets",
            "GET  /validate/{iata}",
        ],
    }
