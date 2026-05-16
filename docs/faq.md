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
it manually to `native/inc/<obj>/<obj>_core.h` inside the struct body, after
the generated fields.

The generator regenerates the struct header when you run `just-makeit add`,
but your manually-added fields are preserved as long as they appear *after* the
generated block. To be safe, add a comment: `/* manual */`.

______________________________________________________________________

## Does `jm add` overwrite my hand-edited files?

No for implementation files, yes for binding and header files. See the
[Customization](customization.md#what-regenerates-vs-whats-yours) page for the
complete table. In short:

- `*_core.c` and test files: yours, never overwritten.
- `*_ext.c`, `.pyi`, `CMakeLists.txt`: regenerated.

All regenerated files are backed up before writing. If anything fails, the
originals are restored.

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
`git clone` required. All 10 bundled examples are shipped inside the wheel.
For a list: `just-makeit example --list`.

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
