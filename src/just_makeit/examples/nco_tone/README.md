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

    This example links against the [Doppler](https://github.com/doppler-dsp/doppler)
    DSP library. The test runner fetches Doppler's **latest** prebuilt release into
    a per-user cache (`~/.cache/jm-tests/doppler/v<version>/<platform>`) and builds
    against that, so no manual step is needed and a local run matches CI, which
    downloads the latest release too.

    It deliberately does **not** search for an installed Doppler. Scanning
    `/usr/local`, `~/.local` and `~/doppler/build` meant that on any machine with
    Doppler present the fetched version was never used — and the versions silently
    disagreed. If you want a specific Doppler, say so with `--doppler-prefix`.

!!! tip "Installing Doppler permanently instead of per-run"

    The per-run fetch is a convenience for trying the example. If you are building
    against Doppler for real, install it once and point the example at it with
    `--doppler-prefix`. Either form works — `find_package(Doppler)` searches both
    `<prefix>/lib/cmake` and `<prefix>/lib64/cmake`.

    **From a release tarball** (platform tags: `linux-x86_64`, `linux-aarch64`,
    `macos-arm64`; Windows is `doppler-$VER-windows-x86_64.zip`):

    ```sh
    VER=0.55.0; PLAT=linux-x86_64
    curl -fsSL -o /tmp/doppler.tar.gz \
        "https://github.com/doppler-dsp/doppler/releases/download/v$VER/doppler-$VER-$PLAT.tar.gz"
    mkdir -p ~/.local/doppler
    tar xzf /tmp/doppler.tar.gz -C ~/.local/doppler --strip-components=1
    ```

    **Or from source:**

    ```sh
    git clone https://github.com/doppler-dsp/doppler && cd doppler
    cmake -B build -DCMAKE_INSTALL_PREFIX="$HOME/.local/doppler"
    cmake --build build && cmake --install build
    ```

    Then run the example against it:

    ```sh
    python src/just_makeit/examples/nco_tone/test.py \
        --doppler-prefix ~/.local/doppler
    ```

    A prefix passed this way is printed in the run output, so which Doppler a
    build used is always answerable from the log.

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
    `find_package`-resolved target (`doppler::doppler-static`)
- **Opaque state holding a library handle** — `nco_state_t *` from Doppler is
    declared opaque; `create_impl` initialises it, `destroy_impl` tears it down
- **`-DDoppler_DIR` on the cmake configure line** — pointing CMake at an
    installed library that lives outside the project tree

______________________________________________________________________

## 1. Write the fragment

```toml
# tone.toml
[tone]
arg_type        = "void"
return_type     = "float _Complex"
mutable         = "true"
extra_link_libs = ["doppler::doppler-static"]
create_impl     = """
obj->nco = nco_create(norm_freq, 0);
if (!obj->nco) { free(obj); return NULL; }
"""
destroy_impl    = """
nco_destroy(state->nco);
"""

[[tone.state]]
name    = "norm_freq"
type    = "double"
default = "0.0"

[[tone.state]]
name   = "nco"
type   = "nco_state_t *"
opaque = true
```

`norm_freq` is the normalized frequency (cycles/sample) passed to the
constructor; `create_impl` forwards it to `nco_create`. The `nco_state_t *`
field is invisible to Python — it is created in `create_impl`, updated in
`step()`, and released in `destroy_impl`.

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

First make Doppler's NCO header and `<math.h>` visible by adding them after
the always-present `clib_common.h` include:

```c
/* native/inc/nco_tone_demo/tone/tone_core.h */
#include "nco_tone_demo/clib_common.h"
#include "nco/nco_core.h"
#include <math.h>
```

Then fill in `step()` — advance the NCO one sample and map its phase
accumulator to a unit-magnitude complex exponential:

```c
static inline float _Complex
nco_tone_demo_tone_step(nco_tone_demo_tone_state_t *state)
{
    uint32_t phase;
    /* n samples, then the capacity of `out` (doppler >= 0.39) */
    nco_steps_u32(state->nco, 1, &phase, 1);
    /* phase in [0, 2^32) maps to angle in [0, 2*pi) */
    float angle = (float)phase
        * (float)(2.0 * 3.14159265358979323846 / 4294967296.0);
    return cosf(angle) + I * sinf(angle);
}
```

The phase generation is delegated to the Doppler NCO — just-makeit owns the
Python binding glue.

______________________________________________________________________

## 5. Use from Python

```sh
pip install -e .
```

```python
import numpy as np
from tone_demo import Tone

# 0.25 cycles/sample — a quarter turn of the unit circle each step
osc = Tone(norm_freq=0.25)

# Generate 1024 complex samples
buf = osc.steps(1024)
print(buf.dtype)    # complex64
print(buf.shape)    # (1024,)

# Power check: |e^{jωt}| = 1
print(abs(buf).mean())  # ≈ 1.0

# Four quarter-circle steps cycle through 1, j, -1, -j
print([osc.step() for _ in range(4)])  # ≈ [1, 1j, -1, -1j]
```

______________________________________________________________________

## Key concepts

**`extra_link_libs` links the OBJECT library to a CMake target.** The component's
CMake block grows a `target_link_libraries(tone_core ... doppler::doppler-static)`
line, making the Doppler headers and static library available during compilation
and linking.

**`find_package` at the project level, linking at the component level.** The
project-level `find_package(Doppler REQUIRED)` makes the `doppler::doppler-static`
import target available. The component-level `extra_link_libs` consumes it.
Other components in the same project that don't use Doppler are unaffected.

**Opaque state delegates lifetime to the library.** The `nco_state_t *` is
created and destroyed by Doppler's own API (`nco_create` /
`nco_destroy`); just-makeit's `create_impl` / `destroy_impl` are the
bridge. Python never sees the handle.

## See also

- [C library distribution](../c-library.md) — how the generated combined library
    and pkg-config file let C consumers link the same code
- [Declarative scaffolding — `c_deps` and `find_packages`](../declarative-scaffolding.md#integrating-hand-written-c-libraries-c_deps-no_generate-depends_on)
- [Scaffold commands — `--find-package`](../commands/scaffold.md)
