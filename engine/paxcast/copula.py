"""
Dependence structure for the PaxCast engine.

Why this file exists
--------------------
If flight load factors are sampled independently, the sum over ~1,000 daily
flights concentrates by the Central Limit Theorem and the simulated
distribution of daily throughput becomes absurdly narrow -- a 90% interval a
few hundred passengers wide on a 40,000-passenger day. Real airports do not
behave that way, because load factors move together: a weak booking month, a
national holiday, a fuel-price shock or a competitor's capacity dump moves
every flight on the field in the same direction at once.

We impose that dependence with a Gaussian copula built on a one-factor
structure, which is O(n) rather than O(n^2) and needs no Cholesky factor of a
1000x1000 matrix per iteration:

    Z_f = sqrt(rho_common) * M           (airport-day common factor)
        + sqrt(rho_group - rho_common) * C_g(f)   (carrier/route group factor)
        + sqrt(1 - rho_group) * E_f      (flight idiosyncratic)

Each term is standard Normal, so Z_f is standard Normal by construction, and
U_f = Phi(Z_f) are uniform marginals with the desired correlation -- ready to
feed into any inverse-CDF.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm


class OneFactorCopula:
    """One-factor Gaussian copula with a nested group level.

    Parameters
    ----------
    rho_common:
        Correlation between any two flights at the airport on the same day.
        Empirically ~0.25-0.40 for load factors.
    rho_group:
        Correlation between two flights in the same group (same carrier, or
        same route). Must be >= rho_common. Typically ~0.55-0.70.
    """

    def __init__(self, rho_common: float = 0.30, rho_group: float = 0.60):
        if not 0.0 <= rho_common <= 1.0:
            raise ValueError("rho_common must be in [0,1]")
        if rho_group < rho_common:
            raise ValueError("rho_group must be >= rho_common")
        if rho_group >= 1.0:
            raise ValueError("rho_group must be < 1")
        self.rho_common = rho_common
        self.rho_group = rho_group

    def uniforms(
        self,
        n_iter: int,
        group_ids: np.ndarray,
        rng: np.random.Generator,
        common: np.ndarray | None = None,
    ) -> np.ndarray:
        """Generate correlated uniforms of shape (n_iter, n_flights).

        Parameters
        ----------
        group_ids:
            Integer group label per flight, shape (n_flights,). Flights sharing
            a label get the elevated `rho_group` correlation.
        common:
            Optional externally supplied common factor of shape (n_iter,).
            Passing the *same* factor across days is how multi-day persistence
            in demand is achieved; passing None makes days independent.
        """
        n_flights = group_ids.shape[0]
        n_groups = int(group_ids.max()) + 1 if n_flights else 0

        w_common = np.sqrt(self.rho_common)
        w_group = np.sqrt(max(self.rho_group - self.rho_common, 0.0))
        w_idio = np.sqrt(max(1.0 - self.rho_group, 1e-12))

        m = rng.standard_normal((n_iter, 1)) if common is None else common.reshape(-1, 1)
        g = rng.standard_normal((n_iter, n_groups))[:, group_ids] if n_groups else 0.0
        e = rng.standard_normal((n_iter, n_flights))

        z = w_common * m + w_group * g + w_idio * e
        return norm.cdf(z)

    def implied_sum_cv_inflation(self, n_flights: int) -> float:
        """Diagnostic: how much wider the sum's sd is versus independence.

        For an equicorrelated sum of n identically distributed terms:
            var_corr / var_indep = 1 + (n - 1) * rho
        so the sd inflation factor is sqrt(1 + (n-1) * rho). At n=1000 and
        rho=0.30 this is roughly 17x -- which is precisely the point.
        """
        return float(np.sqrt(1.0 + (n_flights - 1) * self.rho_common))
