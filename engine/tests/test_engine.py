"""Tests for the PaxCast Monte Carlo engine."""

from datetime import date

import numpy as np
import pytest

from paxcast import PaxCastEngine, SimulationConfig, get_airport
from paxcast.copula import OneFactorCopula
from paxcast.distributions import (
    CARRIER_LF_PRIORS,
    ShockProcess,
    WeatherChain,
    beta_from_moments,
    beta_moments,
)
from paxcast.models import Scenario
from paxcast.quantile_table import ZToBetaTable

START = date(2026, 9, 1)


def cfg(**kw):
    base = dict(start_date=START, horizon_days=14, n_iterations=2000, seed=11)
    base.update(kw)
    return SimulationConfig(**base)


# ---------------------------------------------------------------- distributions


@pytest.mark.parametrize("mean,sd", [(0.82, 0.09), (0.90, 0.06), (0.72, 0.12)])
def test_beta_reparameterisation_roundtrips(mean, sd):
    a, b = beta_from_moments(mean, sd)
    m2, s2 = beta_moments(a, b)
    assert m2 == pytest.approx(mean, abs=1e-9)
    assert s2 == pytest.approx(sd, abs=1e-9)


def test_beta_from_moments_clamps_impossible_sd():
    """A thin-data airport can produce an sd outside the Beta support."""
    a, b = beta_from_moments(0.5, 5.0)  # sd far above sqrt(m(1-m))
    assert a > 0 and b > 0 and np.isfinite(a) and np.isfinite(b)


def test_weather_chain_is_persistent():
    """Severe weather must be sticky, or multi-day tail risk is understated.

    The correct null is the chain's *stationary* distribution, not its initial
    distribution: an i.i.d. sampler drawing from stationary pi would show
    day-to-day agreement of sum(pi_i^2). Comparing against the initial vector
    instead understates the null, because the chain drifts toward more
    disrupted states than it starts in.
    """
    chain = WeatherChain.for_climate("harsh_winter")
    # Stationary distribution = left eigenvector of P for eigenvalue 1.
    vals, vecs = np.linalg.eig(chain.transition.T)
    pi = np.real(vecs[:, np.argmin(np.abs(vals - 1.0))])
    pi = pi / pi.sum()
    iid_agreement = float((pi**2).sum())

    rng = np.random.default_rng(0)
    states = chain.simulate(6000, 60, rng)
    observed = float((states[:, 30:] == states[:, 29:-1]).mean())
    assert observed > iid_agreement + 0.10, (observed, iid_agreement)


def test_shock_process_only_reduces_demand():
    rng = np.random.default_rng(3)
    f = ShockProcess(lam_per_year=40.0).simulate(500, 90, rng)
    assert f.max() <= 1.0 + 1e-6
    assert f.min() >= 0.0
    assert f.min() < 1.0  # at that rate, some shock must have landed


def test_shock_process_produces_fat_left_tail():
    """The 1st percentile must sit far further out than Normality allows.

    Under a Normal, the 1st percentile is 2.33 sd below the median. The shock
    process is the component that makes the model survive contact with events
    like 2010 Eyjafjallajokull or 2020, so its left tail must be visibly
    heavier than that. Measured: ~4.9 sd.
    """
    f = ShockProcess().simulate(20000, 365, np.random.default_rng(4))
    annual = f.mean(axis=1)
    p50, p1 = np.percentile(annual, 50), np.percentile(annual, 1)
    z_equivalent = (p50 - p1) / annual.std()
    assert z_equivalent > 3.5, z_equivalent


# ---------------------------------------------------------------- quantile table


@pytest.mark.parametrize("ctype", list(CARRIER_LF_PRIORS))
def test_table_matches_exact_ppf(ctype):
    mean, sd = CARRIER_LF_PRIORS[ctype]
    table = ZToBetaTable(*beta_from_moments(mean, sd))
    assert table.max_error() < 1e-4


def test_table_handles_extreme_inputs():
    table = ZToBetaTable(*beta_from_moments(0.82, 0.09))
    out = table(np.array([-1e9, -8.0, 0.0, 8.0, 1e9], dtype=np.float32))
    assert np.all(np.isfinite(out))
    assert np.all((out >= 0.0) & (out <= 1.0))


