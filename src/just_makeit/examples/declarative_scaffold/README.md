# declarative_scaffold example

Schema 6's headline workflow: describe a whole C extension component in
**one TOML fragment** — including the C step body — and `jm apply`
materializes a buildable, tested project. No shell scaffold script, no
per-CLI-call sequence to memorize.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example declarative_scaffold
# declarative_scaffold: PASSED
```

---

## The story

### 1. The fragment

`agc.toml` is the *whole* component spec. The `step()` body is inline.
`{Component}` is interpolated to the title-cased object name when
`jm apply` runs; unknown `{placeholders}` and literal C braces pass
through untouched, so most algorithm bodies need no escaping.

```toml
[agc]
arg_type    = "float _Complex"
return_type = "float _Complex"
mutable     = "true"
no_state    = "false"
no_step     = "false"

impl = """
/* {Component} — EMA power tracker + gain pass-through. */
const float mag2 = crealf(x) * crealf(x) + cimagf(x) * cimagf(x);
state->power = state->power + state->alpha * (mag2 - state->power);
return (float _Complex)(state->gain * x);
"""

[[agc.state]]
name = "alpha"
type = "float"
default = "0.05f"

[[agc.state]]
name = "power"
type = "float"
default = "0.0f"

[[agc.state]]
name = "gain"
type = "float"
default = "1.0f"
```

### 2. Apply

```sh
just-makeit new demo
cd demo
just-makeit apply ../agc.toml
```

`jm apply <fragment>` does three things:

- **copies** the fragment into `objects/agc.toml` (the project stays
  self-contained — the external file is no longer needed),
- **adds** `include = ["objects/*.toml"]` to the manifest if absent,
- **materializes** every file each object/module/function in the merged
  manifest implies, with `impl` injected into the generated stub.

### 3. Build

```sh
cmake -B build -S . -DBUILD_PYTHON=ON
cmake --build build
ctest --test-dir build
```

All green. The generated `agc_core.h` contains the inlined body with
`Agc` substituted for `{Component}`.

---

## Bonus: migrate a legacy single-file project

A project that already has `[obj]` sections in the manifest converts to
the split layout in one command:

```sh
just-makeit split-objects
```

Every top-level object section moves out into `objects/<name>.toml`;
the manifest keeps `[project]` and `[module.X]` and gains the include
glob. The merged cfg every just-makeit consumer sees is byte-identical
before and after.

---

## What this exercises

- `include = [...]` resolution at load (schema 6).
- `jm apply <fragment>` compose path — copy in, register, materialize.
- Inline `impl` body with `{placeholder}` interpolation.
- `_config.save()` provenance routing — mutations route back to the
  fragment file that owns each section.
- `jm split-objects` migration.

The full design lives in `docs/developers/declarative-scaffolding.md`.
