# Kitchen sink — every feature, one project

This example builds a single jm project that exercises **every** major feature
at once: a vendored external C library, cross-component `depends_on`, GIL
release (`nogil`), component-level `extra_link_libs`, and every object flavor.

Integration bugs hide in the *combinations* — a feature that works alone breaks
when used with another. (Building this example is what surfaced jm gh-174: a
`depends_on` object whose C test failed to link its dependency.) Running it in
CI guards that surface on every push.

Run it end to end:

```sh
jm example kitchen_sink
```

---

## What it covers

The generated project has one module, `dsp`, with six objects plus a vendored
C dependency:

| Object   | Flavor                          | Feature exercised |
| -------- | ------------------------------- | ----------------- |
| `gain`   | scalar `step(x) -> y`           | writable property |
| `nco`    | generator `void -> complex64`   | `--mutable`, `--class-name NCO` |
| `meter`  | consumer `float -> void`        | `--field` property |
| `resamp` | block `complex64[] -> complex64`| `variable_output` + `pass_capacity` + **`nogil`** |
| `mixer`  | `complex64 -> complex64`        | `depends_on = ["nco"]` — opaque sibling `nco_state_t *`, header auto-included |
| `config` | sink, `no_step`                 | vendored **cJSON**: opaque `cJSON *`, component `extra_link_libs` + `extra_include_dirs` |

`cjson` is a `[project] c_deps` OBJECT library (vendored under
`native/src/cjson/`, no Python wrapper). `config` links it through
**component-level** `extra_link_libs` — the exact path jm gh-174 fixed.

`depends_on` does two things for `mixer`: it injects
`#include "nco/nco_core.h"` into `mixer_core.h` (so the opaque field compiles)
and links `nco_core` into `mixer`'s OBJECT lib **and** its test/bench
executables (gh-174 follow-up). `mixer`'s `step()` then calls `nco_step()` on
its own oscillator.

`resamp.execute` releases the GIL (`nogil`) around the pure-C kernel, so a
thread-per-shard worker scales across cores.
