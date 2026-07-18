# Which `jm` command do I want?

A quick lookup, not a tutorial: find the command, then open its
[command page](commands/scaffold.md) for the details.

______________________________________________________________________

## Starting a project

No `just-makeit.toml` yet? [`jm new <project>`](commands/scaffold.md#just-makeit-new) scaffolds a complete, building,
tested project. Add `--object <name>` or `--module <mod>` to create your first
component in the same step.

## Creating a component

| To create…                        | run…                                                                                                                                                 |
| --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| A stateful class in its own `.so` | [`jm object <name>`](commands/scaffold.md#just-makeit-object)                                                                                        |
| Several classes sharing one `.so` | [`jm module <mod>`](commands/scaffold.md#just-makeit-module-name), then [`jm object <name> --module <mod>`](commands/scaffold.md#just-makeit-object) |
| A stateless C function (no class) | [`jm function <fn> --module <mod>`](commands/extend.md#just-makeit-function)                                                                         |

New object? Its `step()` shape and state come next under
[Shaping an object](#shaping-an-object).

## Extending a component

| To add…                                 | run…                                                                                           |
| --------------------------------------- | ---------------------------------------------------------------------------------------------- |
| A named execute method                  | [`jm method <obj> <name>`](commands/extend.md#just-makeit-method)                              |
| A read-only Python property             | [`jm property <obj> <name>`](commands/extend.md#just-makeit-property)                          |
| A read-write property                   | [`jm property <obj> <name> --writable`](commands/extend.md#just-makeit-property)               |
| A state field                           | [`jm add --state <var>:T [--object <obj>]`](commands/scaffold.md#just-makeit-add)              |
| A warning after construction            | [`jm warning <obj> --condition <field> --message "…"`](commands/extend.md#just-makeit-warning) |
| A specific `create()`-failure exception | [`jm error <obj> --category ValueError --message "…"`](commands/extend.md#just-makeit-error)   |
| A SIMD / `JM_HOT` performance pass      | [`jm perf`](commands/build.md#just-makeit-perf)                                                |

## Shipping and operating

| To…                                            | run…                                                                                                                 |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Package a runnable app (C / console / PEP 723) | [`jm app --target c\|console\|pep723`](commands/app.md#just-makeit-app)                                              |
| Build, test, or benchmark                      | [`jm build`](commands/build.md#just-makeit-build-dir) · [`jm test`](commands/build.md#just-makeit-test) · `jm bench` |
| Push hand-edited TOML into files               | [`jm apply`](commands/build.md#just-makeit-apply) (see below)                                                        |
| Compose an external fragment                   | [`jm apply <fragment.toml>`](commands/build.md#just-makeit-apply)                                                    |
| Rebuild a component from the manifest          | [`jm regenerate <name>`](commands/build.md#just-makeit-regenerate-component) (see below)                             |
| Delete generated code **and** its wiring       | [`jm remove <kind> <name>`](commands/extend.md#removing-a-method-or-property)                                        |
| Reconstruct the CLI history from TOML          | [`jm script`](commands/build.md#just-makeit-script)                                                                  |
| Upgrade an old project's schema                | [`jm upgrade`](upgrading.md)                                                                                         |

______________________________________________________________________

## Shaping an object

[`jm object`](commands/scaffold.md#just-makeit-object) defaults to a 1:1 processor over `float _Complex`. For any other
shape, pass a `--preset` — or the flags it stands for:

| Your `step()`             | Preset                | …the flags it stands for                                              |
| ------------------------- | --------------------- | --------------------------------------------------------------------- |
| input → output (1:1)      | `processor` (default) | `--arg-type "float _Complex"`                                         |
| array → one sample        | —                     | `--arg-type "T[]"` (`steps()` not generated)                          |
| nothing in, samples out   | `generator`           | `--arg-type void` (complex return by default)                         |
| samples in, nothing out   | `consumer`            | `--return-type void`                                                  |
| no `step()`, custom verbs | `reader`              | `--no-step` (+ `--init-param filepath:"const char *"` to open a file) |
| array → array             | `blockwise`           | override `--arg-type` / `--return-type`                               |

And what state it carries:

| State                             | How                                             |
| --------------------------------- | ----------------------------------------------- |
| Scalar defaults only              | `[[state]]` entries (the default path)          |
| No internal state                 | `--no-state` + `[[init_params]]`                |
| Public ctor ≠ internal state      | `[[state]]` + `[[init_params]]` + `create_impl` |
| Some fields kept across `reset()` | `state.roles = "config"` (TOML only)            |

## Shaping a method's output

For [`jm method`](commands/extend.md#just-makeit-method), the output shape is a TOML setting on the method:

| Output                             | Setting                                                    |
| ---------------------------------- | ---------------------------------------------------------- |
| Fixed N out for N in (resampler)   | `out_type="float"`, `out_divisor=2`                        |
| Variable count out (event emitter) | `variable_output=true` (provide `<comp>_<name>_max_out()`) |
| A list of records (events)         | `result_fields=[{name, type}, …]`                          |
| Several parallel buffers           | `multi_output=["float _Complex", …]`                       |
| Excluded from benchmarks           | `bench=false`                                              |

## Wiring an external library

Declare how it's found on `[project]`:

| It's…                      | Declaration                   |
| -------------------------- | ----------------------------- |
| A vendored C subdir        | `c_deps = ["liba", "libb"]`   |
| Findable by `find_package` | `find_packages = ["Doppler"]` |
| A pkg-config module        | `pkg_modules = ["doppler"]`   |

Then link or include it on the component (or module) that uses it:
`extra_link_libs = ["${DOPPLER_LIBRARY}"]`,
`extra_include_dirs = ["${DOPPLER_INCLUDE_DIR}"]`.

______________________________________________________________________

## `apply` vs `regenerate`

You edited `just-makeit.toml` by hand. Which command carries the change into
the generated files?

|                            | [`jm apply`](commands/build.md#just-makeit-apply)                                                                                             | [`jm regenerate <name>`](commands/build.md#just-makeit-regenerate-component)           |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| **Use when**               | Glue changed — `_ext.c`, `.pyi`, `CMakeLists.txt`, or a new method/property that just needs to reach the public API                           | Structural change — a new state field or a changed signature                           |
| **What it does**           | Regenerates glue and injects missing declarations into `_core.h`. The state struct and inline `step()` are sacred; `_core.c` is never touched | Deletes every file the component owns and re-runs `apply`. Leaves the TOML alone       |
| **Your hand-written code** | Untouched                                                                                                                                     | Lifted out and spliced back by function name; `--discard` skips that for a clean reset |

`apply` is the safe, additive refresh. `regenerate` is the deliberate
rebuild — reach for it when a signature change or new state field has to
re-stub the sacred `_core.c`. [`jm add`](commands/scaffold.md#just-makeit-add) is `regenerate` specialised for state
(always a clean reset); `git stash` first regardless.

______________________________________________________________________

## When the CLI can't reach it

Every common TOML knob has a CLI flag —
[Configuration → Complete CLI ↔ TOML mapping](configuration.md#complete-cli-toml-mapping)
is the authoritative status of each key. A small tail stays TOML-only by
design:

- `opaque` state fields, `no_ctor` per field, `roles = "config"`
- `init_params` modifiers (`default_raw`, `real_type`, `real_create_fn`, `create_fn`), `init_post_parse_impl`, `string_enum:` init-param types
- `buf_field` / `len_field` / `valid_field` / `expr` property variants
- `max_results` / `max_results_param` on methods and functions
- `no_generate` modules, `extra_c` files, per-component `extra_link_libs`

See [Configuration](configuration.md) for the full schema.
