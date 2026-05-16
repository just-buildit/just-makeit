<h1 id="__skip" align="center">
  <img src="https://raw.githubusercontent.com/just-buildit/just-makeit/main/docs/assets/logo-wordmark.png" alt="just-makeit" width="540">
</h1>

[![CI](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml)
[![Docs](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml)

Getting an algorithm right is paramount.  Yet it's rarely the bottleneck.
Turning it into shippable code — a tested C library, a Python binding, a build
system, packaging, and a public C API that Rust or C++ can also link — is the
tedious, exacting work that repeats on every project.

`just-makeit new` scaffolds the whole thing in one command: core C library, thin
Python binding, CMake build system, and full test coverage — all passing before
you write a single line of your algorithm.

---

## Quickstart

**curl (auto-installs dependencies, creates and activates venv):**

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) [-- path]
```

**pip:**

```sh
pip install just-makeit && just-makeit install-deps [-- path]
```

**uv:**

```sh
uv tool install just-makeit && just-makeit install-deps [-- path]
```

**Docker (no install needed):**

```sh
docker run --rm -it ghcr.io/just-buildit/jm-examples-linux:latest
```

---

## Quick examples

**Simple standalone extension:**

```sh
just-makeit new my_project --object engine --state gain:double:1.0
cd my_project && make && make test
```

**Module subpackage — multiple types in one `.so`:**

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

---

## What you get

```
my_project/
├── native/
│   ├── inc/engine/engine_core.h   # public C API + inline step()
│   ├── src/engine/
│   │   ├── engine_core.c          # block processor + lifecycle
│   │   └── engine_ext.c           # thin Python binding
│   └── tests/test_engine_core.c   # CTest
├── src/my_project/
│   ├── engine.pyi                 # type stub
│   └── tests/test_engine.py       # pytest / unittest
├── cmake/my-project.pc.in         # pkg-config template
├── CMakeLists.txt
├── Makefile
└── just-makeit.toml
```

---

## C API

```c
engine_state_t *engine_create(double gain);
void            engine_destroy(engine_state_t *state);
void            engine_reset(engine_state_t *state);

static inline float complex
engine_step(const engine_state_t *state, float complex x);

void engine_steps(engine_state_t *state,
                  const float complex *in, float complex *out, size_t n);

double engine_get_gain(const engine_state_t *state);
void   engine_set_gain(engine_state_t *state, double gain);
```

## Python API

```python
from my_project import Engine
import numpy as np

obj = Engine(gain=2.0)

y = obj.step(1.0 + 0.5j)

x = np.ones(1024, dtype=np.complex64)
y = obj.steps(x)          # allocates output ndarray
obj.steps(x, out=y)       # zero-copy

obj.set_gain(0.5)
obj.reset()

with Engine() as e:
    y = e.steps(x)
```

---

## Requirements

- Python 3.11+
- CMake ≥ 3.16
- A C99 compiler (GCC, Clang, MSVC/MinGW)
- NumPy (runtime, for generated projects)

---

**[Full documentation →](https://just-buildit.github.io/just-makeit/)**

## Authors

Matthew T. Hunter, Ph.D. and [Claude Code](https://claude.ai/code)
