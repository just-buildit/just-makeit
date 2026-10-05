# full_workflow example

A complete development lifecycle walkthrough — scaffold two components with
**both** test and benchmark styles, implement, test, run the C header's own
examples as doctests, benchmark, measure coverage, and publish API docs — all
from a single just-makeit project.

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
| Doctests     | `@code` in the C header    | executed against the built `.so`        |
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

Install the tooling for the pytest styles, coverage and docs:

```sh
sudo apt-get install lcov doxygen          # Debian/Ubuntu
brew install lcov doxygen                  # macOS
sudo pacman -S lcov doxygen                # Arch/CachyOS

# into the venv `make` uses (the active one):
pip install pytest pytest-benchmark pytest-cov mkdocstrings-python zensical
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

# The test and benchmark styles are project settings, and the next step
# switches both. gain's scaffolded test and benchmark are jm's while their
# first line is a `# jm:generated` token, so `jm apply` would rewrite them in
# the new style. Delete that line from both to make them yours, and they stay
# unittest / timeit.
sed -i.bak '1d' src/my_dsp/tests/test_gain.py src/my_dsp/benchmarks/bench_gain.py
rm src/my_dsp/tests/test_gain.py.bak src/my_dsp/benchmarks/bench_gain.py.bak

# Component 2: ema — pytest tests, pytest-benchmark benchmarks. The style is
# a [project] setting with no `object` flag: switch it in the manifest, then
# scaffold. `--mutable` because ema's step() writes state->prev.
sed -i.bak 's/^pytest = "false"/pytest = "true"/; s/^pytest_benchmark = "false"/pytest_benchmark = "true"/' just-makeit.toml
rm just-makeit.toml.bak
just-makeit object ema \
    --arg-type float \
    --return-type float \
    --state alpha:float:0.1f \
    --state prev:float:0.0 \
    --mutable

# A named method on gain. `step()`/`steps()` carry jm's own docstrings, so a
# named method is the only place your own `@code` example can become a doctest.
just-makeit method gain scale \
    --arg-type float \
    --return-type float
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

Each `step()` is a `static inline` in its sacred header; replace the
scaffold's two-line body. The named method's stub is in `gain_core.c`:

```c
/* native/inc/my_dsp/gain/gain_core.h */
static inline float
my_dsp_gain_step(const my_dsp_gain_state_t *state, float x)
{
    return x * state->gain;
}

/* native/src/gain/gain_core.c — the named method */
float
my_dsp_gain_scale(my_dsp_gain_state_t *state, float x)
{
    return x * state->gain;
}

/* native/inc/my_dsp/ema/ema_core.h */
static inline float
my_dsp_ema_step(my_dsp_ema_state_t *state, float x)
{
    float y = state->alpha * x + (1.0f - state->alpha) * state->prev;
    state->prev = y;
    return y;
}
```

---

## 3. Build and test

```sh
make          # cmake configure + build (Release)
make test     # CTest (C) + unittest (gain)
pytest src/   # ema's pytest tests (pytest also runs gain's unittest TestCase)
```

`make test` runs CTest and unittest; `pytest src/` runs ema's tests:

```
Test project .../my_dsp/build
    Start 1: test_ema_core
1/2 Test #1: test_ema_core ....................   Passed    0.00 sec
    Start 2: test_gain_core
2/2 Test #2: test_gain_core ...................   Passed    0.00 sec

100% tests passed, 0 tests failed out of 2

# unittest (gain)
test_context_manager (my_dsp.tests.test_gain.TestGain.test_context_manager) ... ok
test_create (my_dsp.tests.test_gain.TestGain.test_create) ... ok
...
test_step_runs (my_dsp.tests.test_gain.TestGain.test_step_runs) ... ok
...
Ran 8 tests in 0.012s

OK

# pytest src/ (ema, plus gain's TestCase)
src/my_dsp/tests/test_ema.py ........                                    [ 50%]
src/my_dsp/tests/test_gain.py ........                                   [100%]

============================== 16 passed in 0.09s ==============================
```

---

## 4. Executable documentation — `@code` becomes a doctest

