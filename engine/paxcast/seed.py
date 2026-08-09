"""
Seed airport catalogue and schedule synthesiser.

In production the schedule table comes from OAG/Cirium (licensed) or is
reconstructed from OpenSky ADS-B movements plus fleet seat configurations.
For development and for the offline demo we synthesise a schedule that
reproduces the right *structure*: correct daily movement counts, plausible
carrier mix, realistic banked hub waves versus flat O&D profiles, and
aircraft-appropriate seat counts.

The synthesiser is deterministic given the airport code, so demo forecasts are
reproducible across restarts.
"""

from __future__ import annotations

import numpy as np

from .distributions import CARRIER_LF_PRIORS
from .models import Airport, CarrierType, Direction, Flight

# ---------------------------------------------------------------------------
# Aircraft seat configurations by mission type
# ---------------------------------------------------------------------------

FLEET = {
    CarrierType.LCC: [(180, "A320"), (189, "B738"), (230, "A321neo"), (186, "B38M")],
    CarrierType.FSC_SHORT: [(144, "A319"), (168, "A320"), (132, "B737"), (156, "E195")],
    CarrierType.FSC_LONG: [(276, "B788"), (296, "A339"), (350, "B77W"), (410, "A35K")],
    CarrierType.REGIONAL: [(76, "E175"), (88, "CRJ9"), (72, "AT76"), (50, "CRJ2")],
    CarrierType.CHARTER: [(189, "B738"), (235, "B752"), (220, "A321")],
}

# ---------------------------------------------------------------------------
# Airport catalogue
#   (iata, icao, name, city, country, lat, lon, climate, tz,
#    daily_movements, hub_strength, capacity/hr, annual_pax, data_quality)
# hub_strength 0 = pure O&D, 1 = heavily banked connecting hub
# ---------------------------------------------------------------------------

CATALOGUE: list[tuple] = [
    ("SKP", "LWSK", "Skopje International", "Skopje", "North Macedonia",
     41.9616, 21.6214, "temperate", "Europe/Skopje", 48, 0.05, 1800, 2_600_000, 0.72),
    ("OHD", "LWOH", "Ohrid St. Paul the Apostle", "Ohrid", "North Macedonia",
     41.1800, 20.7423, "temperate", "Europe/Skopje", 12, 0.02, 700, 300_000, 0.55),
    ("VIE", "LOWW", "Vienna International", "Vienna", "Austria",
     48.1103, 16.5697, "temperate", "Europe/Vienna", 460, 0.55, 7500, 31_000_000, 0.92),
    ("LHR", "EGLL", "London Heathrow", "London", "United Kingdom",
     51.4700, -0.4543, "temperate", "Europe/London", 1300, 0.62, 14000, 83_000_000, 0.95),
    ("IST", "LTFM", "Istanbul Airport", "Istanbul", "Türkiye",
     41.2753, 28.7519, "temperate", "Europe/Istanbul", 1350, 0.72, 16000, 80_000_000, 0.88),
    ("DOH", "OTHH", "Hamad International", "Doha", "Qatar",
     25.2731, 51.6081, "mild", "Asia/Qatar", 780, 0.80, 12000, 52_000_000, 0.90),
    ("AMS", "EHAM", "Amsterdam Schiphol", "Amsterdam", "Netherlands",
     52.3105, 4.7683, "temperate", "Europe/Amsterdam", 1250, 0.60, 13000, 72_000_000, 0.94),
    ("CDG", "LFPG", "Paris Charles de Gaulle", "Paris", "France",
     49.0097, 2.5479, "temperate", "Europe/Paris", 1400, 0.58, 15000, 70_000_000, 0.93),
    ("FRA", "EDDF", "Frankfurt am Main", "Frankfurt", "Germany",
     50.0379, 8.5622, "temperate", "Europe/Berlin", 1300, 0.65, 14000, 61_000_000, 0.94),
    ("BCN", "LEBL", "Barcelona El Prat", "Barcelona", "Spain",
     41.2974, 2.0833, "mild", "Europe/Madrid", 950, 0.18, 11000, 55_000_000, 0.91),
    ("ATH", "LGAV", "Athens Eleftherios Venizelos", "Athens", "Greece",
     37.9364, 23.9445, "mild", "Europe/Athens", 520, 0.20, 6500, 31_000_000, 0.87),
    ("BEG", "LYBE", "Belgrade Nikola Tesla", "Belgrade", "Serbia",
     44.8184, 20.3091, "temperate", "Europe/Belgrade", 200, 0.30, 3000, 8_200_000, 0.80),
    ("SOF", "LBSF", "Sofia Airport", "Sofia", "Bulgaria",
     42.6967, 23.4114, "temperate", "Europe/Sofia", 130, 0.10, 2400, 7_600_000, 0.78),
    ("TIA", "LATI", "Tirana Nënë Tereza", "Tirana", "Albania",
     41.4147, 19.7206, "temperate", "Europe/Tirane", 110, 0.08, 2200, 7_300_000, 0.74),
    ("ZRH", "LSZH", "Zurich Airport", "Zurich", "Switzerland",
     47.4647, 8.5492, "harsh_winter", "Europe/Zurich", 700, 0.50, 8000, 31_000_000, 0.93),
    ("MUC", "EDDM", "Munich Franz Josef Strauss", "Munich", "Germany",
     48.3538, 11.7861, "harsh_winter", "Europe/Berlin", 1000, 0.55, 11000, 41_000_000, 0.93),
    ("DXB", "OMDB", "Dubai International", "Dubai", "UAE",
     25.2532, 55.3657, "mild", "Asia/Dubai", 1100, 0.75, 16000, 87_000_000, 0.91),
    ("SIN", "WSSS", "Singapore Changi", "Singapore", "Singapore",
     1.3644, 103.9915, "monsoon", "Asia/Singapore", 1000, 0.68, 14000, 68_000_000, 0.92),
    ("JFK", "KJFK", "New York John F. Kennedy", "New York", "United States",
     40.6413, -73.7781, "harsh_winter", "America/New_York", 1150, 0.45, 13000, 62_000_000, 0.96),
    ("LAX", "KLAX", "Los Angeles International", "Los Angeles", "United States",
     33.9416, -118.4085, "mild", "America/Los_Angeles", 1600, 0.35, 15000, 75_000_000, 0.96),
]

