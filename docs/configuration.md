# Configuration — just-makeit.toml

Every project scaffolded by `just-makeit new` contains a `just-makeit.toml`
file at the project root.  It is the single source of truth for the project's
structure: what objects exist, what state they carry, what types and flags were
used, and how the build system is configured.

`just-makeit` reads this file before every `object`, `add`, `method`,
`property`, and `script` command — you never need to pass the project name or
repeat earlier choices on the command line.

______________________________________________________________________

## What is stored

| Category | Stored in TOML |
|---|---|
| Project name and version | Yes |
| Build system (`--basic` / CMake) | Yes |
| Performance annotations (`--perf`) | Yes |
| Test runner (`--pytest`, `--pytest-benchmark`) | Yes |
| Objects and their state variables | Yes |
| `arg-type`, `return-type`, `--mutable`, `--no-state`, `--no-step` | Yes |
| Constructor parameters (`--init-param`) | Yes |
| Extra methods, properties, module-level functions | Yes |
| Module subpackage structure | Yes |
| `--impl` / `--replace` lifted code | **No** — patched directly into source |

`--impl` bodies are written into the generated C files once and then owned by
you; they are not round-tripped through TOML.

______________________________________________________________________

## Schema reference

A fully populated example covering every section:

```toml
[project]
name             = "my_project"
version          = "0.1.0"
build            = "make"    # only written when --basic; omitted for CMake (default)
perf             = "true"    # only written when --perf
pytest           = "true"    # only written when --pytest
pytest_benchmark = "true"    # only written when --pytest-benchmark; requires --pytest

# ── Standalone object ─────────────────────────────────────────────────────────

[engine]
arg_type    = "float _Complex"   # omitted when default
return_type = "float _Complex"   # omitted when same as arg_type
mutable     = "true"             # only written when --mutable
no_state    = "true"             # only written when --no-state
no_step     = "true"             # only written when --no-step

[[engine.state]]
name    = "gain"
type    = "double"
default = "1.0"

[[engine.state]]
name    = "center_freq"
type    = "double"
default = "1000.0"

[[engine.methods]]
name        = "normalize"
return_type = "void"
params      = [{name = "scale", type = "double"}]

[[engine.properties]]
name     = "peak"
ctype    = "double"
writable = true
field    = true

# ── Module subpackage ─────────────────────────────────────────────────────────

[module.filter]
objects = ["fir", "biquad"]

[[module.filter.functions]]
name        = "design_lowpass"
return_type = "void"
doc         = "Compute FIR coefficients for a lowpass filter."
params      = [{name = "cutoff", type = "double"}]

[fir]
arg_type    = "float _Complex"
return_type = "float _Complex"

[[fir.state]]
name    = "coeffs"
type    = "float[16]"
default = "0.0f"
```

### `[project]`

| Key | Type | Default | Set by |
|---|---|---|---|
| `name` | string | — | `just-makeit new <name>` |
| `version` | string | `"0.1.0"` | `just-makeit new` / `just-makeit config version X` |
| `build` | `"make"` | omitted (CMake) | `--basic` |
| `perf` | `"true"` | omitted | `--perf` |
| `pytest` | `"true"` | omitted | `--pytest` |
| `pytest_benchmark` | `"true"` | omitted | `--pytest-benchmark` |

### `[<object>]`

One section per standalone object or module-member object.

| Key | Type | Default | Set by |
|---|---|---|---|
| `arg_type` | string | `"float _Complex"` | `--arg-type` |
| `return_type` | string | same as `arg_type` | `--return-type` |
| `mutable` | `"true"` | omitted | `--mutable` |
| `no_state` | `"true"` | omitted | `--no-state` |
| `no_step` | `"true"` | omitted | `--no-step` |

### `[[<object>.state]]`

One entry per `--state` declaration.

| Key | Type | Notes |
|---|---|---|
| `name` | string | Valid C identifier |
| `type` | string | C type; append `[N]` for fixed arrays |
| `default` | string | C initialiser expression |

