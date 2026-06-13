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

---

## Linking the real doppler C library

When doppler is available (a local install/build or the prebuilt release that
`nco_tone`'s harness auto-downloads), the example adds a standalone `tone`
object that wraps doppler's `nco_state_t *` as opaque state and links
`doppler::doppler-static`:

```toml
[tone]
arg_type        = "void"
return_type     = "float _Complex"
mutable         = "true"
extra_link_libs = ["doppler::doppler-static"]
# create_impl: obj->nco = nco_create(norm_freq, 0);
```

`[project] find_packages = ["Doppler"]` emits the `find_package(Doppler REQUIRED)`
block; the build is configured with `-DDoppler_DIR=...`. If doppler can't be
found, the `tone` object is skipped and the rest of the example still builds —
so the example is green everywhere, and exercises the real cross-library link
wherever doppler is present.

**Gotcha it demonstrates:** the local generator is named `lfo`, **not** `nco`,
on purpose. doppler ships its own `nco` whose header is `nco/nco_core.h`; a
local object of the same name would make `#include "nco/nco_core.h"` ambiguous
and silently resolve to the wrong one. Vendoring/linking an external library
means watching for name collisions with your own objects.

---

## Module function, reexports, and an app face

Three more features round out the project:

- **Module-level function** — `lerp(a, b, t)` is a free function in the `dsp`
  module (not an object): `from kitchen_sink.dsp import lerp`.

- **Reexported `no_generate` sibling** — `dsp_fn` is a *hand-written* CPython
  extension (jm only wires its `add_subdirectory`; the `.c`, CMakeLists, and
  `.pyi` are yours). It builds into the `dsp` package dir, and
  `[module.dsp] reexports = { dsp_fn = ["db10"] }` folds its `db10` into
  `dsp/__init__.py` — so `from kitchen_sink.dsp import db10` just works. This is
  the same pattern doppler uses for its functional `ddc_fn` API.

- **App face** — `jm app --target console --object gain --name dsp_cli`
  generates an `argparse` CLI over the `gain` bindings and wires it into
  `[project.scripts]`.

Everything is built and exercised by `smoke.py`, so the whole combination is
verified on every CI run.
