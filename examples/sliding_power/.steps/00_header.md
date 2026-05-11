# sliding_power — sliding window signal power estimator

Estimate the instantaneous power of a signal over a rolling window of N samples:

```
P[n] = (1/N) * sum( |x[n-k]|^2  for k in 0..N-1 )
```

Two update strategies are shown:

- **step()** — O(1) recursive: `sum_sq += new² − old²`
- **SIMD recompute** — horizontally sums the delay line with `JM_ADD_F32` /
  `JM_HSUM_F32` from `jm_simd.h`; used for periodic recalibration and as a
  clean demonstration of the v0.5 macro set.

## TL;DR — see it work first

```sh
git clone https://github.com/just-buildit/just-makeit
cd just-makeit
uvx git+https://github.com/just-buildit/just-makeit install-deps
source /tmp/jm-venv/bin/activate
python3 examples/sliding_power/test.py
# sliding_power: PASSED
```

## Prerequisites

```sh
pip install just-makeit
just-makeit install-deps --check   # report what is installed vs. what will be installed
just-makeit install-deps           # install cmake, C compiler, numpy, and create a venv
source /tmp/jm-venv/bin/activate
```

Pass a custom path to keep the venv somewhere persistent:

```sh
just-makeit install-deps ~/my-venv && source ~/my-venv/bin/activate
```