### `[[<object>.init_params]]`

Constructor-only parameters added with `--init-param` (no getter/setter, no
reset).  Same `name` / `type` / `default` keys as `state`.

### `[[<object>.methods]]`

One entry per `just-makeit method` call.

| Key | Type | Notes |
|---|---|---|
| `name` | string | Method name |
| `arg_type` | string | Array-style input type |
| `return_type` | string | C return type |
| `params` | array of `{name, type}` | Named scalar / array parameters |
| `variable_output` | bool | `--variable-output` |
| `batch` | bool | `--batch` |
| `multi_output` | array of strings | `--multi-output` types |
| `out_type` | string | `--out-type` |
| `out_divisor` | int | `--out-divisor` (default `1`, omitted) |

### `[[<object>.properties]]`

One entry per `just-makeit property` call.

| Key | Type | Notes |
|---|---|---|
| `name` | string | Property name |
| `ctype` | string | C type of the value |
| `writable` | bool | `--writable` |
| `field` | bool | `--field` (adds struct member, auto-implements getter) |

### `[module.<name>]`

| Key | Type | Notes |
|---|---|---|
| `objects` | array of strings | Objects in declaration order |
| `functions` | array | Module-level functions (see below) |

### `[[module.<name>.functions]]`

One entry per `just-makeit function` call.

| Key | Type | Notes |
|---|---|---|
| `name` | string | Function name |
| `return_type` | string | C return type |
| `doc` | string | Python docstring |
| `params` | array of `{name, type}` | Parameters |

______________________________________________________________________

## Inspecting config

```sh
just-makeit config
```

Prints a summary of the project and every object's state variables:

```
project:  my_project
version:  0.1.0

engine:
  gain:          double = 1.0
  center_freq:   double = 1000.0
```

To update the version:

```sh
just-makeit config version 0.2.0
```

______________________________________________________________________

## Reconstructing a project

`just-makeit script` reads `just-makeit.toml` and prints the exact sequence of
CLI commands that would recreate the project from scratch:

```sh
just-makeit script          # print to stdout
just-makeit script | sh     # pipe directly into a shell to rebuild
```

Example output for a two-object project:

```sh
#!/usr/bin/env sh
# Reconstructed from just-makeit.toml

just-makeit new my_project

cd my_project

just-makeit object engine \
    --state "gain:double:1.0" \
    --state "center_freq:double:1000.0"

just-makeit object detector \
    --arg-type "float _Complex" \
    --state "threshold:float:0.5f"
```

### When is this useful?

**Moving a project.** Copy `just-makeit.toml` to a new machine, run
`just-makeit script | sh`, and the full scaffold is regenerated.  Your own
code (business logic in `*_core.c`, tests, customisations) travels with the
project directory as normal; the script just recreates the generated
boilerplate if it was ever lost or corrupted.

**Starting fresh after a breaking change.** If a just-makeit update changes
generated file layouts, `just-makeit script | sh` in an empty directory
produces a clean scaffold at the current version.

**Documentation / reproducibility.** Commit `just-makeit.toml` to record
exactly how the project was built.  Anyone can reproduce the generated
structure without needing to remember the original command sequence.

!!! note
    `--impl` / `--replace` lifted code is not stored in TOML.  If you used
    these flags, the patched C bodies are in your source files — keep them in
    version control.

______________________________________________________________________

## Editing TOML by hand

The file is plain TOML — you can edit it directly.  `just-makeit` will read
your changes on the next command.  The rules:

- **Order matters for state variables**: `[[<object>.state]]` entries are
  emitted in the order they appear, which controls constructor argument order
  in both C and Python.
- **Removing a state variable** from TOML does not touch the generated source
  files.  Re-run `just-makeit script | sh` in a clean directory if you want a
  fully regenerated scaffold that matches TOML.
- **Don't rename the file** — `just-makeit` always looks for `just-makeit.toml`
  at the project root.
