# Examples

Each example is a complete, buildable project that walks through a real
algorithm from scaffold to optimised implementation.

| Example                | What it demonstrates                                                                                                                                                                                                        |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Running stats](running_stats.md) | Welford's online algorithm — streaming mean and variance over any sequence of real-valued samples.                                                                                                                          |
| [FIR filter](fir_filter.md) | A 16-tap, real-coefficient FIR filter that processes complex (I/Q) signals.                                                                                                                                                 |
| [Sliding power](sliding_power.md) | Estimate the instantaneous power of a signal over a rolling window of N samples:                                                                                                                                            |
| [Sliding correlator](sliding_correlator.md) | A sliding correlator computes the cross-correlation between a running input window and a fixed reference sequence:                                                                                                          |
| [Array processing](array_processing.md) | Every object just-makeit generates can process a block of samples in one call.                                                                                                                                              |
| [Stream chunker](stream_chunker.md) | A stream re-framer: accepts samples in arbitrary-size bursts and emits them as fixed-size chunks.                                                                                                                           |
| [DSP toolkit](dsp_toolkit.md) | A two-component DSP library built with `just-makeit`: a `Gain` component and an `Ema` (exponential moving average) component.                                                                                               |
| [Filter module](filter_module.md) | A two-type filter library where `Fir` (FIR filter) and `Biquad` (biquad IIR) live together in a single `filter` Python extension module.                                                                                    |
| [IQ file](iqfile.md)   | A block-wise converter between **cf32** (complex float-32, 8 bytes/sample) and **q15** (complex signed 16-bit fixed-point, 4 bytes/sample) — the two most common raw IQ file formats in software-defined radio.             |
| [pytest style](pytest_style.md) | Demonstrates the `--pytest` and `--pytest-benchmark` flags introduced in just-makeit 0.11.                                                                                                                                  |
| [Full workflow](full_workflow.md) | A complete development lifecycle walkthrough — scaffold two components with **both** test and benchmark styles, implement, test, benchmark, measure coverage, and publish API docs — all from a single just-makeit project. |
| [Composites](composites.md) | This example builds one jm project that demonstrates the **object-of-objects `kind = "handle"` generator**: a single typed CPython class generated over an opaque hand-C resource handle, with RAII lifetime.               |

All examples ship with end-to-end tests in `examples/*/test.py` that are
run by the CI suite. See `examples/README.md` for contributor notes on the
`.steps/` naming convention.