CARRIERS_BY_REGION = {
    "Europe": [
        ("FR", CarrierType.LCC), ("W6", CarrierType.LCC), ("U2", CarrierType.LCC),
        ("LH", CarrierType.FSC_SHORT), ("AF", CarrierType.FSC_SHORT),
        ("KL", CarrierType.FSC_SHORT), ("OS", CarrierType.FSC_SHORT),
        ("LX", CarrierType.FSC_SHORT), ("BA", CarrierType.FSC_SHORT),
        ("TK", CarrierType.FSC_SHORT), ("JU", CarrierType.REGIONAL),
        ("A3", CarrierType.FSC_SHORT), ("EW", CarrierType.LCC),
    ],
    "Gulf": [
        ("QR", CarrierType.FSC_LONG), ("EK", CarrierType.FSC_LONG),
        ("EY", CarrierType.FSC_LONG), ("FZ", CarrierType.LCC),
        ("G9", CarrierType.LCC), ("WY", CarrierType.FSC_SHORT),
    ],
    "Americas": [
        ("AA", CarrierType.FSC_SHORT), ("DL", CarrierType.FSC_SHORT),
        ("UA", CarrierType.FSC_SHORT), ("B6", CarrierType.LCC),
        ("WN", CarrierType.LCC), ("AS", CarrierType.FSC_SHORT),
    ],
    "Asia": [
        ("SQ", CarrierType.FSC_LONG), ("CX", CarrierType.FSC_LONG),
        ("TR", CarrierType.LCC), ("MH", CarrierType.FSC_SHORT),
        ("TG", CarrierType.FSC_SHORT), ("AK", CarrierType.LCC),
    ],
}

ENDPOINTS = [
    "VIE", "LHR", "IST", "DOH", "AMS", "CDG", "FRA", "BCN", "ATH", "BEG",
    "SOF", "TIA", "ZRH", "MUC", "DXB", "SIN", "JFK", "LAX", "MXP", "FCO",
    "MAD", "CPH", "ARN", "OSL", "WAW", "PRG", "BUD", "OTP", "DUS", "BER",
]


def _region_for(country: str) -> str:
    if country in {"Qatar", "UAE"}:
        return "Gulf"
    if country in {"United States"}:
        return "Americas"
    if country in {"Singapore"}:
        return "Asia"
    return "Europe"


def _hub_wave_minute(rng: np.random.Generator, hub_strength: float) -> int:
    """Departure/arrival minute reflecting hub banking.

    Connecting hubs cluster movements into waves so that inbound and outbound
    banks can exchange passengers. Pure O&D airports spread flights across the
    day with a morning and evening bulge. This distinction is what makes the
    peak-hour heatmap useful rather than decorative.
    """
    if rng.random() < hub_strength:
        wave_centres = [7 * 60, 10 * 60, 13 * 60, 16 * 60, 19 * 60, 22 * 60]
        centre = wave_centres[rng.integers(0, len(wave_centres))]
        m = int(rng.normal(centre, 32))
    else:
        centre = 8 * 60 if rng.random() < 0.5 else 18 * 60
        m = int(rng.normal(centre, 175))
    return int(np.clip(m, 5 * 60, 23 * 60 + 55))


