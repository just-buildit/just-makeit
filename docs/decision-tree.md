# Decision tree — which `jm` command do I want?

Use this page when you don't know which command starts the work you're about
to do. It's a flat lookup, not a tutorial — follow a branch, run the command,
then jump to the relevant per-command page for details.

______________________________________________________________________

## Step 1. Where are you starting from?

```
no `just-makeit.toml` yet?  ─→  jm new <project>
                                  (optionally: --object NAME, --module MOD)
                                  see: docs/commands/scaffold.md

have a project?             ─→  go to Step 2
```

## Step 2. What are you adding?

```
A new stateful Python class
  ├── in its own .so?              ─→  jm object <name>
  └── shared with peers in one .so? ─→  jm module <mod>            (once)
                                       jm object <name> --module <mod>

A module-level C function
  (free function, no class)         ─→  jm function <fn> --module <mod>

An extra method on an existing object  ─→  jm method <obj> <method>
A Python @property on an object        ─→  jm property <obj> <prop>
Another state field on an object       ─→  jm add <obj> <var> --type T

Performance hot-path retrofit
  (JM_HOT, JM_FORCEINLINE,
   SIMD batch dispatch)              ─→  jm perf

A shippable app from a component
  ├── C executable?                  ─→  jm app --target c
  ├── Python console script?         ─→  jm app --target console
  └── PEP 723 inline script?         ─→  jm app --target pep723

Delete generated code                 ─→  jm remove <name>
Materialize TOML edits / fragments    ─→  jm apply [fragment.toml]
Build / run tests / run benchmarks    ─→  jm build  |  jm test  |  jm bench
Reconstruct CLI history from TOML     ─→  jm script
Upgrade an old project's schema       ─→  jm upgrade
```

______________________________________________________________________

## Sub-decision A. Object shape (for `jm object`)

```
What does step() look like?
  sample → sample (filter, NCO mix)      defaults: --arg-type "float _Complex"
  batch → batch  (FFT, block transform)  --arg-type "float[]"
  no input (generator, source)           --arg-type void
  no output (sink, accumulator)          --return-type void
  no step() at all — custom verbs only   --no-step  + jm method ...

What state does it carry?
  scalar defaults only                   [[state]] entries (default path)
  no internal state                      --no-state  + [[init_params]]
  user-facing ctor ≠ internal state      [[state]] + [[init_params]]
                                         + create_impl (state stays internal,
                                                        init_params drive ctor)
  some fields preserved on reset         state.roles = "config"  (TOML only)
```

## Sub-decision B. Method output shape (for `jm method`)

```
Fixed N out for N in   (resampler)        out_type="float", out_divisor=2
Variable count out     (detector)         variable_output=true
                                          (provide <comp>_<name>_max_out())
List of records out    (events)           result_fields=[{name, type}, ...]
Multiple parallel buffers                 multi_output=["float _Complex", ...]
Skip from benchmarks                      bench=false
```

## Sub-decision C. External dependencies

```
Vendored C subdir in your tree   [project] c_deps = ["liba", "libb"]
Findable by find_package         [project] find_packages = ["Doppler"]
pkg-config available             [project] pkg_modules = ["doppler"]

Then on the module or component that uses it:
  link against a library         extra_link_libs = ["${DOPPLER_LIBRARY}"]
  include its headers            extra_include_dirs = ["${DOPPLER_INCLUDE_DIR}"]
```

______________________________________________________________________

## "I want…" lookup

| I want…                           | do…                                                     |
| --------------------------------- | ------------------------------------------------------- |
| A new project                     | `jm new <name>`                                         |
| A class with state, own .so       | `jm object <name>`                                      |
| Multiple classes in one .so       | `jm module <mod>`, then `jm object … --module <mod>`    |
| A free C function in a module     | `jm function <fn> --module <mod>`                       |
| A second `.execute_*()` method    | `jm method <obj> <method>`                              |
| Read-only Python property         | `jm property <obj> <prop>`                              |
| Read-write Python property        | `jm property <obj> <prop> --writable`                   |
| Aliased property (existing field) | `jm property <obj> <prop> --field` (same name as state) |
| Add a state field later           | `jm add <obj> <var> --type T --default V`               |
| SIMD batch dispatch / `JM_HOT`    | scaffold with `--perf`, or `jm perf` later              |
| Standalone C executable           | `jm app --target c`                                     |
| Python CLI from your obj          | `jm app --target console`                               |
| PEP 723 single-file script        | `jm app --target pep723`                                |
| Drop everything generated for X   | `jm remove <name>`                                      |
| Materialize TOML changes          | `jm apply`                                              |
| Compose a fragment file           | `jm apply <fragment.toml>`                              |
| Run benchmarks                    | `jm bench`                                              |
| Reconstruct the CLI history       | `jm script`                                             |
| Upgrade an old project            | `jm upgrade`                                            |

______________________________________________________________________

## When the CLI can't reach it (TOML-only features)

These are decisions you can only express by editing `just-makeit.toml` (or
authoring a fragment and running `jm apply`):

- `create_impl` / `reset_impl` / `destroy_impl` — custom lifecycle bodies
- `impl` on methods and module functions — inline C bodies
- `out_type`, `out_divisor`, `variable_output`, `result_fields`, `multi_output`
- `init_params` with `optional`, `default_raw`, `real_type`, `string_enum:`
- `array_args`, `opaque` state fields, `no_ctor` per-field
- `extra_link_libs`, `extra_include_dirs`, `extra_types`, `extra_c` files
- `find_packages`, `pkg_modules`, `c_deps`
- `no_generate` modules (hand-written from scratch)

See `docs/configuration.md` for the full schema.
