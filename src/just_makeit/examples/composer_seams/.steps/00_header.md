# composer_seams example

A `kind = "composer"` module — the third and largest of the object-of-objects
generators — and specifically **the two seams where it hands work back to you
as plain C** (gh-998).

A composer emits four CPython types into one `.so`: a *source* (`Clip`), a
*segment* (`Track`), a *timeline*, and the *composer* itself (`Mix`). All of
that binding is jm's. Two things are not, and they are the interesting part:

| seam | declared by | what you write |
| --- | --- | --- |
| build the generator from a source config | `[module.X.source.generates] bridge_fn` | `<gen>_state_t *fn(const <struct> *, double)` |
| derive a read-only property | `[[module.X.source.computed]] fn` | `<type> fn(const <struct> *)` |

Both are **straight C with no CPython in them**. jm knows their signatures
exactly, so it publishes them in a generated header —
`native/inc/<mod>/<mod>_bridge.h` — and the binding includes that rather than
re-declaring them. Before gh-998 they were `extern` lines buried inside the
generated `_ext.c`, so a C test or benchmark could reach a signature jm owns
only by writing a second copy of it.

That header is the one file this example is really about, and step 7 uses it
the way it is meant to be used: from a translation unit that includes nothing
else.

A composer is also **manifest-only** — there is no `jm composer` command. You
write the table and run `just-makeit apply`.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example composer_seams
# composer_seams: PASSED
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
