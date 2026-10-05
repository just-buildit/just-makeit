# Project template

## Directory structure

The tree below is the part of the scaffold you actually touch — the C
core, its Python binding, and the tests. `just-makeit new` also emits
project-level scaffolding (docs, packaging, CI config) around it; see
[Configuration — project layout](configuration.md#project-layout-and-schema)
for the complete, generated-file-by-file tree.

```
<project>/
    native/
        inc/
            <project>/
                <project>.h             # umbrella header (every component)
                clib_common.h           # common C99 types
                pyex_common.h           # common Python extension includes
                <component>/
                    <component>_core.h  # public API + inline step()
        src/
            <component>/
                <component>_core.c      # your algorithm lives here (sacred)
                <component>_ext.c       # thin Python binding
        tests/
            test_<component>_core.c     # CTest
    src/
        <package>/
            __init__.py
            <component>.pyi             # type stub
            tests/
                test_<component>.py     # pytest
    just-makeit.toml                    # project manifest (source of truth)
    objects/
        <component>.toml                # this component's manifest section
    CMakeLists.txt
    Makefile
    pyproject.toml
    .gitignore
    README.md
```

______________________________________________________________________

## C conventions

Every C symbol is spelled with the component's C stem,
`<pkg>_<comp>`: `[project] c_prefix` plus the component name. `jm new`
defaults `c_prefix` to the project name (`jm new proj` + `jm object filt`
gives `proj_filt_create()`); `jm new --no-c-prefix` gives bare
`filt_create()`. File names (`filt_core.c`) and CMake targets
(`filt_core`) keep the plain component name.

### State struct

One field is generated for each `--state name:type` flag.

```c
typedef struct {
    float   gain;
    float   bandwidth;
    int32_t order;
} <pkg>_<comp>_state_t;
```

### Constructor

One parameter per `--state` var, in declaration order.

```c
/**
 * @return Heap-allocated state, or NULL on allocation failure.
 * @note Caller must call <pkg>_<comp>_destroy() when done.
 */
<pkg>_<comp>_state_t *<pkg>_<comp>_create(float gain, float bandwidth, int32_t order);
```

### Destructor

```c
/**
 * @param state  May be NULL.
 */
void <pkg>_<comp>_destroy(<pkg>_<comp>_state_t *state);
```

### Reset

Restores all state variables to their declared defaults.

```c
void <pkg>_<comp>_reset(<pkg>_<comp>_state_t *state);
```

### Single-sample processor

Generated as a pass-through stub — implement your DSP here.

```c
static inline float _Complex
<pkg>_<comp>_step(const <pkg>_<comp>_state_t *state, float _Complex x)
{
    (void)state; /* TODO: implement using state variables */
    return (float _Complex)x;
}
```

### Block processor

```c
/**
 * @param output  Pre-allocated by caller; may alias input for in-place.
 */
void <pkg>_<comp>_steps(
    <pkg>_<comp>_state_t *state,
    const float _Complex  *input,
    float _Complex        *output,
    size_t                n);
```

### Getter / setter (one pair per `--state` var)

```c
float   <pkg>_<comp>_get_gain(const <pkg>_<comp>_state_t *state);
void    <pkg>_<comp>_set_gain(<pkg>_<comp>_state_t *state, float val);

float   <pkg>_<comp>_get_bandwidth(const <pkg>_<comp>_state_t *state);
void    <pkg>_<comp>_set_bandwidth(<pkg>_<comp>_state_t *state, float val);

int32_t <pkg>_<comp>_get_order(const <pkg>_<comp>_state_t *state);
void    <pkg>_<comp>_set_order(<pkg>_<comp>_state_t *state, int32_t val);
```

______________________________________________________________________

## Python binding conventions

The generated `<component>_ext.c` exposes the C API as a Python class:

```python
from <package> import <Component>

# lifecycle
obj = <Component>(gain=1.0, bandwidth=200.0, order=4)

# single sample
y: complex = obj.step(1.0 + 0.0j)

# block (numpy)
import numpy as np
x = np.ones(256, dtype=np.complex64)
y = obj.steps(x)  # returns complex64 ndarray

# getters / setters
obj.get_gain()
obj.set_gain(2.0)

# reset (restores to declared defaults)
obj.reset()

# context manager (auto-destroys)
with <Component>(1.0) as obj:
    y = obj.step(1.0 + 0.0j)

# explicit release
obj.destroy()
```

______________________________________________________________________

## Build system

`CMakeLists.txt` uses `Python3_add_library` (CMake ≥ 3.16) to build the
extension. The `.so` is written directly into `src/<package>/` so the
package is importable from the source tree after a single `make`.

`pyproject.toml` declares `just-buildit` as the PEP 517 backend with
`command = "make just-build"`. The `just-build` Makefile target copies
`src/<package>/` (Python files + `.so`) to `$JUST_BUILDIT_OUTPUT_DIR` for
packaging.

```
make                  →  cmake configure + build
make test             →  ctest + the Python tests (unittest discovery)
make just-build       →  build + copy to $JUST_BUILDIT_OUTPUT_DIR
pip install .         →  just-buildit → make just-build → wheel
pip install -e .      →  just-buildit editable (.pth pointing at src/)
```
