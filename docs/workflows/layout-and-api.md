# Project layout & generated API

## Project layout (full)

After scaffolding with one object and running `just-makeit perf`:

```text
my_dsp/
├── CMakeLists.txt
├── Makefile
├── just-makeit.toml
├── pyproject.toml
├── cmake/
│   └── my-dsp.pc.in                    # pkg-config template
├── native/
│   ├── benchmarks/
│   │   └── bench_gain_core.c           # C benchmark
│   ├── inc/
│   │   ├── clib_common.h               # common C99 types
│   │   ├── pyex_common.h               # Python extension includes
│   │   ├── my_dsp.h                    # umbrella header
│   │   ├── jm_perf.h                   # JM_FORCEINLINE / JM_HOT / JM_UNROLL …
│   │   ├── jm_simd.h                   # width-portable SIMD macros
│   │   └── gain/
│   │       └── gain_core.h             # object API  ← implement step() here
│   ├── src/
│   │   └── gain/
│   │       ├── CMakeLists.txt
│   │       ├── gain_core.c             # steps() loop + any multi-sample logic
│   │       └── gain_ext.c              # Python binding  ← do not edit
│   └── tests/
│       └── test_gain_core.c            # CTest lifecycle test
└── src/
    └── my_dsp/
        ├── __init__.py
        ├── gain.pyi                    # type stub
        ├── benchmarks/
        │   ├── __init__.py
        │   └── bench_gain.py           # perf_counter benchmark script
        └── tests/
            ├── __init__.py
            └── test_gain.py            # pytest
```

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
static inline float complex
engine_step(const engine_state_t *state, float complex x);

/* Block processor — in _core.c, loops over step() */
void engine_steps(
    engine_state_t       *state,
    const float complex  *input,
    float complex        *output,
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
