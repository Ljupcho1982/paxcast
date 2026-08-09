# PaxCast

Probabilistic airport passenger-throughput forecasting. Monte Carlo simulation
engine, FastAPI service, and an Expo/React Native Android client.

The product thesis in one line: **airports plan against a single number, and the
single number carries no information about how wrong it might be.** PaxCast
sells the distribution instead.

---

## Repository layout

```
paxcast/
  engine/            Monte Carlo simulation core (pure Python + NumPy/SciPy)
    paxcast/
      distributions.py   Beta load factors, Markov weather, shock process, growth
      copula.py          One-factor Gaussian copula (dependence structure)
      quantile_table.py  Tabulated normal->Beta transform (the speed unlock)
      engine.py          Vectorised simulation loop
      validation.py      CRPS, pinball loss, PIT, interval coverage
      models.py          Airport / Flight / Scenario / SimulationConfig
      seed.py            20-airport catalogue + schedule synthesiser
    tests/
      test_engine.py     31 tests
  api/
    main.py            FastAPI service
    db.py              SQLAlchemy schema (airports, flights, checkpoints, observations)
    repository.py      Row <-> engine-domain translation, seeding, recalibration
    calibration.py     Hierarchical fit of checkpoint parameters from observations
    routes_data.py     Write endpoints: add airports, import schedules, report waits
    tests/
      test_data_layer.py   51 tests
  mobile/              Expo / React Native (Android) client
    app/               expo-router screens
      checkpoint/      Modernist traveller screens (picker, distribution detail)
      contribute/      Add airport, import schedule, report a wait
    components/        FanChart, PeakHourGrid, shared UI
    lib/api.ts         API client with offline cache
    constants/theme.ts Design tokens
  data/
    demo_forecasts.json  Real engine output for 5 airports + one scenario pair
```

---

## Quick start

### Engine + API

```bash
cd engine && pip install numpy scipy pytest
python -m pytest tests/ -q          # 31 tests

cd ../api && pip install fastapi uvicorn
uvicorn main:app --reload --port 8000
```

Then:

```bash
curl "localhost:8000/forecast/SKP?horizon=30"
curl "localhost:8000/validate/SKP"
```

### Android client

```bash
cd mobile && npm install
npx expo start --android
```

The client points at `http://10.0.2.2:8000` (the Android emulator's alias for
host `localhost`). Change `expo.extra.apiBaseUrl` in `app.json` for a device or
a deployed backend.

---

## The model

Per flight *f* on day *d*:

```
Pax(f,d) = Seats(f) x LF(f,d) x Flown(f,d) x ShowUp(f,d)
           x Growth(d) x Shock(d) x Season(d) x Calib(a)
```

Airport throughput uses the ACI convention (arrivals + departures, transfer
passengers counted in both directions).

| Component | Distribution | Why |
|---|---|---|
| Load factor | Beta, via one-factor Gaussian copula | Bounded and skewed; correlated across flights |
| Cancellation | Bernoulli, weather-conditioned | Discrete, and spikes jointly under disruption |
| Weather | 3-state Markov chain | Persistent -- storms do not resample daily |
| Demand shock | Poisson x LogNormal x Geometric | Rare, fat-tailed, sticky |
| Growth | Hierarchical Student-t | Aviation tails are fatter than Normal allows |

### The decision that matters most

Independent load factors make the sum over flights collapse by the Central
Limit Theorem, producing a 90% interval a few hundred passengers wide on a
40,000-passenger day. Real airports do not behave that way. The one-factor
copula fixes it:

```
Z_f = sqrt(rho_common) * M
    + sqrt(rho_group - rho_common) * C_g(f)
    + sqrt(1 - rho_group) * E_f
```

Measured effect, isolated at a one-day horizon with shocks disabled: the
correlated band is ~1.8x wider than the independent one.

**But this is horizon-dependent, and it changes where calibration money should
go.** At 180 days, growth and shock variance dominate and disabling the copula
narrows the band by only ~44%. Load-factor calibration is worth paying for if
you sell short-horizon operational planning; it is close to irrelevant for
annual capacity planning. Both cases are pinned down as tests.

---

## Performance

