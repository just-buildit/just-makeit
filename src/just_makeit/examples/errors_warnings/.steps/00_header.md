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
four are pure glue — **no sacred file is touched by declaring them**, which is
the point: they are a translation layer over signals your C already emits.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example errors_warnings
# errors_warnings: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```
