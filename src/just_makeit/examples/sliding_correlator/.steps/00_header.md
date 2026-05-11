# sliding_correlator example

A sliding correlator computes the cross-correlation between a running input
window and a fixed reference sequence:

```
y[n] = Σ  conj(ref[k]) · x[n−k]
       k=0..N-1
```

When `ref` equals the complex conjugate of a known waveform, the output peaks
whenever that waveform appears in the input.  Practical uses include preamble
detection, CDMA despreading, and radar/sonar matched filtering.

The goal of this example is to show that `JM_DEFINE_STEPS` is algorithm-agnostic:
the correlator uses complex multiply (not real×complex like FIR), a different
state layout, and different semantics — and the macro handles it identically.

## TL;DR — see it work first

```sh
uvx git+https://github.com/just-buildit/just-makeit install-deps
source /tmp/jm-venv/bin/activate
just-makeit example sliding_correlator
# sliding_correlator: PASSED
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
