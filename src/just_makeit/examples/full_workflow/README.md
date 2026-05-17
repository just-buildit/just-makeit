# full_workflow example

A complete development lifecycle walkthrough — scaffold, implement, test,
benchmark, measure coverage, and publish API docs — all from a single
just-makeit project.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example full_workflow
# full_workflow: PASSED
```

## What this example covers

| Stage        | Tool                       | Output                                  |
| ------------ | -------------------------- | --------------------------------------- |
| Build        | CMake + GCC                | `build/`                                |
| C tests      | CTest                      | pass/fail per test                      |
| Python tests | pytest                     | pass/fail per test                      |
| C benchmarks | `bench_*_core` executables | throughput in MSa/s                     |
| Python bench | `bench_*.py` scripts       | throughput in MSa/s                     |
| C coverage   | gcov + lcov → genhtml      | `docs/coverage/c/index.html`            |
| Python cov   | pytest-cov                 | `docs/coverage/python/index.html`       |
| C API docs   | Doxygen                    | `docs/doxygen/html/index.html`          |
| Python docs  | Zensical + mkdocstrings    | `site/index.html`                       |

---

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Install the tooling for coverage and docs:

```sh
sudo apt-get install lcov          # Debian/Ubuntu
brew install lcov                  # macOS
sudo pacman -S lcov                # Arch/CachyOS

uv add --dev pytest-cov mkdocstrings-python zensical
```

---

## 1. Scaffold

```sh
just-makeit new my_gain \
    --object gain \
    --arg-type float \
    --return-type float \
    --state gain:float:1.0
cd my_gain
```

Along with the usual C and Python files, every project now gets:

```
my_gain/
├── zensical.toml          # Zensical + mkdocstrings config
├── docs/
│   ├── index.md           # project home page stub
│   └── api.md             # auto-API via mkdocstrings :::
├── Doxyfile               # Doxygen config → docs/doxygen/
└── Makefile               # all targets below pre-wired
```

---

## 2. Implement

```c
/* native/inc/gain/gain_core.h */
static inline float
gain_step(const gain_state_t *state, float x)
{
    return state->gain * x;
}
```

---

## 3. Build and test

```sh
make        # cmake configure + build (Release)
make test   # CTest (C lifecycle) + pytest (Python API)
```

```
Test project .../my_gain/build
    Start 1: test_gain_core
1/1 Test #1: test_gain_core ........... Passed  0.00s

1/1 tests passed

src/my_gain/tests/test_gain.py ........ 8 passed
```

---

## 4. Benchmarks

```sh
make bench
```

Runs all `build/bench_*_core` executables (C) and all `src/*/benchmarks/bench_*.py`
scripts (Python) in one shot:

```
--- build/bench_gain_core ---
step   1k:       1.2 µs  (833.3 MSa/s)
steps  1k:       1.1 µs  (909.1 MSa/s)
step  64k:      52.4 µs  (1220.7 MSa/s)
steps 64k:      49.8 µs  (1285.1 MSa/s)

--- src/my_gain/benchmarks/bench_gain.py ---
step    1k:       2.3 µs  (434.8 MSa/s)
steps   1k:       1.8 µs  (555.6 MSa/s)
step   64k:     118.0 µs  (542.4 MSa/s)
steps  64k:     110.5 µs  (579.2 MSa/s)
```

The C path is always faster; the Python path includes CPython overhead but
shares the same compiled kernel.

---

## 5. Coverage

```sh
make coverage
```

Compiles a separate debug+`--coverage` build, runs the test suite, then
collects C coverage via **lcov/genhtml** and Python coverage via
**pytest-cov**:

```
C coverage: docs/coverage/c/index.html
Python coverage: docs/coverage/python/index.html

---------- coverage: platform linux ----------
Name                                Stmts   Miss  Cover
--------------------------------------------------------
src/my_gain/__init__.py                 2      0   100%
src/my_gain/gain.pyi                    9      9     0%
src/my_gain/tests/test_gain.py         18      0   100%
--------------------------------------------------------
TOTAL                                  29      9    69%
```

Open `docs/coverage/c/index.html` to see line-by-line C coverage,
or `docs/coverage/python/index.html` for the Python report.

### How coverage is wired

The Makefile `coverage` target:

1. Re-configures CMake with `-DCMAKE_C_FLAGS="--coverage -O0"` into
   `build/cov/` so coverage artifacts don't contaminate the Release build.
2. Builds and runs CTest against the coverage binary.
3. Runs `lcov --capture` to collect `.gcda` files, then `lcov --remove` to
   strip system headers and test files.
4. Calls `genhtml` to render `docs/coverage/c/index.html`.
5. Runs `pytest --cov=<package> --cov-report=html:docs/coverage/python`.

Both reports are written under `docs/` and excluded from version control via
`.gitignore`.

---

## 6. API documentation

```sh
make docs
```

Runs two doc generators in sequence:

### Doxygen (C API)

```
C API docs: docs/doxygen/html/index.html
```

Reads every `*.h` and `*.c` file under `native/inc/` and `native/src/`,
renders JavaDoc-style comments, and writes a full HTML site to
`docs/doxygen/html/`. The `Doxyfile` is pre-configured to:

- Extract all symbols (including `static inline`)
- Exclude `clib_common.h` and `pyex_common.h` (internal glue)
- Optimise output for C (no class hierarchy noise)

Add `/** @brief ... */` comments above your functions and structs and they
appear automatically in the rendered output.

### Zensical + mkdocstrings (Python API)

```
Python API docs: site/index.html
```

Zensical reads `zensical.toml`, then mkdocstrings introspects the compiled
extension and generates Python API pages from the docstrings embedded in the
C extension via `PyDoc_STR(...)`.

The generated `docs/api.md` contains a single directive that auto-documents
the entire package:

```markdown
# API Reference

::: my_gain
    options:
      show_source: true
      members: true
      inherited_members: false
```

### Customising the docs

Edit `zensical.toml` to change the theme, add pages, or configure
mkdocstrings options:

```toml
[project]
site_name    = "my_gain"
site_url     = "https://example.com/my_gain/"
repo_url     = "https://github.com/you/my_gain"
docs_dir     = "docs"
site_dir     = "site"

[project.plugins.mkdocstrings.handlers.python]
paths = ["src"]

[project.plugins.mkdocstrings.handlers.python.options]
show_source = true
```

Serve docs live with hot-reload while editing:

```sh
zensical serve
```

---

## All targets at a glance

```sh
make              # configure + build (Release)
make test         # CTest + pytest
make bench        # C + Python benchmarks
make coverage     # C (lcov) + Python (pytest-cov) HTML reports
make docs         # Doxygen (C API) + Zensical (Python API)
make clean        # remove build/, site/, docs/coverage/, docs/doxygen/
make help         # show this list
```