def test_table_is_monotone():
    table = ZToBetaTable(*beta_from_moments(0.82, 0.09))
    z = np.linspace(-5, 5, 5000, dtype=np.float32)
    assert np.all(np.diff(table(z)) >= -1e-6)


# ---------------------------------------------------------------- copula


def test_copula_marginals_are_uniform():
    rng = np.random.default_rng(2)
    u = OneFactorCopula().uniforms(20000, np.zeros(5, dtype=np.int64), rng)
    from scipy.stats import kstest

    assert kstest(u[:, 0], "uniform").pvalue > 0.01


def test_copula_induces_target_correlation():
    rng = np.random.default_rng(5)
    groups = np.arange(40, dtype=np.int64)  # every flight its own group
    u = OneFactorCopula(rho_common=0.30, rho_group=0.30).uniforms(60000, groups, rng)
    from scipy.stats import norm

    z = norm.ppf(np.clip(u, 1e-9, 1 - 1e-9))
    corr = np.corrcoef(z.T)
    off = corr[~np.eye(40, dtype=bool)]
    assert off.mean() == pytest.approx(0.30, abs=0.02)


def _band_width(res, d=0):
    return res.percentiles["p90"][d] - res.percentiles["p10"][d]


def test_correlation_widens_the_band_substantially():
    """The whole point of the copula: independence collapses the interval.

    Isolated properly -- shocks disabled and a one-day horizon -- so that
    load-factor dependence is the only thing varying. Under independence the
    sum over flights is CLT-narrow; correlation roughly doubles the band.
    """
    airport = get_airport("SKP")
    cf = cfg(horizon_days=1, n_iterations=8000, adaptive=False)
    sc = Scenario(disable_shocks=True)
    wide = PaxCastEngine(OneFactorCopula(0.30, 0.60)).simulate(airport, cf, sc)
    narrow = PaxCastEngine(OneFactorCopula(0.001, 0.002)).simulate(airport, cf, sc)
    assert _band_width(wide) > 1.6 * _band_width(narrow)


def test_shock_variance_dominates_at_long_horizon():
    """Characterises where the uncertainty actually comes from.

    At one day out, load-factor correlation is the dominant driver of the
    band. Over a long horizon, growth and shock uncertainty overtake it, so
    turning the copula off barely narrows the interval. This is worth pinning
    down as a test because it tells you which input is worth spending money to
    calibrate at which horizon.
    """
    airport = get_airport("SKP")
    cf = cfg(horizon_days=180, n_iterations=3000, adaptive=False)
    wide = PaxCastEngine(OneFactorCopula(0.30, 0.60)).simulate(airport, cf)
    narrow = PaxCastEngine(OneFactorCopula(0.001, 0.002)).simulate(airport, cf)
    d = 179
    ratio = _band_width(wide, d) / _band_width(narrow, d)
    assert ratio < 1.55, ratio


# ---------------------------------------------------------------- engine


def test_percentiles_are_ordered():
    res = PaxCastEngine().simulate(get_airport("VIE"), cfg())
    keys = ["p5", "p10", "p25", "p50", "p75", "p90", "p95"]
    for d in range(len(res.dates)):
        vals = [res.percentiles[k][d] for k in keys]
        assert vals == sorted(vals)


def test_deterministic_given_seed():
    a = get_airport("SKP")
    r1 = PaxCastEngine().simulate(a, cfg(seed=99))
    r2 = PaxCastEngine().simulate(a, cfg(seed=99))
    assert r1.percentiles["p50"] == r2.percentiles["p50"]


def test_different_seeds_give_close_but_distinct_results():
    a = get_airport("SKP")
    r1 = PaxCastEngine().simulate(a, cfg(seed=1))
    r2 = PaxCastEngine().simulate(a, cfg(seed=2))
    assert r1.percentiles["p50"] != r2.percentiles["p50"]
    rel = abs(r1.total_percentiles["p50"] - r2.total_percentiles["p50"]) / r1.total_percentiles["p50"]
    assert rel < 0.05


