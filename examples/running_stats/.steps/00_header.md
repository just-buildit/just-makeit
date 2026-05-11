# running_stats example

Welford's online algorithm — streaming mean and variance over any sequence of
real-valued samples.  Useful anywhere you need live statistics without storing
the full dataset: monitoring, data pipelines, scientific computing, control loops.

Follow along to scaffold, implement, build, and use it yourself.

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
