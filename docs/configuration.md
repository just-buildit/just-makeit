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

| Category                                                          | Stored in TOML                        |
| ----------------------------------------------------------------- | ------------------------------------- |
| Project name and version                                          | Yes                                   |
| Build system (`--build-system`)                                   | Yes                                   |
| Performance annotations (`--perf`)                                | Yes                                   |
| Test runner (`--pytest`, `--pytest-benchmark`)                    | Yes                                   |
| Objects and their state variables                                 | Yes                                   |
| `arg-type`, `return-type`, `--mutable`, `--no-state`, `--no-step` | Yes                                   |
| Constructor parameters (`--init-param`)                           | Yes                                   |
| Extra methods, properties, module-level functions                 | Yes                                   |
| Module subpackage structure                                       | Yes                                   |
| `--impl` / `--replace` lifted code                                | **No** — patched directly into source |

`--impl` bodies are written into the generated C files once and then owned by
you; they are not round-tripped through TOML.

______________________________________________________________________

## Project layout and schema

After `just-makeit new my_project` followed by `just-makeit object engine`.
`just-makeit.toml` sits at the project root — every command reads it from
there, no flags required.

=== "File tree"

````
```
my_project/
├── just-makeit.toml
├── CMakeLists.txt
├── Makefile
├── pyproject.toml
├── cmake/
│   ├── my_project-config.cmake.in
│   └── my-project.pc.in
├── native/
│   ├── inc/
│   │   ├── my_project.h
│   │   ├── clib_common.h
│   │   ├── pyex_common.h
│   │   └── engine/
│   │       └── engine_core.h
│   ├── src/
│   │   ├── my_project_lib.c
│   │   └── engine/
│   │       ├── engine_core.c
│   │       ├── engine_ext.c
│   │       └── CMakeLists.txt
│   ├── tests/
│   │   └── test_engine_core.c
│   └── benchmarks/
│       └── bench_engine_core.c
└── src/
    └── my_project/
        ├── __init__.py
        ├── engine.pyi
        ├── tests/
        │   └── test_engine.py
        └── benchmarks/
            └── bench_engine.py
```
````

=== "just-makeit.toml"

````
```toml
[project]
name             = "my_project"
version          = "0.1.0"
build            = "cmake"
perf             = "false"
pytest           = "false"
pytest_benchmark = "false"

# One section per object, named after the object.
[engine]
arg_type    = "float _Complex"
return_type = "float _Complex"
mutable     = "false"
no_state    = "false"
no_step     = "false"

# One entry per --state declaration.
[[engine.state]]
name    = "gain"
type    = "double"
default = "1.0"

# One entry per --init-param.
[[engine.init_params]]
name    = "order"
type    = "int"
default = "4"

# One entry per --array-arg.
[[engine.array_args]]
name = "coeffs"
type = "float32"

# One entry per `just-makeit method`.
[[engine.methods]]
name        = "normalize"
return_type = "void"
params      = [{name = "scale", type = "double"}]

# One entry per `just-makeit property`.
[[engine.properties]]
name     = "peak"
type     = "double"
writable = true
field    = true

# Module subpackage, named after the module.
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
mutable     = "false"
no_state    = "false"
no_step     = "false"

[[fir.state]]
name    = "coeffs"
type    = "float[16]"
default = "0.0f"
```
````

______________________________________________________________________

## Schema reference

### `[project]`

| Key                | Type                  | Default   | Set by                                             |
| ------------------ | --------------------- | --------- | -------------------------------------------------- |
| `name`             | string                | —         | `just-makeit new <name>`                           |
| `version`          | string                | `"0.1.0"` | `just-makeit new` / `just-makeit config version X` |
| `build`            | `"cmake"` or `"make"` | `"cmake"` | `--build-system make`                              |
| `perf`             | `"true"` or `"false"` | `"false"` | `--perf`                                           |
| `pytest`           | `"true"` or `"false"` | `"false"` | `--pytest`                                         |
| `pytest_benchmark` | `"true"` or `"false"` | `"false"` | `--pytest-benchmark`                               |

### `[<object>]`

One section per standalone object or module-member object.  The section name
is whatever you passed to `just-makeit object <name>`.

| Key           | Type                  | Default            | Set by          |
| ------------- | --------------------- | ------------------ | --------------- |
| `arg_type`    | string                | `"float _Complex"` | `--arg-type`    |
| `return_type` | string                | same as `arg_type` | `--return-type` |
| `mutable`     | `"true"` or `"false"` | `"false"`          | `--mutable`     |
| `no_state`    | `"true"` or `"false"` | `"false"`          | `--no-state`    |
| `no_step`     | `"true"` or `"false"` | `"false"`          | `--no-step`     |

### `[[<object>.state]]`

One entry per `--state` declaration.

| Key       | Type   | Notes                                 |
| --------- | ------ | ------------------------------------- |
| `name`    | string | Valid C identifier                    |
| `type`    | string | C type; append `[N]` for fixed arrays |
| `default` | string | C initialiser expression              |

### `[[<object>.array_args]]`

Fixed-size array constructor arguments added with `--array-arg`.

| Key    | Type   | Notes                                                                                                                                                         |
| ------ | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name` | string | Argument name                                                                                                                                                 |
| `type` | string | Stored as NumPy dtype name (`float32`, `float64`, `complex64`, …); C types (`float`, `double`, `float _Complex`, …) are also accepted on input and normalised |

### `[[<object>.init_params]]`

Constructor-only parameters added with `--init-param` (no getter/setter, no
reset).  Same `name` / `type` / `default` keys as `state`.

### `[[<object>.methods]]`

One entry per `just-makeit method` call.

| Key               | Type                    | Notes                                                     |
| ----------------- | ----------------------- | --------------------------------------------------------- |
| `name`            | string                  | Method name                                               |
| `arg_type`        | string                  | Array-style input type                                    |
| `return_type`     | string                  | C return type                                             |
| `params`          | array of `{name, type}` | Named scalar / array parameters                           |
| `variable_output` | bool                    | `--variable-output`                                       |
| `batch`           | bool                    | `--batch`                                                 |
| `multi_output`    | array of strings        | `--multi-output` types                                    |
| `out_type`        | string                  | `--out-type`                                              |
| `out_divisor`     | int                     | `--out-divisor` (default `1`; omitted from TOML when `1`) |

### `[[<object>.properties]]`

One entry per `just-makeit property` call.

| Key        | Type   | Notes                                                  |
| ---------- | ------ | ------------------------------------------------------ |
| `name`     | string | Property name                                          |
| `type`     | string | C type of the value                                    |
| `writable` | bool   | `--writable`                                           |
| `field`    | bool   | `--field` (adds struct member, auto-implements getter) |

### `[module.<name>]`

| Key         | Type             | Notes                              |
| ----------- | ---------------- | ---------------------------------- |
| `objects`   | array of strings | Objects in declaration order       |
| `functions` | array            | Module-level functions (see below) |

### `[[module.<name>.functions]]`

One entry per `just-makeit function` call.

| Key           | Type                    | Notes            |
| ------------- | ----------------------- | ---------------- |
| `name`        | string                  | Function name    |
| `return_type` | string                  | C return type    |
| `doc`         | string                  | Python docstring |
| `params`      | array of `{name, type}` | Parameters       |

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
