# fir_filter example

A 16-tap, real-coefficient FIR filter that processes complex (I/Q) signals.
Follow along to scaffold, implement, build, and use it yourself.

## TL;DR — see it work first

```sh
uvx git+https://github.com/just-buildit/just-makeit install-deps
source /tmp/jm-venv/bin/activate
just-makeit example fir_filter
# fir_filter: PASSED
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