The first working version took 4.4 s for a 48-flight airport. The cost was
`scipy.stats.beta.ppf` over an (iterations x flights) array every simulated day.

Three fixes:

1. **Tabulate the transform.** `LF = F_beta^-1(Phi(Z))` is a fixed monotone
   scalar function, and a schedule contains only ~5 distinct (alpha, beta)
   pairs. One interpolation table per pair replaces two transcendental calls
   with one interpolation. Max error < 1e-4 load-factor points.
2. **Drop per-flight show-up noise above 200 flights.** Measured contribution to
   the sd of daily throughput at 1,100 flights: 0.035%.
3. **Use means, not medians, for the hourly grid.** Hourly load is a *sum* over
   flights, and the median of a sum is not the sum of medians -- accumulating
   per-flight medians never produced a coherent quantity. This was a
   correctness fix that happened to also be 12x faster.

| Airport | Flights | Before | After |
|---|---|---|---|
| SKP | 48 | 4,412 ms | 117 ms |
| VIE | 460 | — | 728 ms |
| LHR | 1,300 | 7,267 ms | 2,155 ms |
| LAX | 1,600 | 9,488 ms | 2,419 ms |

Iteration count is adaptive: sampling stops when the relative standard error of
the **P90** falls below 0.5%, because P90 is the number operators actually staff
against.

---

## Validation

Two things need validating and the code keeps them separate, because reporting
one as the other would be dishonest:

**(a) Machinery calibration** — given a data-generating process, does the stated
P90 behave like a P90? Testable today with no external data.

```
coverage  50% nominal : 49.5%
coverage  80% nominal : 78.8%
coverage  90% nominal : 88.7%
PIT uniformity (KS)   : D=0.029  p=0.95
CRPS skill vs point forecast : 26.5%
CRPS skill vs informed day-of-week baseline : ~3%
verdict : CALIBRATED
```

Inject a +12% unmodelled demand shift and the diagnostics correctly **fail** —
coverage collapses to 20%, PIT piles into the top bin, verdict flips to
MISCALIBRATED. That is the evidence the harness is not self-flattering.

**(b) Empirical accuracy** — do the *priors* describe real airports? Not
testable without real historical throughput. `validation.backtest()` is the
rolling-origin harness that will consume Eurostat `avia_paoc` and BTS T-100 in
Phase 1. It is wired and tested but currently has no real series to score.

### Calibration anchor

Synthetic schedules reproduce the right structure but an arbitrary level, so
`seed.solve_calibration()` solves in closed form for the single multiplicative
factor that reconciles expected annual throughput with published ACI/Eurostat
totals. Result across the catalogue: within 3–5%.

| Airport | Implied annual | Published |
|---|---|---|
| SKP | 2.7 M | 2.6 M |
| OHD | 0.3 M | 0.3 M |
| VIE | 31.9 M | 31.0 M |
| LHR | 85.3 M | 83.0 M |

---

## Known limitations

- **Schedules are synthesised, not licensed.** Structure is realistic; the
  specific flights are not real. Production needs OAG/Cirium, or reconstruction
  from OpenSky ADS-B plus fleet seat configurations.
- **No empirical backtest yet.** See (b) above.
- **Transfer share is modelled but not yet used** to separate terminal
  throughput from O&D demand.
- **`/validate` is a public endpoint.** Exposing calibration diagnostics is a
  deliberate honesty signal, but it will report LOW confidence on thin-data
  airports like OHD. That is correct, and it may be a hard sell.

---

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/airports?q=` | Search catalogue |
| GET | `/airports/{iata}` | Detail incl. carrier mix |
| GET | `/forecast/{iata}` | Baseline forecast |
| POST | `/forecast` | Forecast with a scenario |
| POST | `/compare` | Baseline + scenario, shared seed |
| GET | `/presets` | Scenario presets |
| GET | `/validate/{iata}` | Calibration diagnostics |

`/compare` returns both runs from one call deliberately: independent runs would
differ by Monte Carlo noise, and users would read that noise as scenario effect.

---

## Design handoff

To connect this codebase to a design made in **Claude Design**, work from
Claude Code rather than the web chat:

```bash
claude mcp add --scope user --transport http \
  claude-design https://api.anthropic.com/v1/design/mcp
