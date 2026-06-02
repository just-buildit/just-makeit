# Porting existing C extensions to just-makeit

A complete guide for bringing hand-rolled, fragmented Python C extensions
into the consistent just-makeit scaffold. Covers assessment, three porting
paths, the sacred/glue model, type mapping, and verification.

______________________________________________________________________

## Assess your extension

Before choosing a path, answer four questions about your existing code.

### What is the API shape?

Map your step/process function signature to a just-makeit shape:

| Your signature looks like                      | Shape     | Preset flag          |
| ---------------------------------------------- | --------- | -------------------- |
| `T process(State*, T in)`                      | processor | (default)            |
| `T generate(State*)`                           | generator | `--preset generator` |
| `void consume(State*, T in)`                   | consumer  | `--preset consumer`  |
| `void process(State*, const T *in, n, U *out)` | blockwise | `--preset blockwise` |
| state init from filepath, no step at all       | reader    | `--preset reader`    |
| no state struct at all                         | any       | `--no-state`         |
| no step function at all                        | any       | `--no-step`          |

### What state does it carry?

List every field in your state struct. For each field determine:

- Is it a supported scalar type? (see [Type mapping](#type-mapping-reference))
- Is it a fixed-length array? (`float buf[256]` → `float _Complex[256]`)
- Is it an opaque pointer to internal memory? (use `opaque = true` in TOML)
- Is it a constructor-only parameter that doesn't live in the struct?

### Does it have extra methods or properties?

Beyond the core `step`/`steps`, does your extension expose:

- Named processing variants (e.g. `process_ctrl`, `apply_gain`)? → `jm method`
- Getter/setter pairs for state fields? → `jm property`
- Module-level utility functions? → `jm function`

### Do you have a clean header, or only a .c file?

- Clean `_core.h` with the full API declared → **Path A** (`jm bind`)
- Only `.c` source, no clean header → **Path B** (`--impl` lift)
- Algorithm only in Python / pseudocode → **Path C** (fresh scaffold)

______________________________________________________________________

## Three porting paths

```
Do you have a C header that declares the full API?
    YES → Path A: jm bind           (synthesises binding from header; fastest)
    NO  → Do you have working C source?
              YES → Path B: --impl lift   (scaffold + lift body from file)
              NO  → Path C: fresh scaffold, transplant algorithm by hand
```

All three end at the same place: `make && make test` green and
`jm status` showing no drift.

______________________________________________________________________

## Path A — you have the header (`jm bind`)

`jm bind` reads a `_core.h` and synthesises `_ext.c` + `.pyi`. It handles
every shape that just-makeit can represent.

### What the parser handles

| Header construct                                                            | Becomes in Python         |
| --------------------------------------------------------------------------- | ------------------------- |
| `T comp_step(comp_t *s, T in)`                                              | `.step(x)` method         |
| `void comp_step(comp_t *s, T in)` (consumer)                                | `.step(x)` → None         |
| `T comp_step(comp_t *s)` (generator)                                        | `.step()` → T             |
| `void comp_steps(s, const T *in, n, U *out)` (blockwise)                    | `.steps(in)` → ndarray    |
| `T comp_get_field(const comp_t *s)` / `void comp_set_field(comp_t *s, T v)` | property                  |
| `T comp_verb(comp_t *s, T in)` (custom verb)                                | `.verb(x)` method         |
| `size_t comp_verb(s, T *out, n)` + `comp_verb_max_out(s)`                   | variable-output `.verb()` |
| `typedef struct comp_t comp_t;` (opaque forward decl)                       | opaque state              |
| constructor params not in the struct body                                   | `__init__` keyword args   |

Declarations the parser cannot handle are skipped with a warning — add
those by hand via `[[comp.methods]]` in TOML after binding.

### Running jm bind

```sh
# Scaffold the project first — bind will wire the binding into it
just-makeit new myproject --object engine

# jm bind reads the existing header; it does NOT touch _core.h / _core.c
just-makeit bind engine          # writes engine_ext.c + engine.pyi

# Verify the binding is in sync with the header (use as a CI gate)
just-makeit bind engine --check  # exit 0 = in sync; exit 1 = drift
```

### When bind is not enough

For each skipped declaration, add a `[[engine.methods]]` block to
`just-makeit.toml` and run `jm apply`:

```toml
[[engine.methods]]
name = "apply_ctrl"
arg_type = "double"
return_type = "double"
```

`jm apply` injects the declaration into `_core.h` and appends a stub to
`_core.c`. Fill in the stub body.

______________________________________________________________________

## Path B — you have the C source (`--impl` lift)

Use this when you have working C but no clean, public header.

### Scaffold the object

Match the `jm object` flags to your API shape and list the state fields:

```sh
just-makeit new myproject
just-makeit object gain \
    --arg-type float \
    --return-type float \
    --state gain:float:1.0 \
    --state clip:float:1.0
```

This generates a passing scaffold before you write a single line.

### Lift the step body with `--impl`

```sh
# Lift by function name
just-makeit object gain \
    --arg-type float \
    --return-type float \
    --state gain:float:1.0 \
    --impl legacy/dsp.c::apply_gain

# Lift by line range (when the source isn't a named function)
just-makeit object gain \
    --impl legacy/dsp.c::42:67

# Rename identifiers while lifting (repeatable)
just-makeit object gain \
    --impl legacy/dsp.c::apply_gain \
    --replace apply_gain_state::gain_t \
    --replace apply_gain::gain_step
```

`--impl` extracts the body between the outermost braces of the target
function and injects it into the `/* <<IMPLEMENT>> */` placeholder in
`_core.c`. `--replace old::new` applies word-boundary substitutions before
injection.

### Lift lifecycle bodies

```sh
just-makeit object gain \
    --impl create::legacy/dsp.c::gain_init \
    --impl reset::legacy/dsp.c::gain_reset \
    --impl destroy::legacy/dsp.c::gain_teardown
```

### Record in TOML for reproducibility

Instead of passing `--impl` flags on every run, record the lift in
`just-makeit.toml` so `jm apply` can re-derive the scaffold from scratch:

```toml
[gain]
arg_type        = "float"
return_type     = "float"
impl_file       = "legacy/dsp.c::apply_gain"
create_impl_file = "legacy/dsp.c::gain_init"
replace         = { "apply_gain_state" = "gain_t" }

[[gain.state]]
name    = "gain"
type    = "float"
default = "1.0"
```

### Inline impl placeholder

Write the body inline in TOML using these interpolated placeholders:

| Placeholder     | Expands to                      |
| --------------- | ------------------------------- |
| `{component}`   | lowercase component name        |
| `{Component}`   | title-cased component name      |
| `{arg_type}`    | C type of `step()` argument     |
| `{return_type}` | C type of `step()` return value |
| `{method}`      | method name (in `[[methods]]`)  |

```toml
[gain]
impl = """
    return s->gain * in;
"""
```

______________________________________________________________________

## Path C — fresh scaffold, transplant algorithm

When you only have a Python prototype or pseudocode:

```sh
just-makeit new myproject
just-makeit object engine \
    --arg-type float \
    --return-type float \
    --state coeff:float:0.5

# Build and test before writing a single line of algorithm
make -C /path/to/myproject && make -C /path/to/myproject test
```

Open `native/src/engine/engine_core.c`, fill in the `step()` body, and
run the tests again. The scaffold already passes CTest — you're implementing
into green.

______________________________________________________________________

## The sacred/glue model

### Sacred files (yours — never overwritten)

| File                        | Protected content                        |
| --------------------------- | ---------------------------------------- |
| `native/inc/<c>/<c>_core.h` | state struct body + inline `step()` body |
| `native/src/<c>/<c>_core.c` | all function bodies (steps, lifecycle)   |
| `native/src/<mod>/<fn>.c`   | module-level function bodies             |

`_core.h` is hybrid: `jm apply` may inject new method/property declarations,
but the struct body and `step()` inline body are never touched.

### Glue files (always regenerated — never hand-edit)

| File                           | Regenerated by         |
| ------------------------------ | ---------------------- |
| `native/src/<c>/<c>_ext.c`     | `jm apply` / `jm bind` |
| `src/<pkg>/<c>.pyi`            | every mutating command |
| `native/tests/test_<c>_core.c` | `jm apply`             |
| `src/<pkg>/tests/test_<c>.py`  | `jm apply`             |
| `CMakeLists.txt`               | `jm apply`             |

### The three maintenance commands

| Command              | Effect                                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------------------ |
| `jm apply`           | Additive — injects missing declarations, regenerates glue, never deletes                               |
| `jm regenerate COMP` | Delete all COMP files and rebuild from manifest (discards `_core.c` bodies — record `impl_file` first) |
| `jm status`          | Read-only drift table: OK / MISSING / MODIFIED per file                                                |

Use `jm apply` for day-to-day additions. Use `jm regenerate` only when you
intentionally want to discard a body — for example, after a signature change
where you have a new `impl_file` that provides the replacement.

______________________________________________________________________

## Type mapping reference

### Supported scalar types

| C type                 | NumPy dtype  | Notes                       |
| ---------------------- | ------------ | --------------------------- |
| `float`                | `float32`    |                             |
| `double`               | `float64`    |                             |
| `float _Complex`       | `complex64`  |                             |
| `double _Complex`      | `complex128` |                             |
| `long double _Complex` | —            | No NumPy equiv; Python only |
| `int`                  | `int32`      |                             |
| `int8_t` … `int64_t`   | `int8` …     |                             |
| `uint8_t` … `uint64_t` | `uint8` …    |                             |
| `size_t`               | `uint64`     |                             |
| `ptrdiff_t`            | `int64`      |                             |
| `bool`                 | `bool`       |                             |
| `const char *`         | str          | Return type only            |

### Array types

Append `[]` to any scalar key for variable-length arrays (blockwise preset,
`--multi-output`): `float _Complex[]`, `float[]`, `double[]`, …

Fixed-length state arrays use `TYPE[N]` syntax:

```toml
[[engine.state]]
name    = "buf"
type    = "float _Complex[256]"
default = "0.0"
```

### Unsupported constructs and workarounds

| C construct         | Workaround                                     |
| ------------------- | ---------------------------------------------- |
| Struct return value | Return pointer; expose fields via properties   |
| `void *` pointer    | Mark state field `opaque = true` in TOML       |
| Multi-scalar in/out | Use blockwise (`T[]` / `U[]`) or extra methods |
| Nested structs      | Opaque pointer + accessor methods              |
| Variadic args       | Not supported — wrap in a fixed-signature fn   |

______________________________________________________________________

## Shape mapping reference

| API shape                       | Flags                                              | Preset shorthand     |
| ------------------------------- | -------------------------------------------------- | -------------------- |
| `T step(s, T in)`               | `--arg-type T --return-type T`                     | (default)            |
| `T step(s, T in)` mutable state | `--arg-type T --return-type T --mutable`           |                      |
| `T step(s)` generator           | `--arg-type void --return-type T`                  | `--preset generator` |
| `void step(s, T in)` consumer   | `--arg-type T --return-type void`                  | `--preset consumer`  |
| `void steps(s, T*in, n, U*out)` | `--arg-type T[] --return-type U[]`                 | `--preset blockwise` |
| No step at all                  | `--no-step`                                        | `--preset reader`    |
| No state struct                 | `--no-state`                                       |                      |
| Opaque state pointer            | `opaque = true` on the state field in TOML         |                      |
| Variable-length output          | `--variable-output --method-name verb --max-out N` |                      |

### Adding extra methods, properties, and functions

```sh
just-makeit method engine execute_ctrl \
    --arg-type double --return-type double \
    --impl legacy/dsp.c::engine_ctrl

just-makeit property engine gain --type float --writable

just-makeit function mymod normalize --arg-type float --return-type float
```

If a method needs extra scalar control parameters beyond the standard `x`
input, use `--extra-arg name:type` (or the synonymous `--param`):

```sh
just-makeit method engine execute_ctrl \
    --arg-type double --return-type double \
    --extra-arg bypass:bool \
    --extra-arg gain:float
```

Or declare them in TOML and run `jm apply`:

```toml
[[engine.methods]]
name        = "execute_ctrl"
arg_type    = "double"
return_type = "double"
extra_args  = [{name = "bypass", type = "bool"}, {name = "gain", type = "float"}]
impl_file   = "legacy/dsp.c::engine_ctrl"

[[engine.properties]]
name     = "gain"
type     = "float"
writable = true
```

______________________________________________________________________

## Multi-component and module layout

### Standalone objects (default)

Each object gets its own `.so`: `from myproject import Engine`.

```sh
just-makeit object fir    --arg-type float --return-type float
just-makeit object biquad --arg-type float --return-type float
# → myproject/fir.so + myproject/biquad.so
```

### Module objects (shared `.so`)

Objects in the same module share one `.so` subpackage:

```sh
just-makeit object fir    --module filter --arg-type float --return-type float
just-makeit object biquad --module filter --arg-type float --return-type float
# → myproject/filter.so  (Fir and Biquad both imported from myproject.filter)
```

### Component dependencies

When one component's C code calls another's:

```sh
just-makeit object decimator \
    --arg-type "float _Complex" \
    --return-type float \
    --depends-on hbfilter \
    --depends-on nco
```

This wires `hbfilter` and `nco` OBJECT libraries into `decimator`'s CMake
link list. The C code can then `#include "hbfilter/hbfilter_core.h"`.

______________________________________________________________________

## Wiring external dependencies

```toml
[project]
# pkg-config libraries
pkg_modules   = ["libfftw3f", "libsamplerate"]

# CMake find_package() packages
find_packages = ["ZLIB", "OpenSSL"]

# Pure-C subproject deps (add_subdirectory)
c_deps        = ["resamp", "fft"]
```

For raw header+lib pairs, pass `--extra-include-dirs` on the CLI (recorded
automatically in TOML):

```sh
just-makeit object engine \
    --extra-include-dirs /usr/local/include/mylib
```

______________________________________________________________________

## Post-port verification

```sh
# 1. Drift report
just-makeit status

# 2. Binding in sync with header (if jm bind was used)
just-makeit bind engine --check

# 3. Refresh any stale glue
just-makeit apply

# 4. Build and test
make && make test

# 5. Smoke-test the Python side
python -c "from myproject import Engine; e = Engine(1.0); print(e.step(0.5))"
```

`jm status` column meanings:

| Status   | Meaning                                                                 |
| -------- | ----------------------------------------------------------------------- |
| OK       | File exists and matches what `apply` would generate                     |
| MISSING  | File declared in manifest but not on disk — run `jm apply`              |
| MODIFIED | File differs from generated — expected for sacred files; drift for glue |

MODIFIED on a glue file (`_ext.c`, `.pyi`, `CMakeLists.txt`) means a manual
edit that will be overwritten on next `jm apply`. Move the change into the
manifest instead.

______________________________________________________________________

## Troubleshooting

### `jm bind` skipped declarations

The regex parser couldn't handle those signatures. For each:

- Simplify the C signature where possible, or
- Add a `[[comp.methods]]` TOML block with the correct types and an
    `impl_file` pointing at the existing implementation.

### `jm apply` regenerated `_ext.c` and lost my hand edits

`_ext.c` is glue — never hand-edit it. Move any customisation into a
`[[comp.methods]]` or `[[comp.properties]]` TOML entry, or into the
sacred `_core.c`.

### `jm regenerate` discarded my `step()` body

Record the implementation in TOML before regenerating:

```toml
[engine]
impl_file = "native/src/engine/engine_core.c::engine_step"
```

`jm regenerate` will then re-inject it automatically.

### CMake can't find my external library

Check whether the library ships a `.pc` file (`pkg_modules`), a CMake
config (`find_packages`), or only raw headers+lib (`--extra-include-dirs`).

### Windows: cmake can't find make

MinGW is required (MSVC rejects `float _Complex`). Ensure `mingw32-make.exe`
is on PATH and aliased as `make.exe`. The generated `Makefile` passes
`-G "MinGW Makefiles"` automatically on `Windows_NT`.

### step() signature mismatch after porting

Use `--replace` to rename identifiers during the lift:

```sh
just-makeit object engine \
    --arg-type "float _Complex" \
    --impl legacy.c::my_process \
    --replace my_complex_t::"float _Complex" \
    --replace my_state_t::engine_t
```

______________________________________________________________________

## Quick reference

```sh
# Assess
just-makeit status                               # drift table

# Bootstrap
just-makeit new PROJ                             # empty project
just-makeit object COMP [flags]                  # add standalone object
just-makeit object COMP --module MOD             # add module object

# Bind from header (Path A)
just-makeit bind COMP                            # synthesise _ext.c from _core.h
just-makeit bind COMP --check                    # CI gate: exit 1 if drift

# Lift from existing C (Path B)
just-makeit object COMP --impl file::func        # lift step() body by name
just-makeit object COMP --impl file::N:M         # lift by line range
just-makeit object COMP --impl create::file::fn  # lift create() body
just-makeit object COMP --replace old::new       # rename identifiers on lift

# Extend
just-makeit method COMP VERB                     # add named method
just-makeit property COMP FIELD                  # add getter/setter property
just-makeit function MOD FUNC                    # add module-level function
just-makeit add --object COMP --state N:T:D      # add state field (structural)

# Maintain
just-makeit apply                                # sync glue from manifest (additive)
just-makeit regenerate COMP                      # rebuild component from scratch
just-makeit script                               # emit CLI history from TOML

# Build and test
make && make test
```
