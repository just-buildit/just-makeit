# dsp_toolkit example

A two-component DSP library built with `just-makeit`: a `Gain` component and
an `Ema` (exponential moving average) component.

Follow along to scaffold, implement, and combine them — and see the one place
the generator currently needs a manual touch when you add a second component.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh)
just-makeit example dsp_toolkit
# dsp_toolkit: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh) -- ~/my-venv
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```
