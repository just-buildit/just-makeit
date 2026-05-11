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
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example sliding_correlator
# sliding_correlator: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```