The sacred `native/inc/<pkg>/<obj>/<obj>_core.h` (here
`native/inc/my_dsp/gain/gain_core.h`) is the single source of truth for
documentation. jm reads its Doxygen and renders a numpy-style Python docstring
into the generated `.pyi` — and a `@code` block becomes a **runnable
`Examples` doctest**, executed against the compiled extension.

Write the block on the named method scaffolded in step 1:

```c
/**
 * @brief Scale one sample by the gain and return it.
 * @param x  Input sample.
 * @return The scaled sample.
 * @code
 * >>> from my_dsp import Gain
 * >>> Gain(2.0).scale(1.5)
 * 3.0
 * @endcode
 */
float my_dsp_gain_scale(my_dsp_gain_state_t *state, float x);
```

Then regenerate the glue — `apply` re-derives the stubs from the edited
comments and leaves your `.c` implementations alone:

```sh
just-makeit apply
```

`src/my_dsp/gain.pyi` now carries the whole docstring, assembled from the tags:

```python
    def scale(self, x: float) -> float:
        """Scale one sample by the gain and return it.

        Parameters
        ----------
        x : float
            Input sample.

        Returns
        -------
        float
            The scaled sample.

        Examples
        --------
        >>> from my_dsp import Gain
        >>> Gain(2.0).scale(1.5)
        3.0

        """
```

| Doxygen tag            | Where it lands in the docstring |
| ---------------------- | ------------------------------- |
| `@brief`               | Summary line                    |
| `@param <name> <doc>`  | `Parameters` entry              |
| `@return` / `@returns` | `Returns` entry                 |
| `@code` … `@endcode`   | Runnable `Examples` doctest     |

### Run them

The doctests import the **built** `.so` and execute every `>>>`:

```sh
PYTHONPATH=src python -m pytest -q --doctest-glob='*.pyi' src/my_dsp/gain.pyi
```

```
.                                                                        [100%]
1 passed in 0.12s
```

This is the part that makes `@code` a test rather than a rendered snippet. If
`my_dsp_gain_scale()` were changed to return `x * state->gain + 1.0f` while the header
kept advertising `3.0`, the run fails and names both sides:

```
Expected:
    3.0
Got:
    4.0
```

So a kernel cannot quietly drift away from the example that documents it, as
long as something runs the doctests (next section).

### Wiring it into your own test run

`make test` runs CTest plus unittest discovery; it does **not** sweep the
stubs, and `just-makeit ci` does not add it either. This example's own test
runs them; to make them a gate in your project, add this to your test step
(it runs the `test_*.py` suites too):

```sh
PYTHONPATH=src python -m pytest --doctest-glob='*.pyi' src/
```

### Two rules worth knowing

- **`@code` belongs on a named method**, not on `create()` and not on
    `step()`/`steps()`. A class docstring's `Examples` block is always jm's
    synthesized construction demo, and the built-in step methods keep their
    standard docstrings — so a `@code` block on either is silently not rendered.
- **Write examples with deterministic, printable output.** The doctest compares
    a repr exactly; whole-number results (`2.0 * 1.5 == 3.0`) avoid
    floating-point noise that fails for reasons that teach nothing.

---

## 5. Benchmarks — two styles

```sh
make bench
```

`make bench` runs `just-makeit bench`: the C `bench_*_core` binaries, then
pytest-benchmark over `src/`, and saves a dated snapshot to
`benchmarks/history/`. The timeit script (gain) is standalone, and each
Python style also runs directly, as shown below.

### timeit / perf_counter style (gain)

`bench_gain.py` is a **standalone script** — runnable with plain `python`:

```sh
PYTHONPATH=src python src/my_dsp/benchmarks/bench_gain.py
```

```
gain
  step                        50.5 ns/call
  steps 1k                   0.378 µs  (2707.5 MSa/s)
  steps 64k                  0.010 ms  (6805.4 MSa/s)
```

This style has **no dependencies** beyond numpy — ideal for quick checks in
any environment. The file ends with `if __name__ == "__main__":` so it's
directly executable.

### pytest-benchmark style (ema)

`bench_ema.py` uses the `benchmark` fixture and integrates with the full
pytest reporting infrastructure:

