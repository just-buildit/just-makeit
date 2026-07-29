# Feature tour

Build a complete signal-processing package from scratch — one module, two C
types, one function — using every major just-makeit feature in a single
copy-paste session.

______________________________________________________________________

## What you'll build

A `signal` module inside a `dsp_demo` package containing:

| Component      | Kind                 | Demonstrates                                                                                      |
| -------------- | -------------------- | ------------------------------------------------------------------------------------------------- |
| `NCO`          | stateful generator   | state vars, `--mutable`, `--class-name`, void input, perf annotations                             |
| `Fir`          | pure/no-state filter | `--no-state`, scalar + array + optional-array init-params, step, property, variable-output method |
| `magnitude_db` | module function      | `--inline`, `--out-type`, array param                                                             |

At the end you'll have a building, tested Python package with full C and Python
test coverage, ready to fill in with your algorithm.

______________________________________________________________________

## Prerequisites

```sh
pip install just-makeit && just-makeit install-deps
```

Or the one-liner installer:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

______________________________________________________________________

## Step 1 — Scaffold

```sh
just-makeit new dsp_demo --module signal --pytest
cd dsp_demo
```

`--module signal` creates the `signal` subpackage shell immediately.
`--pytest` generates pure pytest tests (instead of unittest).

**What you get:**

```
dsp_demo/
├── CMakeLists.txt
├── Makefile
├── pyproject.toml
├── just-makeit.toml
└── src/
    └── dsp_demo/
        └── signal/
            ├── __init__.py
            └── signal.pyi
```

The project builds and tests green out of the box (empty module, no objects yet).

______________________________________________________________________

## Step 2 — Stateful generator: NCO

A numerically controlled oscillator has persistent state (a phase accumulator)
and *mutates* it on every call, so it uses `--mutable`. Its step() takes no
input and produces one complex sample, so `--arg-type void`.

```sh
just-makeit object nco --module signal \
    --state "phase:uint32_t:0" \
    --mutable \
    --arg-type void --return-type "float _Complex" \
    --class-name NCO
```

`--class-name NCO` overrides the default `Nco` casing so the Python import
reads `from dsp_demo.signal import NCO`.

Add a writable `freq` property so callers can retune without a full reset:

```sh
just-makeit property nco freq --module signal --type double --writable
```

**What was created:**

| File                                    | Purpose                                              |
| --------------------------------------- | ---------------------------------------------------- |
| `native/inc/signal/nco_core.h`          | Struct + `static inline nco_step()` — implement here |
| `native/src/signal/nco_core.c`          | `nco_steps()` + lifecycle stubs                      |
| `native/src/signal/signal_ext_nco.c`    | CPython binding (auto-generated)                     |
| `native/tests/test_nco_core.c`          | C lifecycle smoke test                               |
| `src/dsp_demo/signal/tests/test_nco.py` | pytest integration test                              |

______________________________________________________________________

## Step 3 — No-state filter: Fir

A FIR filter owns its own state (coefficients + delay line), but that struct
is defined and allocated in C — you write `fir_create()` yourself rather than
having jm generate struct fields from TOML declarations. That is what
`--no-state` means: jm skips the auto-generated struct and just wires the
Python constructor to call your `fir_create()` with the init-params you
declare.

```sh
just-makeit object fir --module signal \
    --no-state \
    --init-param "n_taps:int:64" \
    --init-param "coeff:float _Complex[]" \
    --init-param "bank:float _Complex[][]:optional:fir_create_poly" \
    --arg-type "float _Complex" --return-type "float _Complex"
```

The three init-params demonstrate the three kinds:

| Param                                              | Form                | Python annotation                            |
| -------------------------------------------------- | ------------------- | -------------------------------------------- |
| `n_taps:int:64`                                    | scalar with default | `n_taps: int = 64`                           |
| `coeff:float _Complex[]`                           | required array      | `coeff: NDArray[np.complex64]`               |
| `bank:float _Complex[][]:optional:fir_create_poly` | optional 2-D array  | `bank: NDArray[np.complex64] \| None = None` |

When `bank` is provided, `fir_create_poly(dim0, dim1, ptr, n_taps)` is called
instead of the default `fir_create(coeff_ptr, coeff_len, n_taps)`.

Add a read-only `length` property and a variable-output method that returns the
current tap values:

```sh
just-makeit property fir length --module signal --type int
just-makeit method fir taps --module signal \
    --arg-type void --return-type "float _Complex" --variable-output
```

