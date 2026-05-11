# dsp_toolkit example

A two-component DSP library built with `just-makeit`: a `Gain` component and
an `Ema` (exponential moving average) component.

Follow along to scaffold, implement, and combine them — and see the one place
the generator currently needs a manual touch when you add a second component.

## Prerequisites

```sh
pip install just-makeit
jm-install-deps --check      # report what is installed vs. what will be installed
jm-install-deps              # install cmake, C compiler, numpy, and create a venv
source /tmp/jm-venv/bin/activate
```

Pass a custom path to keep the venv somewhere persistent:

```sh
jm-install-deps ~/my-venv && source ~/my-venv/bin/activate
```
