# fir_filter example

A 16-tap, real-coefficient FIR filter that processes complex (I/Q) signals.
Follow along to scaffold, implement, build, and use it yourself.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example fir_filter
# fir_filter: PASSED
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

Pass a path to put the venv somewhere else:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
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
