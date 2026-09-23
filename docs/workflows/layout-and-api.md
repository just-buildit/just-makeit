# Project layout & generated API

## Project layout (full)

After scaffolding with one object and running `just-makeit perf`. The tag on
each file is who owns it, which decides what `jm apply` does to it; the five
kinds are defined in [the edit lifecycle](edit-lifecycle.md#who-owns-each-file).

```text
my_dsp/
├── just-makeit.toml                    [yours]     the manifest
├── objects/
│   └── gain.toml                       [yours]     gain's manifest table
├── pyproject.toml                      [yours]
├── README.md                           [yours]
├── CMakeLists.txt                      [shared]    jm splices its marked blocks
├── CMakePresets.json                   [versioned] cmake --preset release|debug|…
├── Makefile                            [versioned] make · make test · make bench
├── bootstrap.toml                      [versioned] toolchain declaration for CI
├── .clang-tidy  .gitignore  Doxyfile  zensical.toml          [versioned]
├── cmake/
│   ├── my-dsp.pc.in                    [versioned] pkg-config template
│   └── my_dsp-config.cmake.in          [versioned] find_package template
├── docs/                               [yours]     index.md, api.md
├── benchmarks/history/                 [yours]     saved `jm bench` results
├── native/
│   ├── inc/
│   │   ├── my_dsp.h                    [jm]        umbrella header
│   │   ├── clib_common.h               [versioned] common C99 types
│   │   ├── pyex_common.h               [versioned] Python extension includes
│   │   ├── jm_perf.h                   [versioned] JM_FORCEINLINE / JM_HOT / …
│   │   ├── jm_simd.h                   [versioned] width-portable SIMD macros
│   │   └── gain/
│   │       └── gain_core.h             [yours]     state struct + step()  ← implement
│   ├── src/
│   │   ├── my_dsp_lib.c                [yours]     libmy_dsp root: my_dsp_version()
│   │   └── gain/
│   │       ├── CMakeLists.txt          [jm]
│   │       ├── gain_core.c             [yours]     create/destroy/steps/methods
│   │       └── gain_ext.c              [jm]        Python binding  ← do not edit
│   ├── tests/
│   │   ├── jm_test.h                   [versioned] test macros
│   │   ├── test_gain_core.c            [yours]     CTest lifecycle test
│   │   └── test_gain_symbols.c         [derived]   links every function the binding calls
│   └── benchmarks/
│       ├── jm_bench.h                  [versioned] portable timer
│       └── bench_gain_core.c           [yours]     C benchmark
└── src/
    └── my_dsp/
        ├── __init__.py                 [shared]    jm keeps the re-exports current
        ├── gain.pyi                    [jm]        type stub
        ├── benchmarks/bench_gain.py    [yours]
        └── tests/test_gain.py          [jm]        pytest; yours once you delete its `# jm:generated` line
```

`[yours]` files are created once and then only gain what is missing — a
declaration or a stub for a new method. `[jm]` files are rewritten on every
`apply`. `[versioned]` files are jm's but written once; a newer jm reports
them as OUTDATED in `jm status`.

______________________________________________________________________

## Generated C API

Every object follows this lifecycle. Names are derived from the component
name you pass to `just-makeit object`:

```c
/* Constructor — one parameter per --state declaration */
engine_state_t *engine_create(double gain);

/* Destructor */
void engine_destroy(engine_state_t *state);

/* Reset — restores every field to its declared default */
void engine_reset(engine_state_t *state);

/* Single sample — inline stub in _core.h; implement here */
static inline float _Complex
engine_step(const engine_state_t *state, float _Complex x);

/* Block processor — in _core.c, loops over step() */
void engine_steps(
    engine_state_t       *state,
    const float _Complex  *input,
    float _Complex        *output,
    size_t                n);

/* Generator (--arg-type void) — no input parameter */
static inline float
nco_step(const nco_state_t *state);
void nco_steps(nco_state_t *state, float *output, size_t n);

/* Getter and setter for each --state variable */
double engine_get_gain(const engine_state_t *state);
void   engine_set_gain(engine_state_t *state, double val);
```

______________________________________________________________________

## Generated Python API

```python
from my_project import Engine   # standalone object
import numpy as np

obj = Engine(gain=1.0)   # keyword arg per --state variable
obj = Engine()           # uses declared defaults

y: complex = obj.step(1.0 + 0.5j)       # single sample

x = np.ones(1024, dtype=np.complex64)
y = obj.steps(x)                         # returns new array
obj.steps(x, out=y)                      # zero-copy: fills y, returns y

obj.get_gain()                           # getter
obj.set_gain(2.0)                        # setter
obj.reset()                              # restores declared defaults

with Engine() as e:                      # context manager
    y = e.steps(x)

# Module subpackage — one .so, one subpackage import
from my_filters.filter import Fir, Biquad
fir = Fir(gain=1.0)
bq  = Biquad(b0=1.0)
```

Types within a module are fully independent — separate lifecycles, each
with its own `step`, `steps`, `reset`, getters/setters, and context
manager.
