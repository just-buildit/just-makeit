# varargs_method example

A `filter` object whose runtime configuration is updated through
`configure(**kwargs)`.  Typed `--param` flags work well when the parameter
set is fixed at code-generation time; `--varargs` is the right tool when it
is open-ended, mixed-type, or evolves independently of the scaffold.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example varargs_method
# varargs_method: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip`, which also works on Python 3.9 and 3.10 (the installer
needs 3.11+). It installs just-makeit, then builds the toolchain venv at
`/tmp/jm-venv`:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```