`--variable-output` means the output length is determined at runtime: the C
helper `fir_taps_max_out(state, n)` bounds it, and the kernel reports the
actual count. The Python binding allocates a NumPy-owned array per call and
returns it trimmed to that count — each result is independent and safe to
keep. See [Array memory ownership](memory-ownership.md).

!!! note "String-enum params (TOML-only)"

    Need a discrete choice constructor param like `mode: Literal["full", "polyphase"]`?
    Add it directly to `just-makeit.toml` after scaffolding:

    ```toml
    [[fir.init_params]]
    name    = "mode"
    type    = "string_enum:full,polyphase"
    default = "full"
    ```

    Then run `just-makeit apply` to regenerate the binding. The stub emits
    `mode: Literal["full", "polyphase"] = "full"` and adds
    `from typing import Literal` automatically.

______________________________________________________________________

## Step 4 — Module function: magnitude_db

A stateless utility that converts a complex buffer to dB magnitude. It
allocates a new `float` output array on each call (`--out-type float`) and
lives entirely in the header (`--inline`) so the compiler can inline it at
every call site.

```sh
just-makeit function magnitude_db --module signal \
    --param "x:float _Complex[]" \
    --param "floor:float" \
    --return-type void \
    --out-type float \
    --inline
```

The generated Python stub:

```python
def magnitude_db(x: NDArray[np.complex64], floor: float) -> NDArray[np.float32]:
    """Magnitude db."""
```

______________________________________________________________________

## Step 5 — Performance annotations

Once the algorithm is written and the tests pass, retrofit hot-path hints:

```sh
just-makeit perf
```

This writes `native/inc/jm_perf.h` (the `JM_HOT` / `JM_FORCEINLINE` macros),
patches `step()` in every `_core.h`, and records the setting in
`just-makeit.toml` so future objects inherit it automatically.

______________________________________________________________________

## Step 6 — Build and use

```sh
make && make test
```

Both C (CTest) and Python (pytest) tests run.

```python
import numpy as np
from dsp_demo.signal import NCO, Fir, magnitude_db

# Generator — no input, produces complex samples
nco = NCO(phase=0)
nco.set_freq(0.1)
x = nco.steps(1024)                   # → NDArray[np.complex64]

# No-state filter — coefficients set at construction
coeff = np.ones(64, dtype=np.complex64) / 64
fir = Fir(n_taps=64, coeff=coeff)
y = fir.steps(x)                      # → NDArray[np.complex64]

# Variable-output method
taps = fir.taps()                     # → NDArray[np.complex64], len = length

# Read-only property
print(fir.length)                     # → 64

# Module function
db = magnitude_db(x, floor=1e-6)     # → NDArray[np.float32]

# Context manager
with Fir(n_taps=64, coeff=coeff) as f:
    y = f.steps(x)
```

______________________________________________________________________

## What you used

- `--module` — module subpackage grouping multiple types into one `.so`
- `--state name:type:default` — persistent C struct fields
- `--mutable` — remove `const` from the state pointer in `step()`
- `--class-name` — override auto-cased Python class name
- `--arg-type void` — generator shape (no scalar input)
- `--return-type` — explicit output type
- `--no-state` — pure object, all config at construction time
- `--init-param name:type:default` — scalar constructor parameter
- `--init-param name:type[]` — required array constructor parameter
- `--init-param name:type[][]:optional:fn` — optional 2-D array with alternate create fn
- `string_enum` (TOML) — discrete-choice constructor parameter
- `just-makeit property` — read-only and writable Python properties
- `just-makeit method --variable-output` — runtime-length array output method
- `just-makeit function --inline --out-type` — header-inlined module function with array output
- `just-makeit perf` — hot-path performance annotations

______________________________________________________________________

!!! tip "Further reading"

    Features not shown here:

    - [`--impl file.c::funcname`](commands/scaffold.md) — lift a C body from an existing file into the generated stub (or `file.c::12:48` to lift a line range)
    - [`--replace old::new`](commands/scaffold.md) — rename symbols on lift
    - [`just-makeit regenerate <comp>`](commands/build.md) — delete a component's files and rebuild from the manifest, splicing hand-written `_core.c`/`_core.h` bodies back in by default (`--discard` for a clean reset; `git stash` first regardless)
    - [`result_fields`](commands/extend.md) — typed `list[tuple[...]]` return from a method
    - [`--multi-output`](commands/extend.md) — secondary output array on a variable-output method
    - [`--batch`](commands/extend.md) — 1:1-rate array transform
    - [`just-makeit split-objects`](commands/build.md) — move each object into its own TOML fragment
    - [`--no-step`](pure.md) — objects with only methods and properties, no primary algorithm