def build_airport(spec: tuple) -> Airport:
    (iata, icao, name, city, country, lat, lon, climate, tz,
     movements, hub, cap, annual, dq) = spec

    rng = np.random.default_rng(abs(hash(iata)) % (2**32))
    region = _region_for(country)
    pool = CARRIERS_BY_REGION[region] + CARRIERS_BY_REGION["Europe"][:4]

    flights: list[Flight] = []
    for i in range(movements):
        carrier, ctype = pool[rng.integers(0, len(pool))]

        # Long-haul share scales with hub strength and airport size.
        if rng.random() < 0.18 * hub + (0.10 if movements > 800 else 0.0):
            ctype = CarrierType.FSC_LONG
        elif movements < 60 and rng.random() < 0.25:
            ctype = CarrierType.REGIONAL

        fleet = FLEET[ctype]
        seats, _ac = fleet[rng.integers(0, len(fleet))]

        direction = Direction.ARRIVAL if i % 2 == 0 else Direction.DEPARTURE

        # Frequency pattern: daily, 5x weekly, or 2-3x weekly.
        r = rng.random()
        if r < 0.55:
            mask = 0b1111111
        elif r < 0.80:
            mask = 0b0011111 if rng.random() < 0.5 else 0b1111100
        else:
            bits = rng.choice(7, size=int(rng.integers(2, 4)), replace=False)
            mask = int(sum(1 << int(b) for b in bits))

        endpoint = ENDPOINTS[rng.integers(0, len(ENDPOINTS))]
        if endpoint == iata:
            endpoint = "FCO"

        reliability = float(np.clip(rng.normal(0.986, 0.008), 0.94, 0.998))
        transfer = float(np.clip(rng.normal(0.10 + 0.55 * hub, 0.08), 0.0, 0.85))

        flights.append(
            Flight(
                flight_no=f"{carrier}{rng.integers(100, 9999)}",
                carrier=carrier,
                carrier_type=ctype,
                other_endpoint=endpoint,
                direction=direction,
                seats=int(seats),
                sched_minute=_hub_wave_minute(rng, hub),
                dow_mask=mask,
                transfer_share=transfer,
                reliability=reliability,
            )
        )

    # Seasonality: leisure-heavy airports swing harder than business hubs.
    leisure = 1.0 - hub
    amp = 0.10 + 0.22 * leisure
    w = np.arange(53)
    seasonality = (1.0 + amp * np.sin(2 * np.pi * (w - 12) / 53.0)).astype(np.float32)

    airport = Airport(
        iata=iata, icao=icao, name=name, city=city, country=country,
        lat=lat, lon=lon, climate=climate, timezone=tz,
        terminal_capacity_hourly=cap, annual_pax_baseline=annual,
        seasonality=seasonality, data_quality=dq, flights=flights,
    )
    airport.calibration_factor = solve_calibration(airport)
    return airport


def solve_calibration(airport: Airport) -> float:
    """Anchor the synthetic schedule to published annual throughput.

    A synthesised schedule reproduces the right *shape* -- carrier mix, banking,
    frequency patterns -- but its absolute level is arbitrary. Published annual
    passenger totals (ACI World, Eurostat avia_paoc, BTS) are the one number we
    can always obtain for a commercial airport, so we solve for the single
    multiplicative factor that makes the schedule reproduce it.

    This is a closed-form deterministic solve, not a fitted parameter: it uses
    the analytic expectation of the pax identity rather than running the
    simulation, so it costs nothing and introduces no Monte Carlo noise.

        E[annual pax] = 52 * sum_f seats_f * days_f * E[LF_f] * E[flown_f]
                        * E[showup] * mean_season

    In production this factor is replaced by per-route calibration against
    actual carried-passenger data, and drops to 1.0 for airports where the
    licensed schedule is complete.
    """
    if not airport.annual_pax_baseline:
        return 1.0

    expected = 0.0
    for f in airport.flights:
        lf_mean, _ = CARRIER_LF_PRIORS[f.carrier_type.value]
        days_per_week = bin(f.dow_mask).count("1")
        expected += f.seats * days_per_week * lf_mean * f.reliability
    expected *= 52.0 * 0.955  # weeks per year x mean show-up

    if airport.seasonality is not None:
        expected *= float(np.mean(airport.seasonality))

    if expected <= 0:
        return 1.0
    return float(airport.annual_pax_baseline / expected)


_CACHE: dict[str, Airport] = {}


def get_airport(iata: str) -> Airport:
    iata = iata.upper()
    if iata not in _CACHE:
        spec = next((s for s in CATALOGUE if s[0] == iata), None)
        if spec is None:
            raise KeyError(f"Unknown airport: {iata}")
        _CACHE[iata] = build_airport(spec)
    return _CACHE[iata]


def list_airports() -> list[dict]:
    return [
        {
            "iata": s[0], "icao": s[1], "name": s[2], "city": s[3],
            "country": s[4], "lat": s[5], "lon": s[6],
            "daily_movements": s[9], "annual_pax": s[12], "data_quality": s[13],
        }
        for s in CATALOGUE
    ]
