# Examples

Each example is a complete, buildable project that walks through a real
algorithm from scaffold to optimised implementation.

| Example | What it demonstrates |
|---|---|
| [FIR filter](fir_filter.md) | Stateful step(), `just-makeit perf`, `JM_DEFINE_STEPS`, pure variant |
| [Sliding power](sliding_power.md) | `--return-type float`, `jm_simd.h` recompute, numerical drift analysis |
| [Sliding correlator](sliding_correlator.md) | Complex cross-correlation, `JM_DEFINE_STEPS` with a different state layout |
| [Running stats](running_stats.md) | Welford online mean/variance, multiple return values |

All examples ship with end-to-end tests in `examples/*/test.py` that are run
by the CI suite.  See `examples/README.md` for contributor notes on the
`.steps/` naming convention.
