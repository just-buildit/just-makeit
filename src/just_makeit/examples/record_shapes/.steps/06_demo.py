"""Read one ring back three ways."""

import sys

sys.path.insert(0, "src")

import numpy as np  # noqa: E402

from evlog import Collector  # noqa: E402

c = Collector()
for x in (0.5, 2.5, 0.25, 1.75):
    c.step(x)

# ── shape 1: ONE record ──────────────────────────────────────────────────
s = c.summary()
print(f"summary()  -> {s}")
print(f"             type={type(s).__name__}  n={s.n}  mean={s.mean:.4f}")
assert s.n == 4
assert abs(s.mean - 1.25) < 1e-12
# A PyStructSequence: named fields AND tuple indexing.
assert s[0] == s.n and s[1] == s.mean

# ── shape 2: an ARRAY of records ─────────────────────────────────────────
rows = c.read()
print(f"read()     -> {rows!r}")
print(f"             dtype={rows.dtype}")
assert isinstance(rows, np.ndarray)
# The dtype was built by the generated C at runtime, from offsetof/sizeof on
# the author's struct -- jm never saw these names in a type context.
assert rows.dtype.names == ("t", "v")
assert rows.shape == (4,)
assert rows["t"].tolist() == [0, 1, 2, 3]
assert np.allclose(rows["v"], [0.5, 2.5, 0.25, 1.75])

# ── shape 3: a list of tuples ────────────────────────────────────────────
pk = c.peaks()
print(f"peaks()    -> {pk!r}")
assert isinstance(pk, list) and all(isinstance(p, tuple) for p in pk)
# Only values above the kernel's threshold, as (index, value) pairs.
assert pk == [(1, 2.5), (3, 1.75)]

print("record_shapes demo: PASSED")
