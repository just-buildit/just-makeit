# Stream source example

A **source** is an object that generates samples from internal state with no
input — `steps(n)` hands you `n` fresh samples. Mark it `--streamable` and
just-makeit generates a Pythonic block iterator for free, so instead of the
hand-rolled pull loop:

```python
while True:
    block = osc.steps(256)
    consume(block)
```

you write:

```python
for block in osc.stream(256):
    consume(block)
```

This example builds a free-running ramp oscillator, marks it streamable, and
walks through everything the generator gives you: `stream(block)`, the
`count` cap, the `on_block` hook, and `__iter__`.

The generated `Ramp` class also carries a real one-sentence summary rather
than the generic `Ramp component.` fallback — jm lifts it straight from the
`@brief` you write above `stream_source_demo_ramp_create` in the sacred `ramp_core.h` header.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example stream_source
# stream_source: PASSED
```

## Prerequisites

With jbx, the recommended way on every platform
([getting jbx](https://just-buildit.github.io/just-bashit/just-runit/#getting-just-runit)):

```sh
jbx just-makeit:install
```

Run in a project's root, it installs into that project's `.venv`; anywhere else, if `jm` is not found in a new shell, run `jbx update-path` once.

Or with the curl installer, sourced, which also activates the venv
in this shell:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Or with `pip`, which installs just-makeit and then the build toolchain
and a venv, printing the line that activates it:

```sh
pip install just-makeit && just-makeit install-deps
```

On Windows, from PowerShell without jbx (see
[Building on Windows](../windows.md)):

```powershell
irm https://just-buildit.github.io/just-makeit/install.ps1 | iex
```
