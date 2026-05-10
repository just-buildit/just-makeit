<p align="center">
  <img src="https://raw.githubusercontent.com/just-buildit/just-makeit/main/docs/assets/logo-wordmark.png" alt="just-makeit" width="540">
</p>

[![CI](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml)
[![Docs](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml)

Python C extensions the easy way.

`just-makeit new` generates a complete, working C99 extension project in one
command: core C library, thin Python binding, CMake build system, and full test
coverage — all passing before you write a single line of code.

______________________________________________________________________

## Installation

```sh
pip install just-makeit
```

______________________________________________________________________

## Quickstart

**Standalone object** — each type gets its own `.so`:

```sh
just-makeit new my_project --object engine --state gain:double:1.0
cd my_project && make && make test
```

**What you get:**

```
my_project/
├── native/
│   ├── benchmarks/
│   │   └── bench_engine_core.c     # C-level benchmark
│   ├── inc/
│   │   ├── clib_common.h           # common C99 types
│   │   ├── pyex_common.h           # Python extension includes
│   │   ├── my_project.h            # umbrella header
│   │   └── engine/
│   │       └── engine_core.h       # object API  ← implement step() here
│   ├── src/
│   │   ├── my_project_lib.c        # combined C library stub (version symbol)
│   │   └── engine/
│   │       ├── CMakeLists.txt
│   │       ├── engine_core.c       # block processor + lifecycle
│   │       └── engine_ext.c        # thin Python binding
│   └── tests/
│       └── test_engine_core.c      # CTest
├── cmake/
│   └── my-project.pc.in            # pkg-config template
├── src/
│   └── my_project/                 # Python package — import my_project
│       ├── __init__.py
│       ├── engine.pyi              # type stub
│       ├── benchmarks/
│       │   ├── __init__.py
│       │   └── bench_engine.py     # Python benchmark
│       └── tests/
│           ├── __init__.py
│           └── test_engine.py      # pytest / unittest
├── CMakeLists.txt
├── Makefile
├── pyproject.toml
└── just-makeit.toml
```

**Module subpackage** — multiple types share one `.so`:

```sh
just-makeit new my_filters --module filter
cd my_filters
just-makeit object fir    --module filter \
    --state "coeffs:float[16]" --state "delay:float _Complex[16]" --state "gain:float:1.0"
just-makeit object biquad --module filter \
    --arg-type float --return-type float \
    --state "b0:double:1.0" --state "b1:double:0.0" --state "a1:double:0.0"
make && make test
```

```python
from my_filters.filter import Fir, Biquad   # one .so, one import
```

______________________________________________________________________

## Commands

`just-makeit` provides a CLI with several commands run with

```bash
just-makeit COMMAND
```

| Command | Description |
| ------- | ----------- |
| `new <project>` | Create a new project scaffold |
| `new <project> --module name [--module name ...]` | Project + one or more empty modules |
| `module <name>` | Scaffold an empty extension module (subpackage `.so`) |
| `object <name> --module name [--state ...] [--arg-type T] [--return-type T]` | Add a Python type to a module subpackage |
| `new <project> --object name [--state ...] [--arg-type T] [--return-type T]` | Project + first standalone object |
| `object <name> [--state ...] [--arg-type T] [--return-type T]` | Add a standalone object (its own `.so`) |
| `add --state name:type[:default] [--object name] [...]` | Add state variables to a standalone object |
| `perf` | Upgrade an existing project with performance annotations |
| `config [key value]` | Show or edit project configuration |
| `build [dir]` | Configure + build C, and package dist |
| `test` | Build and run CTest + pytest |
| `dry-run` | Preview what would be compiled |

See [State Variable Types](https://just-buildit.github.io/just-makeit/types/) for supported types, defaults, and C/Python mappings.

______________________________________________________________________

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

______________________________________________________________________

## Python API

**Standalone object** (`just-makeit object`):

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

**Module subpackage** (`just-makeit module` + `just-makeit object`):

```python
from my_filters.filter import Fir, Biquad   # one .so, clean subpackage import

fir = Fir(gain=1.0)
bq  = Biquad(b0=1.0)
```

Types within a module are fully independent — separate lifecycles, each with
its own `step`, `steps`, `reset`, getters/setters, and context manager.

______________________________________________________________________

## Multiple state variables

```sh
just-makeit new my_project \
    --object engine \
    --state center_freq:double:1000.0 \
    --state bandwidth:double:200.0 \
    --state order:int:4
```

Each `--state name:type:default` becomes a struct field, a constructor parameter
(optional in Python, required in C), getter/setter pair, and reset target — in
both C and Python.

______________________________________________________________________

## Integrations

- **CMake** — `Python3_add_library` with `WITH_SOABI`; `.so` lands in `src/` for zero-install dev workflow
- **GNU Make** — convenience wrapper with `build`, `test`, and `just-build` targets
- **NumPy buffer protocol** — `steps()` accepts and returns typed ndarrays matching your declared state types
- **pytest** — tests generated covering create, step, steps, getters/setters, reset, context manager, and destroy
- **CTest** — C-level test for the core lifecycle
- **just-buildit** — PEP 517 backend; `pip install .` and `pip install -e .` work out of the box

______________________________________________________________________

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

______________________________________________________________________

## Examples

The [`examples/`](https://github.com/just-buildit/just-makeit/tree/main/examples) directory contains step-by-step walkthroughs:

- [`running_stats/`](https://github.com/just-buildit/just-makeit/tree/main/examples/running_stats) — Welford's online mean & variance (introductory walkthrough)
- [`fir_filter/`](https://github.com/just-buildit/just-makeit/tree/main/examples/fir_filter) — 16-tap FIR filter processing complex I/Q signals, with perf annotations
- [`sliding_correlator/`](https://github.com/just-buildit/just-makeit/tree/main/examples/sliding_correlator) — sliding window cross-correlation against a fixed reference sequence
- [`sliding_power/`](https://github.com/just-buildit/just-makeit/tree/main/examples/sliding_power) — sliding window instantaneous signal power estimator
- [`dsp_toolkit/`](https://github.com/just-buildit/just-makeit/tree/main/examples/dsp_toolkit) — two-object library (Gain + Ema); demonstrates multi-object workflow and `__init__.py` auto-splice
- [`filter_module/`](https://github.com/just-buildit/just-makeit/tree/main/examples/filter_module) — `Fir` + `Biquad` in a single `filter` subpackage `.so` using `module` + `object`

______________________________________________________________________

## Design principles

**Separation of concerns.** Core C logic goes in `*_core.c` / `*_core.h`.
The Python extension in `*_ext.c` is a thin adapter — argument parsing, array
wrapping, and nothing more.  This keeps the C library independently testable
and usable from Rust, C++, or any other language.

**Full test coverage by default.** Every generated project has C tests (CTest)
and Python tests (pytest) from day one.

**just-buildit for packaging.** The generated `pyproject.toml` uses
[just-buildit](https://github.com/just-buildit/just-buildit) as the PEP 517
build backend, so `pip install .` just works.

______________________________________________________________________

## Requirements

- Python 3.11+
- CMake ≥ 3.16
- A C99 compiler (GCC, Clang, MSVC/MinGW)
- NumPy (runtime, for generated projects)

______________________________________________________________________

## Authors

Matthew T. Hunter, Ph.D. and [Claude Code](https://claude.ai/code)
