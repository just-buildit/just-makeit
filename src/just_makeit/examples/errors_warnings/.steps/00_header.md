# errors_warnings example

The four ways a component can tell Python that something is wrong — and they
are four, not one, because C has more failure modes than it has channels to
report them on.

| what happened | how C says it | how Python hears it | declared by |
| --- | --- | --- | --- |
| `create()` refuses | returns `NULL` | an exception, at construction | `just-makeit error` |
| `create()` succeeded, with a caveat | a `bool` field on the state struct | a `warning`, after construction | `just-makeit warning` |
| a call fails | an `int` status, non-zero is bad | an exception; the method returns `None` | `--status-return --error` |
| a call fails, but usually returns a value | an `int` that is a value unless negative | an exception, or the `int` | `--error-negative --error` |

The first two are `create()`'s problem and the last two are a method's. All
four translations are pure glue — `error` and `warning` touch no sacred file
at all, and the two method flags change only the binding (`jm method` still
appends the method's C stub and prototype, as it always does), which is the
point: they are a translation layer over signals your C already emits.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example errors_warnings
# errors_warnings: PASSED
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
