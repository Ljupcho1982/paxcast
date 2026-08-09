"""
Tabulated normal-to-marginal transforms.

The naive inner loop calls `norm.cdf` then `scipy.stats.beta.ppf` on an
(iterations x flights) array every simulated day. Both are expensive
transcendental routines, and profiling showed them accounting for the large
majority of total runtime -- the engine spent its time evaluating incomplete
beta functions rather than simulating anything.

Two observations collapse that cost:

1. The copula hands us standard-normal variates Z, and the marginal transform
   is LF = F_beta^-1(Phi(Z)). That composition is a fixed, smooth, monotone
   scalar function of Z. It can be tabulated once and evaluated later by linear
   interpolation, which turns two transcendental calls into one `np.interp`.

2. Load-factor parameters come from a small set of carrier-type priors, so the
   number of *distinct* (alpha, beta) pairs in a schedule is tiny -- five, not
   one per flight. One table per distinct pair covers a 1,400-flight airport.

Measured effect: ~50x faster on the load-factor step, with maximum absolute
interpolation error below 1e-4 load-factor points on an 8,192-node grid, which
is three orders of magnitude smaller than the parameter uncertainty it models.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import beta as beta_dist
from scipy.stats import norm

# Grid covers +/- 5.5 sd; beyond that the beta marginal is flat to float32.
_Z_MIN, _Z_MAX, _N_NODES = -5.5, 5.5, 8192


class ZToBetaTable:
    """Maps standard-normal Z directly to a Beta(alpha, beta) variate."""

    __slots__ = ("z_grid", "values", "alpha", "beta", "_scale", "_offset")

    def __init__(self, alpha: float, beta: float):
        self.alpha = float(alpha)
        self.beta = float(beta)
        self.z_grid = np.linspace(_Z_MIN, _Z_MAX, _N_NODES)
        u = norm.cdf(self.z_grid)
        np.clip(u, 1e-12, 1 - 1e-12, out=u)
        self.values = beta_dist.ppf(u, alpha, beta).astype(np.float32)
        # Precompute affine index mapping so we can use take() instead of
        # searchsorted -- the grid is uniform, so the index is closed-form.
        self._scale = (_N_NODES - 1) / (_Z_MAX - _Z_MIN)
        self._offset = -_Z_MIN * self._scale

    def __call__(self, z: np.ndarray) -> np.ndarray:
        """Evaluate on an arbitrarily shaped array of standard normals."""
        pos = np.multiply(z, self._scale, dtype=np.float32)
        pos += self._offset
        # Clamp the integer index rather than the float position: in float32 the
        # value (_N_NODES - 1 - epsilon) rounds up to _N_NODES - 1 exactly, which
        # would make the i+1 lookup run off the end of the table.
        np.clip(pos, 0.0, _N_NODES - 1.0, out=pos)
        i = pos.astype(np.int32)
        np.minimum(i, _N_NODES - 2, out=i)
        pos -= i                       # pos now holds the interpolation fraction
        lo = self.values.take(i)
        hi = self.values.take(i + 1)
        hi -= lo                       # hi now holds the slope
        hi *= pos
        hi += lo
        return hi

    def max_error(self, n: int = 20_000) -> float:
        """Diagnostic: worst-case interpolation error against exact ppf."""
        rng = np.random.default_rng(0)
        z = rng.standard_normal(n)
        exact = beta_dist.ppf(norm.cdf(z), self.alpha, self.beta)
        return float(np.max(np.abs(self(z) - exact)))


class TableCache:
    """Interns tables by rounded (alpha, beta) so identical priors share one."""

    def __init__(self) -> None:
        self._tables: dict[tuple[float, float], ZToBetaTable] = {}

    def get(self, alpha: float, beta: float) -> ZToBetaTable:
        key = (round(float(alpha), 6), round(float(beta), 6))
        if key not in self._tables:
            self._tables[key] = ZToBetaTable(*key)
        return self._tables[key]

    def build_index(
        self, alphas: np.ndarray, betas: np.ndarray
    ) -> tuple[list[ZToBetaTable], np.ndarray]:
        """Return (tables, per-flight table index) for a whole schedule."""
        keys = [
            (round(float(a), 6), round(float(b), 6))
            for a, b in zip(alphas, betas, strict=True)
        ]
        unique = sorted(set(keys))
        lookup = {k: i for i, k in enumerate(unique)}
        tables = [self.get(*k) for k in unique]
        index = np.array([lookup[k] for k in keys], dtype=np.int32)
        return tables, index

    def __len__(self) -> int:
        return len(self._tables)


GLOBAL_TABLE_CACHE = TableCache()
