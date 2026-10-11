<h1 id="__skip" align="center">
  <img src="https://raw.githubusercontent.com/just-buildit/just-makeit/main/docs/assets/logo-wordmark.png" alt="just-makeit" width="540">
</h1>

[![CI](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml)
[![Docs](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml)
[![codecov](https://codecov.io/gh/just-buildit/just-makeit/graph/badge.svg?token=29J7ACUITR)](https://codecov.io/gh/just-buildit/just-makeit)

Getting an algorithm right is paramount. Yet it's rarely the bottleneck.
Turning it into shippable code — a tested C library, a Python binding, a build
system, packaging, and a public C API that Rust or C++ can also link — is the
tedious, exacting work that repeats on every project.

`just-makeit new` scaffolds the whole thing in one command: core C library, thin
Python binding, CMake build system, and full test coverage — all passing before
you write a single line of your algorithm.

______________________________________________________________________

## Try it now!

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/just-buildit/just-makeit)

Click the badge to launch a pre-built sandbox in your browser — no install, no
compiler, no Docker required. You'll land in a GitHub Codespaces environment
with `just-makeit` installed, dozens of bundled examples already scaffolded and
compiled, and a terminal ready to go. Run an example end-to-end, browse the
generated C and Python source, or start a fresh project with
`just-makeit new`.

______________________________________________________________________

## Quickstart

`install-deps` installs cmake, a C compiler, pkg-config and, on Linux,
patchelf through your system package manager (only when one of them is
missing), then numpy and just-makeit into a Python venv
(default `/tmp/jm-venv`, or pass your own path). Run
`just-makeit install-deps --help` for the full reference.

**curl (auto-installs dependencies, creates and activates venv):**

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) [-- path]
```

Append `--check` to report what would change without installing anything, or
`--force` to reinstall just-makeit even when it is current.

**pip:**

```sh
pip install just-makeit && just-makeit install-deps [path]
```

**uv:**

```sh
uv tool install just-makeit && just-makeit install-deps [path]
```

**Docker (no install needed):**

```sh
docker run --rm -it ghcr.io/just-buildit/jm-examples-linux:latest
```

______________________________________________________________________

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

______________________________________________________________________

## What you get

```
my_project/
├── native/
│   ├── inc/my_project/engine/engine_core.h   # public C API + inline step()
│   ├── src/engine/
│   │   ├── engine_core.c          # block processor + lifecycle
│   │   └── engine_ext.c           # thin Python binding
│   └── tests/test_engine_core.c   # CTest
├── src/my_project/
│   ├── engine.pyi                 # type stub
│   └── tests/test_engine.py       # pytest / unittest
├── cmake/my_project.pc.in         # pkg-config template
├── CMakeLists.txt
├── Makefile
├── just-makeit.toml
├── objects/engine.toml            # engine's manifest fragment
└── bootstrap.toml            # tool + system-dep manifest (jbx install-deps)
```

`bootstrap.toml` lists the system packages a build needs, per package manager.
[jbx](https://just-buildit.github.io/just-bashit/just-runit/#getting-just-runit)
installs them: `jbx install-deps -g dev` picks your OS's package manager.

______________________________________________________________________

## C API

```c
my_project_engine_state_t *my_project_engine_create(double gain);
void my_project_engine_destroy(my_project_engine_state_t *state);
void my_project_engine_reset(my_project_engine_state_t *state);

static inline float _Complex
my_project_engine_step(const my_project_engine_state_t *state,
                       float _Complex x);

void my_project_engine_steps(my_project_engine_state_t *state,
                             const float _Complex *input,
                             float _Complex *output, size_t n);

double my_project_engine_get_gain(const my_project_engine_state_t *state);
void my_project_engine_set_gain(my_project_engine_state_t *state, double val);
```

Every C symbol jm derives carries `[project] c_prefix`, which `jm new` sets to
the project name; `jm new --no-c-prefix` gives bare `engine_create`.

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

______________________________________________________________________

## Requirements

- Python 3.9+
- CMake ≥ 3.16
- A C99 compiler (GCC or Clang; on Windows, clang-cl — not MSVC's `cl.exe`)
- NumPy (runtime, for generated projects)

______________________________________________________________________

**[Full documentation →](https://just-buildit.github.io/just-makeit/)**

## Authors

Matthew T. Hunter, Ph.D. and [Claude Code](https://claude.ai/code)
