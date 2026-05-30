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

Delete generated code + TOML wiring   ─→  jm remove <name>
Materialize TOML edits / fragments    ─→  jm apply [fragment.toml]
Refresh a component from the manifest  ─→  jm regenerate <name>
  (keeps TOML, deletes + rebuilds files)
Build / run tests / run benchmarks    ─→  jm build  |  jm test  |  jm bench
Reconstruct CLI history from TOML     ─→  jm script
Upgrade an old project's schema       ─→  jm upgrade
```

> `jm apply` and `jm regenerate` are the two halves of the sacred/glue
> contract — see [Sub-decision D](#sub-decision-d-apply-vs-regenerate-the-sacredglue-contract).

______________________________________________________________________

## Sub-decision A. Object shape (for `jm object`)

```
What does step() look like?
  input → output (1:1)  (processor)      defaults: --arg-type "float _Complex"
  array → 1 sample      (array input)    --arg-type "T[]"  (steps() not gen'd)
  no input              (generator)      --arg-type void  (defaults to complex
                                                            return)
  no output             (consumer)       --return-type void
  no step() at all — custom verbs only   --no-step  + jm method ...
                                         (reader pattern: --no-step +
                                          --init-param filepath:"const char *")

  array → array  ("blockwise")  is NOT yet supported. --return-type "T[]"
  errors cleanly. Array INPUT works; array RETURN does not.

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
Variable count out     (event emitter)    variable_output=true
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

## Sub-decision D. `apply` vs `regenerate` (the sacred/glue contract)

You edited `just-makeit.toml` by hand. Which command propagates the change?

```
Glue changed — _ext.c, .pyi, CMakeLists.txt, or a
  TOML-declared method/field that only needs to reach
  the public API                              ─→  jm apply
    (glue files always regenerate; _core.h declarations
     refresh, but its inline step() body and state struct
     are PRESERVED; _core.c is SACRED — never overwritten)

You want the whole component rebuilt from the manifest,
  discarding hand-written _core.c bodies        ─→  jm regenerate <name>
    (deletes every file the component owns, then re-runs
     apply; leaves TOML untouched. git stash first.)
```

Rule of thumb: `apply` is the safe, additive refresh. `regenerate` is the
deliberate, destructive one — use it when a signature change in TOML must also
reach the sacred `_core.c` body.

## Sub-decision E. Preset (for `jm object --preset NAME`)

```
input → output (1:1)        processor   (default)
no input, produces samples  generator   (void arg → complex return)
consumes input, no output   consumer
no step(), custom verbs     reader

blockwise is intentionally excluded — array return is unsupported.
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
| Drop generated files **and** TOML | `jm remove <name>`                                      |
| Materialize TOML changes (glue)   | `jm apply`                                              |
| Compose a fragment file           | `jm apply <fragment.toml>`                              |
| Refresh a component, keep TOML    | `jm regenerate <name>`                                  |
| Run benchmarks                    | `jm bench`                                              |
| Reconstruct the CLI history       | `jm script`                                             |
| Upgrade an old project            | `jm upgrade`                                            |

______________________________________________________________________

## When the CLI can't reach it (TOML-only features)

This list shrank dramatically when Phase 2 of the implementation plan
shipped: every common TOML knob now has a CLI flag. See the field-by-field
inventory at
[Configuration → Complete CLI ↔ TOML mapping](configuration.md#complete-cli-toml-mapping)
for the authoritative status of every key.

Remaining TOML-only by design (≤5% of use cases):

- `opaque` state fields, `no_ctor` per-field, `roles = "config"`
- `init_params` modifiers: `default_raw`, `real_type`, `real_create_fn`, `create_fn`
- `init_post_parse_impl`, `string_enum:` init-param types
- `buf_field` / `len_field` / `valid_field` / `expr` property variants
- `max_results` / `max_results_param` on methods/functions
- `no_generate` modules (hand-written from scratch)
- `extra_c` files
- Per-component `extra_link_libs` (per-module is reachable via CLI)

See `docs/configuration.md` for the full schema.