```sh
pytest src/my_dsp/benchmarks/bench_ema.py --benchmark-only -v \
    --benchmark-columns=min,max,mean,stddev,median
```

```
src/my_dsp/benchmarks/bench_ema.py::test_bench_step PASSED               [ 33%]
src/my_dsp/benchmarks/bench_ema.py::test_bench_steps_1k PASSED           [ 66%]
src/my_dsp/benchmarks/bench_ema.py::test_bench_steps_64k PASSED          [100%]


-------------------------------------------------------------- benchmark: 3 tests --------------------------------------------------------------
Name (time in ns)                 Min                       Max                    Mean                 StdDev                  Median
------------------------------------------------------------------------------------------------------------------------------------------------
test_bench_step               27.7500 (1.0)         40,425.3300 (1.0)           38.9840 (1.0)         205.4482 (1.0)           28.9500 (1.0)
test_bench_steps_1k        3,777.0005 (136.11)      51,887.0002 (1.28)       3,849.9872 (98.76)       366.4622 (1.78)       3,827.0009 (132.19)
test_bench_steps_64k     229,288.0017 (>1000.0)  4,240,941.9984 (104.91)   236,731.8573 (>1000.0)  98,554.6178 (479.71)   229,759.0036 (>1000.0)
------------------------------------------------------------------------------------------------------------------------------------------------
```

Add `--benchmark-autosave` to store each run in `.benchmarks/` and compare
later with `--benchmark-compare`; `make bench` keeps its own dated snapshots
in `benchmarks/history/`.

### Choosing a style

Use pytest-benchmark (`jm new --pytest-benchmark`, or `pytest_benchmark =
"true"` in `[project]` as in step 1) when you want CI regression tracking and
rich reporting. Use the default (timeit/perf_counter) when you want zero extra
dependencies and simple printout benchmarks.

---

## 6. Coverage

```sh
make coverage
```

Compiles a separate debug+`--coverage` build, runs the test suite, then
collects C coverage via **lcov/genhtml** and Python coverage via
**pytest-cov**:

```
C coverage: docs/coverage/c/index.html
...
_______________ coverage: platform linux, python 3.12.15-final-0 _______________

Name                                  Stmts   Miss  Cover   Missing
-------------------------------------------------------------------
src/my_dsp/__init__.py                    8      1    88%   7
src/my_dsp/benchmarks/__init__.py         0      0   100%
src/my_dsp/benchmarks/bench_ema.py       16     16     0%   9-34
src/my_dsp/benchmarks/bench_gain.py      28     28     0%   9-45
src/my_dsp/tests/__init__.py              0      0   100%
src/my_dsp/tests/test_ema.py             48      0   100%
src/my_dsp/tests/test_gain.py            69     21    70%   17-49
-------------------------------------------------------------------
TOTAL                                   169     66    61%
Coverage HTML written to dir docs/coverage/python
...
Python coverage: docs/coverage/python/index.html
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

## 7. API documentation

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
appear automatically in the rendered output. This example's test does exactly
that: it replaces the scaffold's `@brief Create a <obj> instance.` above each
`my_dsp_<obj>_create()` in `native/inc/my_dsp/<obj>/<obj>_core.h` with a real
one-sentence class summary (`gain`, `ema`), and `jm apply` flows it into the
generated Python class docstring — so the same comment feeds both the Doxygen
C site and the Zensical Python pages.

The same is true of the `my_dsp_gain_scale()` block from section 4 above: one comment
renders on the Doxygen C site, becomes the Python docstring the Zensical pages
show, and is *executed* as a doctest. Three artifacts, one place to edit.

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
make test         # CTest + unittest (gain); pytest src/ for ema
PYTHONPATH=src python -m pytest --doctest-glob='*.pyi' src/   # @code examples (see 4)
make bench        # C benchmarks + pytest-benchmark; run bench_gain.py directly
make coverage     # C (lcov) + Python (pytest-cov) HTML reports
make docs         # Doxygen (C API) + Zensical (Python API)
make clean        # remove build/, site/, docs/coverage/, docs/doxygen/
make help         # show this list
```
