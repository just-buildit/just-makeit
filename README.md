# just-makeit

Python C extensions the easy way.

`just-makeit init` scaffolds a complete, working C99 extension project in one
command: core C library, thin Python binding, CMake build system, and full test
coverage — no boilerplate to write.

---

## Installation

```sh
pip install just-makeit
```

---

## Quickstart

```sh
just-makeit init my_filter --state gain:double
cd my_filter
make
make test
```

**What you get:**

```
my_filter/
    native/
        inc/
            clib_common.h               # common C99 types
            pyex_common.h               # common Python extension includes
            my_filter/
                my_filter_core.h        # component API — edit this first
        src/
            my_filter/
                my_filter_core.c        # core C logic — your business logic
                my_filter_ext.c         # thin Python binding — no business logic
        tests/
            test_my_filter_core.c       # CTest
    src/
        my_filter/
            __init__.py
            my_filter.pyi               # type stub
            tests/
                test_my_filter.py       # pytest
    CMakeLists.txt
    Makefile
    pyproject.toml
```

---

## Commands

| Command | Description |
|---|---|
| `just-makeit init <name> [dir] [--state name:type ...]` | Create a new C extension project |
| `just-makeit build [dir]` | Configure + build C, then package a wheel |
| `just-makeit test` | Build and run CTest + pytest |
| `just-makeit dry-run` | Preview what would be compiled |

State types: `double` (default), `float`, `int`.

---

## C conventions

Generated code follows a consistent lifecycle pattern:

```c
// Constructor — one parameter per --state var
my_filter_state_t *my_filter_create(double gain);

// Destructor
void my_filter_destroy(my_filter_state_t *state);

// Reset (zeros all state vars)
void my_filter_reset(my_filter_state_t *state);

// Single sample (inlined, pass-through stub — implement your DSP here)
static inline float complex
my_filter_step(const my_filter_state_t *state, float complex x);

// Block processor
void my_filter_steps(
    my_filter_state_t *state,
    const float complex *input,
    float complex       *output,
    size_t               n);

// Getter / setter for each --state var
double my_filter_get_gain(const my_filter_state_t *state);
void   my_filter_set_gain(my_filter_state_t *state, double gain);
```

---

## Python API

```python
from my_filter import MyFilter
import numpy as np

obj = MyFilter(gain=1.0)

# single sample
y: complex = obj.step(1.0 + 0.5j)

# block processing
x = np.ones(1024, dtype=np.complex64)
y = obj.steps(x)   # returns complex64 ndarray

# getters / setters
obj.get_gain()
obj.set_gain(2.0)

# context manager
with MyFilter(1.0) as f:
    y = f.steps(x)
```

---

## Multiple state variables

```sh
just-makeit init my_bpf \
    --state center_freq:double \
    --state bandwidth:double \
    --state order:int
```

Each `--state name:type` becomes a struct field, a constructor parameter, and a
getter/setter pair — in both C and Python.

---

## Integrations

- **CMake** — `Python3_add_library` with `WITH_SOABI`; `.so` lands in `src/` for zero-install dev workflow
- **GNU Make** — convenience wrapper with `build`, `test`, and `just-build` targets
- **NumPy buffer protocol** — `steps()` accepts and returns `complex64` ndarrays
- **pytest** — tests generated covering create, step, steps, getters/setters, reset, context manager, and destroy
- **CTest** — C-level test for the core lifecycle
- **just-buildit** — PEP 517 backend; `pip install .` and `pip install -e .` work out of the box

---

## Packaging

The generated project uses [just-buildit](https://github.com/just-buildit/just-buildit)
as its PEP 517 build backend.

```sh
# Build and install
pip install .

# Development install (no rebuild needed after editing Python files)
pip install -e .

# Build a wheel manually
just-makeit build
```

---

## Examples

See [`examples/gain/`](examples/gain/) for a complete generated project.

---

## Requirements

- Python 3.12+
- CMake ≥ 3.16
- A C99 compiler (GCC, Clang, MSVC/MinGW)
- NumPy (runtime, for generated projects)