# then, inside Claude Code:
/design-login
/design-sync
```

Source: https://support.claude.com/en/articles/14604416-get-started-with-claude-design


---

## Adding airports and data

The catalogue is no longer a hardcoded list. It lives in SQLite, is seeded from
the built-in catalogue on first run, and is extended through the API or the app.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/airports` | Add an airport |
| PATCH | `/airports/{iata}` | Edit a user-added airport |
| DELETE | `/airports/{iata}` | Remove a user-added airport |
| POST | `/airports/{iata}/flights` | Add schedule rows as JSON |
| POST | `/airports/{iata}/flights/csv` | Bulk CSV import |
| DELETE | `/airports/{iata}/flights` | Clear the schedule, optionally by source |
| POST | `/airports/{iata}/checkpoints` | Add a security checkpoint |
| GET | `/airports/{iata}/checkpoints` | List checkpoints with fitted parameters |
| POST | `/checkpoints/{id}/observations` | Report an observed wait |
| GET | `/checkpoints/{id}/observations` | Recent reports |
| POST | `/checkpoints/{id}/recalibrate` | Force a refit |

### CSV format

```
flight_no,carrier,carrier_type,other_endpoint,direction,seats,sched_time,dow
W64301,W6,LCC,VIE,DEP,230,06:35,1234567
JU0170,JU,REGIONAL,BEG,DEP,76,17:05,"Mo,Tu,We,Th,Fr"
```

`sched_time` accepts `HH:MM` or minutes past midnight. `dow` accepts a 7-bit
mask, an IATA digit string (1 = Monday), or day names. Import is **idempotent**:
the unique constraint on `(airport, flight_no, direction, sched_minute)` means
re-importing an updated file adds only what is new.

Bad rows are rejected individually with their line number and reason; the rest
of the file still imports. A row with an unquoted comma is rejected rather than
reassembled, because guessing which column overflowed could silently move a seat
count into the time field.

### Guardrails

- IATA/ICAO format, coordinate ranges, and capacity are validated per field
- `(0, 0)` is rejected as an unfilled form rather than accepted as the Gulf of Guinea
- An airport within ~5 km of an existing one is refused as a probable duplicate
- Seeded catalogue airports are read-only
- An airport with no schedule refuses to forecast, and says what to do instead
- User-supplied data is capped at `data_quality` 0.62 unverified, 0.75 verified —
  it never reaches the confidence of a licensed source

### Checkpoint calibration

Checkpoints carry a **prior** and a **fitted posterior**. Reports flow in and the
fit shrinks away from the prior as evidence accumulates:

```
w      = n / (n + kappa)
mu_hat = w * mu_observed + (1 - w) * mu_prior
```

`kappa` is 8 for the median and 25 for dispersion, because sigma is harder to
estimate than location. This matters: fitting two observations by maximum
likelihood yields a near-zero sigma, i.e. a distribution claiming the wait is
*certain* — the worst possible failure for an app selling honest uncertainty.

Observations are also de-peaked by hour (so a rush-hour-skewed sample does not
inflate the base), weighted by source (sensor > operator > crowd), decayed with
a 60-day half-life, and winsorised at the 5th/95th percentile.

Measured convergence, recovering a true `base=24.0, sig=0.55`:

| reports | fitted base | observation weight |
|---|---|---|
| 1 | barely moves | 11% |
| 8 | blending | 46% |
| 45 | close | 83% |
| 400 | within 15% | 98% |

### The join between the two halves

A new checkpoint's prior is derived from the airport's **own modelled peak load**
via `suggest_prior_from_throughput()`, rather than a generic constant. The
throughput engine emits hourly passenger load; a checkpoint is a queue served at
a known rate.

This deliberately does **not** use M/M/c. At a realistic 55–75% mean utilisation
a stationary queueing formula predicts waits of a few seconds, because it assumes
Poisson arrivals. Real security queues are transient overloads driven by flight
banks, so the dominant term is a deterministic fluid surge, with a Kingman term
for variability away from the bank and a floor for divest and screening.

Every constant in it is an engineering estimate, not a fitted value. It is a
**prior only** and is labelled as unvalidated in its own docstring and API
response. Once reports arrive, it stops mattering.
