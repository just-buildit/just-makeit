# full_workflow example

A complete development lifecycle walkthrough — scaffold two components with
**both** test and benchmark styles, implement, test, benchmark, measure
coverage, and publish API docs — all from a single just-makeit project.

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
| Python tests | unittest **and** pytest    | both styles, side by side               |
| C benchmarks | `bench_*_core` executables | throughput in MSa/s                     |
| Python bench | timeit **and** pytest-bm   | both styles, side by side               |
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

This example creates a project with **two components** to show both test and
benchmark styles in the same project:

```sh
# Component 1: gain — unittest tests, timeit/perf_counter benchmarks (default)
just-makeit new my_dsp \
    --object gain \
    --arg-type float \
    --return-type float \
    --state gain:float:1.0
cd my_dsp

# Component 2: ema — pytest tests, pytest-benchmark benchmarks
just-makeit object ema \
    --arg-type float \
    --return-type float \
    --state alpha:float:0.1 \
    --state prev:float:0.0 \
    --pytest \
    --pytest-benchmark
```

Along with the usual C and Python files, every project now gets:

```
my_dsp/
├── zensical.toml          # Zensical + mkdocstrings config
├── docs/
│   ├── index.md           # project home page stub
│   └── api.md             # auto-API via mkdocstrings :::
├── Doxyfile               # Doxygen config → docs/doxygen/
└── Makefile               # all targets below pre-wired
```

The two components demonstrate the two styles you can mix:

| Component | Test style | Benchmark style |
| --------- | ---------- | --------------- |
| `gain`    | `unittest` | `timeit` / `perf_counter` standalone script |
| `ema`     | `pytest`   | `pytest-benchmark` fixture |

---

## 2. Implement

```c
/* native/src/gain/gain_core.c */
static inline float
gain_step(const gain_state_t *state, float x)
{
    return x * state->gain;
}

/* native/src/ema/ema_core.c */
static inline float
ema_step(ema_state_t *state, float x)
{
    float y = state->alpha * x + (1.0f - state->alpha) * state->prev;
    state->prev = y;
    return y;
}
```

---

## 3. Build and test

```sh
make        # cmake configure + build (Release)
make test   # CTest (C) + unittest (gain) + pytest (ema)
```

`make test` runs all three test layers:

```
Test project .../my_dsp/build
    Start 1: test_gain_core
    Start 2: test_ema_core
1/2 Test #1: test_gain_core ............. Passed  0.00s
2/2 Test #2: test_ema_core .............. Passed  0.00s

2/2 tests passed

# unittest (gain)
test_reset (my_dsp.tests.test_gain.TestGain) ... ok
test_step  (my_dsp.tests.test_gain.TestGain) ... ok

# pytest (ema)
src/my_dsp/tests/test_ema.py ........ 8 passed
```

---

## 4. Benchmarks — two styles

```sh
make bench
```

The `bench` Makefile target automatically dispatches to the right runner for
each component.

### timeit / perf_counter style (gain)

`bench_gain.py` is a **standalone script** — runnable with plain `python`:

```sh
python src/my_dsp/benchmarks/bench_gain.py
```

```
step    1k:       2.3 µs  (434.8 MSa/s)
steps   1k:       1.8 µs  (555.6 MSa/s)
step   64k:     118.0 µs  (542.4 MSa/s)
steps  64k:     110.5 µs  (579.2 MSa/s)
```

This style has **no dependencies** beyond numpy — ideal for quick checks in
any environment. The file ends with `if __name__ == "__main__":` so it's
directly executable.

### pytest-benchmark style (ema)

`bench_ema.py` uses the `benchmark` fixture and integrates with the full
pytest reporting infrastructure:

```sh
pytest src/my_dsp/benchmarks/bench_ema.py --benchmark-only -v
```

```
benchmark: 3 tests, min 5 rounds (of min 200.00us), 5.00s max time
Name                     Min       Max      Mean  StdDev   Median
---------------------------------------------------------------------
test_bench_step        1.2µs     1.8µs     1.3µs   0.1µs    1.3µs
test_bench_steps_1k    1.1µs     1.5µs     1.2µs   0.1µs    1.2µs
test_bench_steps_64k  49.8µs    52.0µs    50.5µs   0.6µs   50.3µs
```

This style integrates with CI — results are stored in `.benchmarks/` for
regression tracking with `--benchmark-compare`.

### Choosing a style

Use `--pytest-benchmark` when you want CI regression tracking and rich
reporting. Use the default (timeit/perf_counter) when you want zero extra
dependencies and simple printout benchmarks.

---

## 5. Coverage

```sh
make coverage
```

Compiles a separate debug+`--coverage` build, runs the test suite, then
collects C coverage via **lcov/genhtml** and Python coverage via
**pytest-cov**:

```
C coverage:      docs/coverage/c/index.html
Python coverage: docs/coverage/python/index.html

---------- coverage: platform linux ----------
Name                                  Stmts   Miss  Cover
----------------------------------------------------------
src/my_dsp/__init__.py                    2      0   100%
src/my_dsp/tests/test_gain.py            18      0   100%
src/my_dsp/tests/test_ema.py             22      0   100%
----------------------------------------------------------
TOTAL                                    42      0   100%
```

### How coverage is wired

The Makefile `coverage` target:

1. Re-configures CMake with `-DCMAKE_C_FLAGS="--coverage -O0"` into
   `build/cov/` so coverage artifacts don't contaminate the Release build.
2. Builds and runs CTest against the coverage binary.
3. Runs `lcov --capture` to collect `.gcda` files, then `lcov --remove` to
   strip system headers and test files.
4. Calls `genhtml` to render `docs/coverage/c/index.html`.
5. Runs `pytest --cov=my_dsp --cov-report=html:docs/coverage/python`.

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
appear automatically in the rendered output. This example already does exactly
that: the `@brief` on each `<obj>_create()` in `native/inc/<obj>/<obj>_core.h`
is a real one-sentence class summary (`gain`, `ema`), which `jm apply` also
flows through into the generated Python docstrings — so the same comment feeds
both the Doxygen C site and the Zensical Python pages.

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

::: my_dsp
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
site_name    = "my_dsp"
site_url     = "https://example.com/my_dsp/"
repo_url     = "https://github.com/you/my_dsp"
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
make test         # CTest + unittest (gain) + pytest (ema)
make bench        # C benchmarks + timeit (gain) + pytest-bm (ema)
make coverage     # C (lcov) + Python (pytest-cov) HTML reports
make docs         # Doxygen (C API) + Zensical (Python API)
make clean        # remove build/, site/, docs/coverage/, docs/doxygen/
make help         # show this list
```
