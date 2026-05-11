# fir_filter example

A 16-tap, real-coefficient FIR filter that processes complex (I/Q) signals.
Follow along to scaffold, implement, build, and use it yourself.

## TL;DR — see it work first

```sh
curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh | sh
source /tmp/jm-venv/bin/activate
just-makeit example fir_filter
# fir_filter: PASSED
```

## Prerequisites

```sh
curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh | sh
source /tmp/jm-venv/bin/activate
```

Pass a custom path to keep the venv somewhere persistent:

```sh
curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh | sh -s -- ~/my-venv
source ~/my-venv/bin/activate
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit
just-makeit install-deps
source /tmp/jm-venv/bin/activate
```
