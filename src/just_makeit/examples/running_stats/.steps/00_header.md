# running_stats example

Welford's online algorithm — streaming mean and variance over any sequence of
real-valued samples.  Useful anywhere you need live statistics without storing
the full dataset: monitoring, data pipelines, scientific computing, control loops.

Follow along to scaffold, implement, build, and use it yourself.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example running_stats
# running_stats: PASSED
```

## Prerequisites

With jbx, the recommended way on every platform
([getting jbx](https://just-buildit.github.io/just-bashit/just-runit/#getting-just-runit)):

```sh
jbx just-makeit:install
source /tmp/jm-venv/bin/activate   # Linux and macOS
```

Or with the curl installer, which also activates the venv:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip`, which installs just-makeit and then builds the toolchain
venv at `/tmp/jm-venv`:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

On Windows, from PowerShell without jbx (see
[Building on Windows](../windows.md)):

```powershell
irm https://just-buildit.github.io/just-makeit/install.ps1 | iex
```
