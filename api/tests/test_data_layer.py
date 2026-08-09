"""Tests for the data layer: persistence, ingestion, validation, calibration."""

import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A fresh database file per test session; the module reads this at import time.
_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["PAXCAST_DB"] = _TMP.name

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from calibration import (  # noqa: E402
    Observation,
    fit_checkpoint,
    peak,
    suggest_prior_from_throughput,
)
from routes_data import _parse_dow, _parse_time  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app)


NEW_AIRPORT = {
    "iata": "KVD",
    "icao": "LWKV",
    "name": "Kavadarci Regional",
    "city": "Kavadarci",
    "country": "North Macedonia",
    "lat": 41.4331,
    "lon": 22.0117,
    "terminal_capacity_hourly": 400,
    "annual_pax_baseline": 120_000,
}

CSV = (
    "flight_no,carrier,carrier_type,other_endpoint,direction,seats,sched_time,dow\n"
    "W64301,W6,LCC,VIE,DEP,230,06:35,1234567\n"
    "W64302,W6,LCC,VIE,ARR,230,22:10,1234567\n"
    "FR8811,FR,LCC,BGY,DEP,189,09:20,135\n"
    "FR8812,FR,LCC,BGY,ARR,189,08:40,135\n"
)


# ---------------------------------------------------------------- seeding


