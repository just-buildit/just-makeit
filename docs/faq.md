# FAQ

Short answers to questions that come up repeatedly.

______________________________________________________________________

## When should I use `--mutable`?

Use `--mutable` when `step()` must modify state directly — typically for
generator or oscillator objects where the state evolves on every call (an NCO
that advances its phase, an RNG that updates its seed).

Without `--mutable`, the generated `step()` takes `const comp_state_t *state`,
which lets the compiler optimise more aggressively. If your `step()` only
*reads* state, don't use `--mutable`.

For sources that produce output with no input, use `--arg-type void` instead.
See [Stateful vs Pure](pure.md) for the full shape matrix.

______________________________________________________________________

## When should I use a module vs standalone objects?

**Standalone** (`just-makeit object`, no `--module`): each type is its own
`.so` and its own top-level import. Good when types are unrelated or will be
used independently.

**Module** (`just-makeit module` + `just-makeit object --module`): multiple
types share one `.so` and one subpackage import. Good when types are
conceptually related (a filter bank, a set of codec stages) and users will
typically import several at once.

Module layout:

```python
from my_filters.filter import Fir, Biquad   # one .so
```

Standalone layout:

```python
from my_dsp import Gain        # gain.so
from my_dsp import Equalizer   # equalizer.so
```

The C code is identical either way.

______________________________________________________________________

## How do I link an external C library (FFTW, libsndfile, …)?

In the component's `CMakeLists.txt`, add:

```cmake
find_package(FFTW3f REQUIRED)
target_link_libraries(my_filter_core PRIVATE FFTW3::fftw3f)
```

For Python runtime dependencies, add to `pyproject.toml`:

```toml
[project]
dependencies = ["numpy", "scipy"]
```

The generated project's C code and Python binding don't need to change —
just wire the library into the CMake target.

______________________________________________________________________

## Can I add a method that takes extra scalar arguments beyond x?

Yes. Use `--extra-arg name:type` (or `--param name:type`) on `jm method`, or
declare `extra_args = [{name, type}]` in TOML:

```sh
just-makeit method integrator step_controlled \
    --arg-type "float _Complex" --return-type "float _Complex" \
    --extra-arg dump_now:bool
```

Generated C signature:

```c
float complex integrator_step_controlled(
    integrator_state_t *state, float complex x, bool dump_now);
```

Generated Python stub:

```python
def step_controlled(self, x: complex, dump_now: bool) -> complex: ...
```

In TOML, `extra_args` and `params` are synonyms — use whichever reads more
clearly:

```toml
[[integrator.methods]]
name        = "step_controlled"
arg_type    = "float _Complex"
return_type = "float _Complex"
extra_args  = [{name = "dump_now", type = "bool"}]
```

Supported scalar types for extra args are the same as for state fields (see
the [type mapping table](configuration.md#type-mapping)).

______________________________________________________________________

## Can I add a method that takes no arguments?

Yes. Use `--param` to declare method parameters; omitting `--param` gives
you a no-argument method:

```sh
just-makeit method my_obj flush   # step with no params
```

The C stub is `T my_obj_flush(my_obj_state_t *state)` and the Python binding
calls it as `obj.flush()`.

______________________________________________________________________

## How do I add a struct field without making it a state variable?

State variables (declared with `--state`) get a constructor parameter,
getter/setter pair, and reset target. If you just need a field in the struct
— a scratch buffer, a lookup table, something initialised in `_create` — add
it manually to `native/inc/<obj>/<obj>_core.h` inside the struct body.

The state struct (and the inline `step()` body) in `<obj>_core.h` is sacred —
`just-makeit apply` never re-renders it; it only injects missing method/property
*declarations* from the manifest. Your hand-added fields stay put across
`apply`. The exception is a **rebuild** (`just-makeit add` or
`just-makeit regenerate`): it re-stubs the struct from the manifest, so re-add
any manual fields after one — or declare them in TOML so they come back
automatically.

______________________________________________________________________

## Does `jm add` / `jm apply` overwrite my hand-edited files?

It depends on the verb — they follow a sacred/glue contract. See the
[Customization](customization.md#what-regenerates-vs-whats-yours) page for the
complete table. In short:

- **`jm apply`** never touches `_core.c`. It regenerates the glue (`*_ext.c`,
    `.pyi`, `CMakeLists.txt`) and injects any missing method/property
    *declaration* into `*_core.h`; the state struct and inline `step()` body
    stay sacred.
- **`jm method` / computed `jm property` / `jm function`** are additive: they
    inject one declaration and append a fresh stub, leaving your existing
    bodies intact.
- **`jm add`** is structural — it rebuilds the object from the manifest, which
    re-stubs the sacred `_core.c`. Keep your algorithm in the TOML
    `impl`/`create_impl` (the rebuild re-asserts it) or `git stash` first.

So editing the TOML propagates to the glue. A signature change or a new state
field is structural — use `jm regenerate` (or `jm add` for state) to rebuild
the sacred `_core.c` body from the manifest.

______________________________________________________________________

## How do I force everything to rebuild from the manifest?

`just-makeit regenerate <component>` deletes every file the component owns and
re-runs apply, rebuilding from `just-makeit.toml`. It is the deliberate-refresh
counterpart to the sacred-file contract: unlike `jm apply` it *does* discard
hand-written `_core.c` bodies, so `git stash` first. It leaves the manifest
untouched (unlike `jm remove`). One confirmation prompt; `--force` skips it.
Works for both standalone and module objects.

______________________________________________________________________

## Can I use just-makeit without Python (C-only)?

The *generator* requires Python to run. The *generated project* does not —
the C library (`libmy_project.so`) and its headers are standalone. You can
build and distribute the C library without Python installed on end-user machines.

See [C library](c-library.md) for the full install and consumption story.

______________________________________________________________________

## How do I run the bundled examples?

```sh
just-makeit example fir_filter
```

This runs the `fir_filter` example end-to-end in a temporary directory — no
`git clone` required. The bundled examples ship inside the wheel.
For a list, run `just-makeit example` with no name.

______________________________________________________________________

## Why does `make test` use `unittest` instead of pytest?

By default the generated tests use `python -m unittest discover` so that no
extra dependencies are required. If you want pytest:

```sh
just-makeit new my_project --object obj --pytest
```

Or add `--pytest-benchmark` to also generate `pytest-benchmark` bench files.

______________________________________________________________________

## How do I ship a Python wheel with the C extension?

```sh
just-makeit build
```

This calls `pip wheel` using the generated `pyproject.toml` and
[just-buildit](https://github.com/just-buildit/just-buildit) as the PEP 517
backend. For cross-platform wheel distribution, the generated project is
`cibuildwheel`-compatible — add a `cibuildwheel` section to `pyproject.toml`
and configure for your target platforms.