def test_output_shapes():
    res = PaxCastEngine().simulate(get_airport("BEG"), cfg(horizon_days=21))
    assert len(res.dates) == 21
    assert all(len(v) == 21 for v in res.percentiles.values())
    assert len(res.peak_hour_grid) == 7
    assert all(len(row) == 24 for row in res.peak_hour_grid)


def test_adaptive_stopping_meets_its_target():
    res = PaxCastEngine().simulate(get_airport("SKP"), cfg(n_iterations=60000))
    assert res.converged
    assert res.p90_rel_se <= 0.005
    assert res.n_iterations < 60000  # stopped early


def test_calibration_reconciles_with_published_annual_traffic():
    """Simulated throughput must land near the published ACI/Eurostat total."""
    engine = PaxCastEngine()
    for code in ["SKP", "OHD", "BEG", "VIE", "LHR"]:
        a = get_airport(code)
        res = engine.simulate(a, cfg(horizon_days=28, n_iterations=3000))
        implied_annual = res.total_percentiles["p50"] / 28 * 365
        ratio = implied_annual / a.annual_pax_baseline
        assert 0.85 < ratio < 1.20, f"{code}: implied/published = {ratio:.2f}"


# ---------------------------------------------------------------- scenarios


def test_load_factor_drop_reduces_throughput():
    a = get_airport("VIE")
    e = PaxCastEngine()
    base = e.simulate(a, cfg())
    down = e.simulate(a, cfg(), Scenario(name="LF -8pp", load_factor_delta=-0.08))
    assert down.total_percentiles["p50"] < base.total_percentiles["p50"] * 0.95


def test_grounding_a_carrier_reduces_throughput():
    a = get_airport("VIE")
    e = PaxCastEngine()
    base = e.simulate(a, cfg())
    carrier = a.flights[0].carrier
    out = e.simulate(a, cfg(), Scenario(name="grounded", grounded_carriers=(carrier,)))
    assert out.total_percentiles["p50"] < base.total_percentiles["p50"]


def test_atc_strike_widens_the_lower_tail():
    a = get_airport("CDG")
    e = PaxCastEngine()
    base = e.simulate(a, cfg())
    strike = e.simulate(a, cfg(), Scenario(name="ATC strike", extra_cancel_prob=0.35))
    assert strike.percentiles["p50"][0] < base.percentiles["p50"][0] * 0.75


def test_disabling_shocks_narrows_the_left_tail():
    a = get_airport("BCN")
    e = PaxCastEngine()
    cf = cfg(horizon_days=120, n_iterations=3000, adaptive=False)
    with_shocks = e.simulate(a, cf)
    without = e.simulate(a, cf, Scenario(name="no shocks", disable_shocks=True))
    spread_w = with_shocks.total_percentiles["p50"] - with_shocks.total_percentiles["p5"]
    spread_o = without.total_percentiles["p50"] - without.total_percentiles["p5"]
    assert spread_w > spread_o


def test_grounding_every_carrier_raises():
    a = get_airport("OHD")
    carriers = tuple({f.carrier for f in a.flights})
    with pytest.raises(ValueError):
        PaxCastEngine().simulate(a, cfg(), Scenario(grounded_carriers=carriers))


# ---------------------------------------------------------------- horizon


def test_uncertainty_grows_with_horizon():
    """A 12-month-out day must be less certain than tomorrow."""
    res = PaxCastEngine().simulate(
        get_airport("VIE"), cfg(horizon_days=365, n_iterations=3000, adaptive=False)
    )
    def rel_width(d):
        return (res.percentiles["p90"][d] - res.percentiles["p10"][d]) / max(
            res.percentiles["p50"][d], 1
        )
    early = np.mean([rel_width(d) for d in range(0, 14)])
    late = np.mean([rel_width(d) for d in range(350, 365)])
    assert late > early * 1.3


def test_confidence_label_respects_data_quality():
    from paxcast.seed import build_airport, CATALOGUE

    spec = list(next(s for s in CATALOGUE if s[0] == "SKP"))
    spec[13] = 0.30  # degrade data quality
    poor = build_airport(tuple(spec))
    res = PaxCastEngine().simulate(poor, cfg())
    assert res.confidence == "LOW"