def test_catalogue_is_seeded_on_boot(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["airports"] >= 20


def test_seeded_airport_is_forecastable(client):
    r = client.get("/forecast/SKP?horizon=7&iterations=1000")
    assert r.status_code == 200
    assert len(r.json()["dates"]) == 7


# ---------------------------------------------------------------- create


def test_create_airport(client):
    r = client.post("/airports", json=NEW_AIRPORT)
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["iata"] == "KVD"
    assert body["source"] == "user"
    assert body["forecastable"] is False  # no schedule yet
    assert body["data_quality"] < 0.5     # and the badge must say so


def test_duplicate_iata_rejected(client):
    assert client.post("/airports", json=NEW_AIRPORT).status_code == 409


def test_coincident_position_rejected(client):
    payload = {**NEW_AIRPORT, "iata": "QQQ", "lat": 41.4335, "lon": 22.0119}
    r = client.post("/airports", json=payload)
    assert r.status_code == 409
    assert "KVD" in r.json()["detail"]


def test_null_island_rejected(client):
    payload = {**NEW_AIRPORT, "iata": "ZZZ", "lat": 0.0, "lon": 0.0}
    r = client.post("/airports", json=payload)
    assert r.status_code == 422
    assert "unset" in r.json()["detail"].lower()


@pytest.mark.parametrize(
    "field,value",
    [
        ("iata", "KAVA"),
        ("iata", "K1"),
        ("icao", "LW"),
        ("lat", 999.0),
        ("lon", -400.0),
        ("climate", "tropical"),
        ("terminal_capacity_hourly", 0),
    ],
)
def test_field_validation(client, field, value):
    payload = {**NEW_AIRPORT, "iata": "TST", field: value}
    assert client.post("/airports", json=payload).status_code == 422


def test_seed_airports_are_immutable(client):
    assert client.patch("/airports/SKP", json={"name": "Hacked"}).status_code == 409
    assert client.delete("/airports/SKP").status_code == 409


def test_user_airport_is_editable(client):
    r = client.patch("/airports/KVD", json={"city": "Kavadarci Municipality"})
    assert r.status_code == 200
    assert r.json()["city"] == "Kavadarci Municipality"


# ---------------------------------------------------------------- forecast gating


def test_forecast_without_schedule_is_refused_with_guidance(client):
    r = client.get("/forecast/KVD?horizon=7")
    assert r.status_code == 422
    # The error must tell the user what to do next, not just that it failed.
    assert "flights" in r.json()["detail"]


# ---------------------------------------------------------------- ingestion


def test_csv_import(client):
    r = client.post("/airports/KVD/flights/csv", json={"csv": CSV})
    assert r.status_code == 201
    body = r.json()
    assert body["added"] == 4
    assert body["rejected"] == 0
    assert body["total_flights"] == 4


def test_csv_import_is_idempotent(client):
    r = client.post("/airports/KVD/flights/csv", json={"csv": CSV}).json()
    assert r["added"] == 0
    assert r["skipped_duplicates"] == 4
    assert r["total_flights"] == 4


def test_forecast_available_once_schedule_exists(client):
    r = client.get("/forecast/KVD?horizon=7&iterations=1000")
    assert r.status_code == 200
    p = r.json()["percentiles"]
    assert p["p10"][0] < p["p50"][0] < p["p90"][0]


def test_csv_rejects_bad_rows_individually(client):
    bad = (
        "flight_no,carrier,carrier_type,other_endpoint,direction,seats,sched_time,dow\n"
        "OK0001,W6,LCC,VIE,DEP,180,07:00,1234567\n"
        "BAD001,XX,LCC,VIE,SIDEWAYS,200,07:00,1234567\n"
        "BAD002,XX,LCC,VIE,DEP,200,notatime,1234567\n"
        "BAD003,,LCC,VIE,DEP,,08:00,1234567\n"
    )
    body = client.post("/airports/KVD/flights/csv", json={"csv": bad}).json()
    assert body["added"] == 1
    assert body["rejected"] == 3
    # Every rejection carries its source line so the user can fix the file.
    assert {e["line"] for e in body["errors"]} == {3, 4, 5}


def test_csv_overflow_row_rejected_not_crashed(client):
    """An unquoted comma over-splits the row; it must not 500 the import."""
    over = (
        "flight_no,carrier,carrier_type,other_endpoint,direction,seats,sched_time,dow\n"
        "OV0001,JU,REGIONAL,BEG,DEP,76,17:05,Mo,Tu,We,Th,Fr\n"
    )
    r = client.post("/airports/KVD/flights/csv", json={"csv": over})
    assert r.status_code == 201
    body = r.json()
    assert body["added"] == 0 and body["rejected"] == 1
    assert "unquoted comma" in body["errors"][0]["error"]


def test_csv_missing_required_columns(client):
    r = client.post("/airports/KVD/flights/csv", json={"csv": "a,b\n1,2\n"})
    assert r.status_code == 422
    assert "missing required columns" in r.json()["detail"].lower()


def test_csv_empty_body(client):
    assert client.post("/airports/KVD/flights/csv", json={"csv": "  "}).status_code == 422


# ---------------------------------------------------------------- parsers


@pytest.mark.parametrize(
    "text,expected", [("06:35", 395), ("00:00", 0), ("23:59", 1439), ("480", 480)]
)
def test_parse_time(text, expected):
    assert _parse_time(text) == expected


@pytest.mark.parametrize("bad", ["25:00", "1500", "-3"])
def test_parse_time_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        _parse_time(bad)


def test_parse_dow_digit_string():
    assert _parse_dow("1234567") == 0b1111111
    assert _parse_dow("135") == 0b0010101  # Mon, Wed, Fri


def test_parse_dow_day_names():
    assert _parse_dow("Mo,Tu,We,Th,Fr") == 0b0011111
    assert _parse_dow("Sat Sun") == 0b1100000


def test_parse_dow_rejects_nonsense():
    with pytest.raises(ValueError):
        _parse_dow("someday")


# ---------------------------------------------------------------- checkpoints


def test_add_checkpoint_derives_prior(client):
    r = client.post(
        "/airports/KVD/checkpoints",
        json={"name": "Checkpoint A", "zone": "Main hall", "lane_type": "standard", "lanes": 3},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["prior_base"] > 0 and 0 < body["prior_sig"] < 3
    assert body["confidence"] == "LOW"  # no observations yet


def test_duplicate_checkpoint_name_rejected(client):
    r = client.post("/airports/KVD/checkpoints", json={"name": "Checkpoint A"})
    assert r.status_code == 409


def test_observation_moves_the_fit(client):
    cp_id = client.get("/airports/KVD/checkpoints").json()["checkpoints"][0]["id"]
    first = client.post(
        f"/checkpoints/{cp_id}/observations", json={"wait_minutes": 30}
    ).json()["fit"]
    assert first["n_observations"] == 1
    # One report must barely move the estimate; claiming otherwise would be a lie.
    assert first["shrinkage_mu"] < 0.2

    for _ in range(40):
        client.post(f"/checkpoints/{cp_id}/observations", json={"wait_minutes": 30})
    later = client.post(
        f"/checkpoints/{cp_id}/observations", json={"wait_minutes": 30}
    ).json()["fit"]
    assert later["n_observations"] == 42
    assert later["shrinkage_mu"] > first["shrinkage_mu"]
    assert later["confidence"] in {"MEDIUM", "HIGH"}


@pytest.mark.parametrize(
    "payload",
    [
        {"wait_minutes": 0},
        {"wait_minutes": -5},
        {"wait_minutes": 600},
        {"wait_minutes": 20, "source": "psychic"},
    ],
)
def test_observation_validation(client, payload):
    cp_id = client.get("/airports/KVD/checkpoints").json()["checkpoints"][0]["id"]
    assert client.post(f"/checkpoints/{cp_id}/observations", json=payload).status_code == 422


def test_observation_timestamp_bounds(client):
    cp_id = client.get("/airports/KVD/checkpoints").json()["checkpoints"][0]["id"]
    now = datetime.now(timezone.utc)
    future = {"wait_minutes": 20, "observed_at": (now + timedelta(days=1)).isoformat()}
    ancient = {"wait_minutes": 20, "observed_at": (now - timedelta(days=800)).isoformat()}
    assert client.post(f"/checkpoints/{cp_id}/observations", json=future).status_code == 422
    assert client.post(f"/checkpoints/{cp_id}/observations", json=ancient).status_code == 422


def test_unknown_ids_404(client):
    assert client.get("/airports/XXX").status_code == 404
    assert client.post("/checkpoints/999999/observations", json={"wait_minutes": 10}).status_code == 404
    assert client.get("/airports/XXX/checkpoints").status_code == 404


# ---------------------------------------------------------------- calibration unit tests


def test_fit_with_no_observations_returns_prior():
    fit = fit_checkpoint([], "standard")
    assert fit.base == 12.0 and fit.sig == 0.52
    assert fit.shrinkage_mu == 0.0


def test_fit_converges_to_truth():
    """With enough clean reports the fit must recover the generating process."""
    import random

    rng = random.Random(3)
    now = datetime.now(timezone.utc)
    true_base, true_sig = 24.0, 0.55
    obs = []
    for _ in range(400):
        h = rng.choice([6, 7, 8, 12, 17, 18])
        w = math.exp(math.log(true_base * peak(h)) + true_sig * rng.gauss(0, 1))
        obs.append(Observation(w, h, now - timedelta(days=rng.uniform(0, 20))))
    fit = fit_checkpoint(obs, "standard")
    assert abs(fit.base - true_base) / true_base < 0.15
    assert abs(fit.sig - true_sig) < 0.12


def test_fit_resists_a_single_outlier():
    now = datetime.now(timezone.utc)
    clean = [Observation(20.0, 7, now) for _ in range(30)]
    fit_clean = fit_checkpoint(clean, "standard")
    fit_dirty = fit_checkpoint([*clean, Observation(470.0, 7, now)], "standard")
    assert abs(fit_dirty.base - fit_clean.base) / fit_clean.base < 0.20


def test_stale_observations_decay_to_prior():
    old = datetime.now(timezone.utc) - timedelta(days=400)
    fit = fit_checkpoint([Observation(45.0, 7, old) for _ in range(200)], "standard")
    assert fit.n_used == 0
    assert fit.base == 12.0


def test_trusted_sources_carry_more_weight():
    now = datetime.now(timezone.utc)
    user = fit_checkpoint([Observation(40.0, 7, now, "user") for _ in range(5)], "standard")
    sensor = fit_checkpoint([Observation(40.0, 7, now, "sensor") for _ in range(5)], "standard")
    assert sensor.shrinkage_mu > user.shrinkage_mu


def test_depeaking_removes_hour_bias():
    """Reports gathered only at the morning bank must not inflate the base."""
    now = datetime.now(timezone.utc)
    base = 20.0
    peak_only = [
        Observation(base * peak(7), 7, now) for _ in range(200)
    ]
    spread = [
        Observation(base * peak(h), h, now) for h in (5, 7, 9, 12, 17, 20) for _ in range(34)
    ]
    a = fit_checkpoint(peak_only, "standard")
    b = fit_checkpoint(spread, "standard")
    assert abs(a.base - b.base) / b.base < 0.10


def test_throughput_prior_is_monotone_in_load():
    """More passengers over the same lanes must never predict a shorter wait."""
    prev = 0.0
    for pax in range(500, 6000, 250):
        base, sig, _ = suggest_prior_from_throughput(pax, 20)
        assert base >= prev - 1e-9, f"non-monotone at {pax} pax/hr"
        prev = base


def test_throughput_prior_flags_undersizing():
    _, _, note = suggest_prior_from_throughput(4000, 5)
    assert "undersized" in note.lower()


def test_peak_curve_peaks_at_morning_bank():
    hours = [h / 4 for h in range(0, 96)]
    top = max(hours, key=peak)
    assert 6.5 <= top <= 7.5
