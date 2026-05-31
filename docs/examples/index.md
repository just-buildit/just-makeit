# Examples

Each example is a complete, buildable project that walks through a real
algorithm from scaffold to optimised implementation.

## Scaffold and lifecycle

| Example                                         | What it demonstrates                                                                                                                                                                                                        |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Full workflow](full_workflow.md)               | A complete development lifecycle walkthrough — scaffold two components with **both** test and benchmark styles, implement, test, benchmark, measure coverage, and publish API docs — all from a single just-makeit project. |
| [DSP toolkit](dsp_toolkit.md)                   | A two-component DSP library built with `just-makeit`: a `Gain` component and an `Ema` (exponential moving average) component.                                                                                               |
| [Accumulator](accumulator.md)                   | A two-type module (`AccF32` and `AccCf64`) each with five methods — the most comprehensive scaffold-and-implement example.                                                                                                  |
| [Declarative scaffold](declarative_scaffold.md) | Author a complete component — state, types, and inline `step()` body — in a single TOML fragment, then `jm apply` it into a buildable extension.                                                                            |

## DSP algorithms

| Example                                     | What it demonstrates                                                                                                                                                                                            |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [FIR filter](fir_filter.md)                 | A 16-tap, real-coefficient FIR filter that processes complex (I/Q) signals.                                                                                                                                     |
| [Running stats](running_stats.md)           | Welford's online algorithm — streaming mean and variance over any sequence of real-valued samples.                                                                                                              |
| [Sliding power](sliding_power.md)           | Estimate the instantaneous power of a signal over a rolling window of N samples.                                                                                                                                |
| [Sliding correlator](sliding_correlator.md) | A sliding correlator computes the cross-correlation between a running input window and a fixed reference sequence.                                                                                              |
| [Stream chunker](stream_chunker.md)         | A stream re-framer: accepts samples in arbitrary-size bursts and emits them as fixed-size chunks.                                                                                                               |
| [IQ file](iqfile.md)                        | A block-wise converter between **cf32** (complex float-32, 8 bytes/sample) and **q15** (complex signed 16-bit fixed-point, 4 bytes/sample) — the two most common raw IQ file formats in software-defined radio. |
| [NCO tone](nco_tone.md)                     | Wire a just-makeit object to an external C library (Doppler NCO) via `find_package` and `extra_link_libs`.                                                                                                      |

## Opaque state and heap lifecycle

| Example                             | What it demonstrates                                                                                                                              |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Opaque counter](opaque_counter.md) | The simplest possible opaque-state object: a heap-allocated counter with `create_impl` / `destroy_impl` and no auto-generated getters.            |
| [Delay line](delay_line.md)         | A circular delay buffer with runtime-configurable length — `create_impl`, `reset_impl`, and `destroy_impl` with a heap-allocated `float *` field. |

## Modules and functions

| Example                                 | What it demonstrates                                                                                                                      |
| --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| [Filter module](filter_module.md)       | A two-type filter library where `Fir` (FIR filter) and `Biquad` (biquad IIR) live together in a single `filter` Python extension module.  |
| [Module functions](jm_function.md)      | Add stateless C functions to a module — a regular function (own `.c` file) and a `--inline` variant (static inline in the module header). |
| [Array processing](array_processing.md) | Every object just-makeit generates can process a block of samples in one call.                                                            |

## Application scaffolding and tooling

| Example                         | What it demonstrates                                                                                                                 |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| [App targets](jm_app.md)        | Three application entry points from one component: C executable, Python console script with `argparse`, and a PEP 723 inline script. |
| [pytest style](pytest_style.md) | Demonstrates the `--pytest` and `--pytest-benchmark` flags introduced in just-makeit 0.11.                                           |

All examples ship with end-to-end tests in `examples/*/test.py` that are
run by the CI suite. See `examples/README.md` for contributor notes on the
`.steps/` naming convention.
