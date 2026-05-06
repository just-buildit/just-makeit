<p align="center">
  <img src="https://raw.githubusercontent.com/just-buildit/just-makeit/main/docs/assets/logo-wordmark.png" alt="just-makeit" width="540">
</p>

[![CI](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml)
[![Docs](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml)

Python C extensions the easy way.

`just-makeit new` scaffolds a complete, working C99 extension project in one
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
just-makeit new my_project --component engine --state gain:double:1.0
cd my_project && make && make test
```

**What you get:**

```
my_project/
├── native/
│   ├── inc/
│   │   ├── clib_common.h           # common C99 types
│   │   ├── pyex_common.h           # Python extension includes
│   │   └── engine/
│   │       └── engine_core.h       # component API
│   ├── src/
│   │   └── engine/
│   │       ├── CMakeLists.txt
│   │       ├── engine_core.c       # core logic — your algorithm goes here
│   │       └── engine_ext.c        # thin Python binding
│   └── tests/
│       └── test_engine_core.c      # CTest
├── src/my_project/
│   ├── __init__.py
│   ├── engine.pyi                  # type stub
│   └── tests/
│       └── test_engine.py          # pytest / unittest
├── CMakeLists.txt
├── Makefile
├── pyproject.toml
└── just-makeit.toml
```

---

## Commands

| Command | Description |
|---|---|
| `just-makeit new <project> [--component name] [--state ...]` | Create a new project (optionally with a first component) |
| `just-makeit init <component> [--state ...]` | Add a component to an existing project |
| `just-makeit add --state name:type[:default] [...]` | Add state variables to a component |
| `just-makeit config [key value]` | Show or edit project configuration |
| `just-makeit build [dir]` | Configure + build C, then package a wheel |
| `just-makeit test` | Build and run CTest + pytest |
| `just-makeit dry-run` | Preview what would be compiled |

State types: `double`, `float`, `int`. Default value is `0` for each type if omitted.
Both `reset` and the Python `__init__` use the declared default — `Engine()` with no args is valid.

---

## C conventions

Generated code follows a consistent lifecycle pattern:

```c
// Constructor — parameters match your --state declarations
engine_state_t *engine_create(double gain);

// Destructor
void engine_destroy(engine_state_t *state);

// Reset — restores every variable to its declared default
void engine_reset(engine_state_t *state);

// Single sample (inlined, pass-through stub — implement your algorithm here)
static inline float complex
engine_step(const engine_state_t *state, float complex x);

// Block processor
void engine_steps(
    engine_state_t *state,
    const float complex *input,
    float complex       *output,
    size_t               n);

// Getter / setter for each --state variable
double engine_get_gain(const engine_state_t *state);
void   engine_set_gain(engine_state_t *state, double gain);
```

---

## Python API

```python
from my_project import Engine
import numpy as np

obj = Engine(gain=1.0)   # explicit
obj = Engine()           # uses declared defaults

# single sample
y: complex = obj.step(1.0 + 0.5j)

# block processing
x = np.ones(1024, dtype=np.complex64)
y = obj.steps(x)   # returns complex64 ndarray

# getters / setters
obj.get_gain()
obj.set_gain(2.0)

# reset restores declared defaults
obj.reset()

# context manager
with Engine() as e:
    y = e.steps(x)
```

---

## Multiple state variables

```sh
just-makeit new my_project \
    --component engine \
    --state center_freq:double:1000.0 \
    --state bandwidth:double:200.0 \
    --state order:int:4
```

Each `--state name:type:default` becomes a struct field, a constructor parameter
(optional in Python, required in C), getter/setter pair, and reset target — in
both C and Python.

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

See [`examples/gain/`](https://github.com/just-buildit/just-makeit/tree/main/examples/gain) for a complete generated project.

---

## Requirements

- Python 3.11+
- CMake ≥ 3.16
- A C99 compiler (GCC, Clang, MSVC/MinGW)
- NumPy (runtime, for generated projects)
