<h1 id="__skip" align="center">
  <img src="https://raw.githubusercontent.com/just-buildit/just-makeit/main/docs/assets/logo-wordmark.png" alt="just-makeit" width="540">
</h1>

[![CI](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml)
[![Docs](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml)

Writing a DSP algorithm in C is the easy part.  Getting it into Python — with a
clean C API, a tested Python binding, a build system, and packaging — takes days
of boilerplate you've written before and will write again.

`just-makeit new` scaffolds the whole thing in one command: core C library, thin
Python binding, CMake build system, and full test coverage — all passing before
you write a single line of your algorithm.

______________________________________________________________________

## Quickstart

### Get it

Automatically install any missing requirements, create and activate venv

=== "curl"


    ```sh
    . <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) [-- path]
    ```

=== "pip"

    ```sh
    pip install just-makeit && just-makeit install-deps [-- path]
    ```

=== "uv"

    ```sh
    uv tool install just-makeit && just-makeit install-deps [-- path]
    ```

______________________________________________________________________

!!! note

     Project virtual environments are created in: 
     
     - `/tmp/jm-venv` (Linux/macOS) 
     - `%LOCALAPPDATA%\jm-venv` (Windows) 
     
     Customize by providing the optional `path` in the command above.


!!! info

    Installer detects your platform and installs system dependencies via
    the available package manager:

    | Platform     | Detection order                                            |
    |--------------|------------------------------------------------------------|
    | **Linux**    | apt · dnf · pacman · zypper · apk                          |
    | **macOS**    | Homebrew                                                   |
    | **Windows**  | MSYS2 · winget · choco · scoop · direct download fallback  |


______________________________________________________________________

### Get it with Docker

=== "docker-linux"

    ```sh
    docker run --rm -it ghcr.io/just-buildit/jm-examples-linux:latest
    ```

=== "docker-windows"

    ```sh
    docker run --rm -it ghcr.io/just-buildit/jm-examples-windows:latest
    ```

______________________________________________________________________

!!! Tip

    **No install needed** - the container prints a welcome message with everything you need:
        
    - pre-built example projects in `~/examples/`
    - commands to browse or re-run them
    - a quickstart for your own project

______________________________________________________________________


### Use it - Quick Examples

#### Simple standalone extension

Create a complete working project with a single command, build and test:

```sh
just-makeit new my_project --object engine --state gain:double:1.0
cd my_project && make && make test
```

!!! success "That's it! The project is installed and ready to customize."


**What you get:**

Each top-level object creates a stand-alone (shared object: `.so` on Linux) extension.

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
│   │       └── engine_core.h       # public C API + inline step()
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
├── compile_commands.json
└── just-makeit.toml
```

#### Module subpackage — multiple types share one `.so`:

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

**What you get** (Python package layer):

```
src/
└── my_filters/
    ├── __init__.py
    └── filter/
        ├── __init__.py        # from .filter import Fir, Biquad
        └── filter.pyi         # type stub for filter.so
```

One `.pyi` per `.so`, named to match the compiled extension.

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

// Generator / source object (--arg-type void): no input parameter
static inline float
nco_step(const nco_state_t *state);

void nco_steps(nco_state_t *state, float *output, size_t n);

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
y = obj.steps(x)            # allocates and returns complex64 ndarray
obj.steps(x, out=y)         # zero-copy: writes into y, returns y

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

## Design principles

**Your C code runs everywhere.** Core logic lives in `*_core.c` / `*_core.h`,
compiled once as a CMake OBJECT library and linked into both the Python
extension and a distributable `lib<project>.so`.  C, C++, and Rust consumers
link the same binary.  The Python binding in `*_ext.c` is a thin adapter —
argument parsing, array wrapping, and nothing more.

**Tests from day one.** Every generated project ships C tests (CTest) and
Python tests (pytest/unittest) that pass before you've written a line of your
algorithm.  Adding state variables, methods, and properties keeps the tests in
sync automatically.

**Standard packaging.** The generated `pyproject.toml` uses
[just-buildit](https://github.com/just-buildit/just-buildit) as the PEP 517
build backend.  `pip install .` builds and installs.  `just-makeit build`
produces a wheel.

______________________________________________________________________

## Requirements

- Python 3.11+
- CMake ≥ 3.16
- A C99 compiler (GCC, Clang, MSVC/MinGW)
- NumPy (runtime, for generated projects)

______________________________________________________________________

## Authors

Matthew T. Hunter, Ph.D. and [Claude Code](https://claude.ai/code)
