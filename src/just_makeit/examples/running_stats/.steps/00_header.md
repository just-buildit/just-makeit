# running_stats example

Welford's online algorithm — streaming mean and variance over any sequence of
real-valued samples.  Useful anywhere you need live statistics without storing
the full dataset: monitoring, data pipelines, scientific computing, control loops.

Follow along to scaffold, implement, build, and use it yourself.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh)
just-makeit example running_stats
# running_stats: PASSED
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
