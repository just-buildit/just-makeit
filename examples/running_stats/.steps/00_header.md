# running_stats example

Welford's online algorithm — streaming mean and variance over any sequence of
real-valued samples.  Useful anywhere you need live statistics without storing
the full dataset: monitoring, data pipelines, scientific computing, control loops.

Follow along to scaffold, implement, build, and use it yourself.

## TL;DR — see it work first

```sh
git clone https://github.com/just-buildit/just-makeit
cd just-makeit
uvx git+https://github.com/just-buildit/just-makeit install-deps
source /tmp/jm-venv/bin/activate
python3 examples/running_stats/test.py
# running_stats: PASSED
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
