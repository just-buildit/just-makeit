# declarative_scaffold example

Author a complete component in a single TOML fragment — state, types, and the
`step()` body inline — then `jm apply` it into a buildable Python extension.
Also demonstrates `jm split-objects` to migrate an existing single-manifest
project onto the per-component fragment layout.

## TL;DR — see it work first

```sh
just-makeit example declarative_scaffold
# declarative_scaffold: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

______________________________________________________________________

## What it demonstrates

- The **`impl` key** — an inline TOML heredoc that becomes the `step()` body
- **Placeholder interpolation** — `{Component}`, `{arg_type}`, `{return_type}`
    expand at materialise time; bare C braces (`{ }`) pass through untouched
- **`jm apply <fragment>`** — copy a fragment into a project and materialise
    everything it implies
- **`jm split-objects`** — migrate a single-manifest project so each `[obj]`
    section moves to its own `objects/<name>.toml`
- The three project layouts (monolith → split → fragment+apply) and how they
    interoperate

______________________________________________________________________

## 1. Write the fragment

```toml
# agc.toml
[agc]
arg_type    = "float _Complex"
return_type = "float _Complex"
mutable     = "true"

impl = """
/* {Component} — EMA power tracker + gain pass-through */
const float mag2 = crealf(x) * crealf(x) + cimagf(x) * cimagf(x);
state->power = state->power + state->alpha * (mag2 - state->power);
return (float _Complex)(state->gain * x);
"""

[[agc.state]]
name    = "alpha"
type    = "float"
default = "0.05f"

[[agc.state]]
name    = "power"
type    = "float"
default = "0.0f"

[[agc.state]]
name    = "gain"
type    = "float"
default = "1.0f"
```

`{Component}` expands to `Agc`; bare C braces around struct member accesses
(`state->power`) pass through unchanged.

!!! warning "TOML ordering: scalar keys before sub-table arrays"

    All scalar keys (`impl`, `arg_type`, `mutable`, …) **must appear before**
    any `[[agc.state]]` entries. TOML requires this — once an array-of-tables
    header appears, subsequent bare keys are parsed into that entry, not the
    parent section.

______________________________________________________________________

## 2. Create a project and apply the fragment

```sh
just-makeit new agc_demo
cd agc_demo
just-makeit apply ../agc.toml
```

`jm apply`:

1. Copies `agc.toml` into `objects/agc.toml` — the project becomes
    self-contained and the external fragment is no longer needed
1. Adds `include = ["objects/*.toml"]` to `just-makeit.toml`
1. Materialises `native/inc/agc/agc_core.h`, `native/src/agc/agc_core.c`,
    `native/src/agc/agc_ext.c`, tests, type stub, CMake wiring

The `step()` body from `impl` is injected into `agc_core.h` at materialise
time — no `/* TODO */` stubs to fill in.

______________________________________________________________________

## 3. Build and test

```sh
cmake -B build && cmake --build build
ctest --test-dir build
```

Green on the first run — the AGC algorithm is already in the generated code.

______________________________________________________________________

## 4. Use from Python

```sh
pip install -e .
```

```python
import numpy as np
from agc_demo import Agc

agc = Agc(alpha=0.05, gain=1.0)

# Feed a burst of complex samples
signal = (np.random.randn(1000) + 1j * np.random.randn(1000)).astype(np.complex64)
out = agc.steps(signal)

# Power tracker adapts over the burst
print(f"tracked power: {agc.get_power():.4f}")
```

______________________________________________________________________

## 5. Migrate to split layout

If you have an existing project with all components inline in `just-makeit.toml`,
`jm split-objects` moves each `[obj]` section into its own fragment:

```sh
# Before: everything in just-makeit.toml
just-makeit split-objects
# After: just-makeit.toml has [project] + include glob
#        objects/agc.toml has [agc]
```

The merged config every just-makeit command sees is byte-identical before and
after. Future mutations (`jm add`, `jm method`, …) write back to the owning
fragment, not the manifest. Use `jm migrate-to-fragments` to also move module
sections.

______________________________________________________________________

## Key concepts

**`impl` is splice-free.** The step body is stored in TOML and injected during
`jm apply`. Unlike the old approach, `jm apply` never re-renders or splices your
hand-written `_core.c` — the inline body in TOML is the canonical version, not
a one-time paste.

**Unknown placeholders pass through.** `{Component}` and a handful of other
named tokens expand; anything else (like C struct initialiser syntax
`{ .field = value }`) is left verbatim. You cannot accidentally corrupt C code
with TOML interpolation.

**Fragment layout is the default.** `jm new` seeds new projects with
`include = ["objects/*.toml", "modules/*.toml"]` by default. `--no-fragments`
opts into the legacy single-manifest layout.

**Mutations route back to the owning fragment.** `_config.save()` tracks which
file each section came from, so a later `jm add` or `jm method` on `agc` writes
to `objects/agc.toml` — not to `just-makeit.toml`. The split survives the
commands that come after it.

## See also

- [Declarative scaffolding](../declarative-scaffolding.md) — full reference for
    the fragment format, placeholder table, and lifecycle body keys
- [Declarative scaffolding — three layouts](../declarative-scaffolding.md#three-layouts)
- [`jm apply` and `jm migrate-to-fragments` reference](../commands/build.md)
- [Declarative scaffolding design](../developers/declarative-scaffolding.md) —
    the design rationale behind the fragment format
