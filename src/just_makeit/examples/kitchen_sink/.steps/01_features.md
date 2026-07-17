## What it covers

The generated project has a `dsp` module with six objects, a hand-written
`dsp_fn` sibling module, and a vendored C dependency:

| Object   | Flavor                          | Feature exercised |
| -------- | ------------------------------- | ----------------- |
| `gain`   | scalar `step(x) -> y`           | writable property, plus a `--batch` method (`process_batch`) |
| `lfo`    | generator `void -> complex64`   | `--mutable`, `--class-name Lfo` |
| `meter`  | consumer `float -> void`        | `--field` property |
| `resamp` | block `complex64[] -> complex64`| `variable_output` + `pass_capacity` + **`nogil`** |
| `mixer`  | `complex64 -> complex64`        | `depends_on = ["lfo"]` — opaque sibling `lfo_state_t *`, header auto-included |
| `config` | sink, `no_step`                 | vendored **cJSON**: opaque `cJSON *`, component `extra_link_libs` + `extra_include_dirs` |

`cjson` is a `[project] c_deps` OBJECT library (vendored under
`native/src/cjson/`, no Python wrapper). `config` links it through
**component-level** `extra_link_libs` — the exact path jm gh-174 fixed.

`depends_on` does two things for `mixer`: it injects
`#include "lfo/lfo_core.h"` into `mixer_core.h` (so the opaque field compiles)
and links `lfo_core` into `mixer`'s OBJECT lib **and** its test/bench
executables (gh-174 follow-up). `mixer`'s `step()` then calls `lfo_step()` on
its own oscillator.

`resamp.execute` releases the GIL (`nogil`) around the pure-C kernel, so a
thread-per-shard worker scales across cores.
