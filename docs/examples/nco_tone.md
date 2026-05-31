# nco_tone example

Wire a just-makeit object to an external C library — the Doppler NCO —
demonstrating `find_package`, `extra_link_libs`, and opaque state holding a
library-owned handle.

## TL;DR — see it work first

```sh
just-makeit example nco_tone
# nco_tone: PASSED
```

!!! note "External dependency"

    This example links against the [Doppler](https://github.com/ju-plaggemann/doppler)
    DSP library. The test runner auto-fetches a prebuilt tarball when Doppler is
    not already installed; no manual step is needed unless you are working offline.

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

______________________________________________________________________

## What it demonstrates

- **`find_package` integration** — `jm new --find-package Doppler` wires
    `find_package(Doppler REQUIRED)` into the root `CMakeLists.txt`
- **`extra_link_libs`** — link the component's OBJECT library against a
    `find_package`-resolved target (`doppler::doppler_lib`)
- **Opaque state holding a library handle** — `nco_state_t *` from Doppler is
    declared opaque; `create_impl` initialises it, `destroy_impl` tears it down
- **`-DDoppler_DIR` on the cmake configure line** — pointing CMake at an
    installed library that lives outside the project tree

______________________________________________________________________

## 1. Write the fragment

```toml
# tone.toml
[tone]
arg_type         = "void"
return_type      = "float _Complex"
mutable          = "true"
extra_link_libs  = ["doppler::doppler_lib"]
create_impl      = """
obj->nco = dp_nco_create(freq_hz, sample_rate_hz);
if (!obj->nco) { free(obj); return NULL; }
"""
destroy_impl     = "dp_nco_destroy(state->nco);"

[[tone.init_params]]
name    = "freq_hz"
type    = "double"
default = "1000.0"

[[tone.init_params]]
name    = "sample_rate_hz"
type    = "double"
default = "48000.0"

[[tone.state]]
name   = "nco"
type   = "dp_nco_t *"
opaque = true
```

The `dp_nco_t *` field is invisible to Python — it is created in `create_impl`,
updated in `step()`, and released in `destroy_impl`.

______________________________________________________________________

## 2. Create a project and apply

```sh
just-makeit new tone_demo \
    --find-package Doppler
cd tone_demo
just-makeit apply ../tone.toml
```

`--find-package Doppler` writes `find_package(Doppler REQUIRED)` and a
`-DDoppler_DIR` hint comment into the root `CMakeLists.txt`.

______________________________________________________________________

## 3. Build (with Doppler installed)

```sh
cmake -B build \
    -DDoppler_DIR=/path/to/doppler/lib/cmake/Doppler \
    && cmake --build build
ctest --test-dir build
```

The example's test runner auto-fetches a prebuilt tarball and passes
`-DDoppler_DIR` automatically when running through `just-makeit example nco_tone`.

______________________________________________________________________

## 4. Implement step()

```c
/* native/inc/tone/tone_core.h */
static inline float _Complex
tone_step(tone_state_t *state)
{
    return dp_nco_step(state->nco);
}
```

The entire algorithm is delegated to the Doppler NCO — just-makeit owns the
Python binding glue.

______________________________________________________________________

## 5. Use from Python

```sh
pip install -e .
```

```python
import numpy as np
from tone_demo import Tone

# 1 kHz sine at 48 kHz sample rate
osc = Tone(freq_hz=1000.0, sample_rate_hz=48000.0)

# Generate 1024 complex samples
buf = osc.steps(1024)
print(buf.dtype)    # complex64
print(buf.shape)    # (1024,)

# Power check: |e^{jωt}| = 1
print(abs(buf).mean())  # ≈ 1.0
```

______________________________________________________________________

## Key concepts

**`extra_link_libs` links the OBJECT library to a CMake target.** The component's
CMake block grows a `target_link_libraries(tone_core ... doppler::doppler_lib)`
line, making the Doppler headers and shared library available during compilation
and linking.

**`find_package` at the project level, linking at the component level.** The
project-level `find_package(Doppler REQUIRED)` makes the `doppler::doppler_lib`
import target available. The component-level `extra_link_libs` consumes it.
Other components in the same project that don't use Doppler are unaffected.

**Opaque state delegates lifetime to the library.** The `dp_nco_t *` is
created and destroyed by Doppler's own API (`dp_nco_create` /
`dp_nco_destroy`); just-makeit's `create_impl` / `destroy_impl` are the
bridge. Python never sees the handle.

## See also

- [C library distribution](../c-library.md) — how the generated combined library
    and pkg-config file let C consumers link the same code
- [Declarative scaffolding — `c_deps` and `find_packages`](../declarative-scaffolding.md#integrating-hand-written-c-libraries-c_deps-no_generate-depends_on)
- [Scaffold commands — `--find-package`](../commands/scaffold.md)
