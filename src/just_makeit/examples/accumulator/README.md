# accumulator example

Two running accumulators in a shared Python extension module — `AccF32`
(single-precision float) and `AccCf64` (double-precision complex) — built with
just-makeit from scratch.

An accumulator is the simplest stateful DSP primitive: push samples in, read
the running total out, reset when done.  This example is intentionally
straightforward so you can focus on the jm workflow rather than on the
algorithm.

| Type     | C type          | Python dtype        | Precision  |
| -------- | --------------- | ------------------- | ---------- |
| `AccF32` | `float`         | `np.float32`        | 32-bit     |
| `AccCf64`| `double complex`| `np.complex128`     | 128-bit    |

Both live in a shared `accumulator` subpackage:

```python
from my_acc.accumulator import AccF32, AccCf64
```

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example accumulator
# accumulator: all checks passed
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

---

## 1. Scaffold the project

```sh
just-makeit new my_acc
cd my_acc
just-makeit module accumulator
```

`just-makeit new` with no `--object` creates the project skeleton only:
`CMakeLists.txt`, `pyproject.toml`, `just-makeit.toml`, and the `native/`
directory tree.  No component yet.

`just-makeit module accumulator` adds a named module slot:

| Created                                   | Purpose                       |
| ----------------------------------------- | ----------------------------- |
| `native/src/accumulator/accumulator_ext.c`| C extension (empty, no types) |
| `native/src/accumulator/CMakeLists.txt`   | Python module target          |
| `src/my_acc/accumulator/__init__.py`      | Subpackage init (empty)       |

`just-makeit.toml` gains:

```toml
[module.accumulator]
objects = []
```

Objects are added with `just-makeit object` next.

---

## 2. Add the accumulator types

```sh
just-makeit object acc_f32 \
    --module accumulator \
    --arg-type float \
    --return-type void \
    --state "acc:float:0.0f" \
    --mutable

just-makeit object acc_cf64 \
    --module accumulator \
    --arg-type "double _Complex" \
    --return-type void \
    --state "acc:double _Complex:0.0 + 0.0 * I" \
    --mutable
```

### The key insight: jm already gives you push and add

Before reaching for named methods, notice what jm scaffolds automatically:

| jm pattern      | accumulator meaning            | generated C               |
| --------------- | ------------------------------ | ------------------------- |
| `step(x)`       | push one sample                | `acc_f32_step(state, x)`  |
| `steps(x[])`    | batch-add an array of samples  | `acc_f32_steps(state, x, n)` |
| `reset()`       | zero the accumulator           | `acc_f32_reset(state)`    |

`step(x) -> void` with `--mutable` and `--return-type void` is exactly a push
operation.  `steps()` is the auto-generated batch loop that calls `step()` in a
tight loop — it is add.  You do not need to implement these; jm writes them.

`--mutable` drops the `const` qualifier from the state pointer in `step()` so
the implementation can write to `state->acc`.

### State fields

Both types follow the same layout.  The only difference is the C type:

| Object    | Field | Type            | Default          |
| --------- | ----- | --------------- | ---------------- |
| `acc_f32` | `acc` | `float`         | `0.0f`           |
| `acc_cf64`| `acc` | `double _Complex`| `0.0 + 0.0 * I` |

After both commands `just-makeit.toml` contains:

```toml
[module.accumulator]
objects = ["acc_f32", "acc_cf64"]
```

And `src/my_acc/accumulator/__init__.py` exports both types:

```python
from .accumulator import AccF32, AccCf64

__all__ = ["AccF32", "AccCf64"]
```

---

## 3. Add named methods

```sh
# AccF32 named methods
just-makeit method acc_f32 get \
    --module accumulator \
    --arg-type void \
    --return-type float

just-makeit method acc_f32 dump \
    --module accumulator \
    --arg-type void \
    --return-type float

just-makeit method acc_f32 madd \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:float[]" \
    --param "h:float[]"

just-makeit method acc_f32 add2d \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:float[]"

just-makeit method acc_f32 madd2d \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:float[]" \
    --param "h:float[]"

# AccCf64 named methods
just-makeit method acc_cf64 get \
    --module accumulator \
    --arg-type void \
    --return-type "double _Complex"

just-makeit method acc_cf64 dump \
    --module accumulator \
    --arg-type void \
    --return-type "double _Complex"

just-makeit method acc_cf64 madd \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:double _Complex[]" \
    --param "h:float[]"

just-makeit method acc_cf64 add2d \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:double _Complex[]"

just-makeit method acc_cf64 madd2d \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:double _Complex[]" \
    --param "h:float[]"
```

Five extra methods per type — ten commands total.  Why can't these be `step`?

| Method   | Why it's not `step`                                               |
| -------- | ----------------------------------------------------------------- |
| `get`    | Read-only peek at the accumulator; no input arg, different return |
| `dump`   | Atomic read-then-zero: `v = acc; acc = 0; return v`              |
| `madd`   | Weighted accumulate: `acc += dot(x, h)` — two array inputs        |
| `add2d`  | Flat accumulate of an input array (step applied row-by-row)       |
| `madd2d` | Weighted flat accumulate: `madd` applied to each row              |

`dump` is the interesting one.  It returns the current total *and* zeroes the
accumulator in one atomic C call.  There is no way to express "return current
value and mutate state" with just `step()` semantics.

All array-input methods use `--arg-type void` with `--param "name:type[]"`.
Each array param expands to two C arguments — a `const elem_t *name` pointer
and a `size_t name_len` length — and a matching NumPy buffer acquisition in
the Python glue.

After these ten commands, `accumulator_ext.c` contains `AccF32Object` and
`AccCf64Object` with fully generated Python argument parsing and NumPy buffer
protocol for all array parameters.

---

## 4. Implement

All implementations are trivially short. jm generates all the scaffolding;
you only fill in the algorithm body.

### step — one line, same pattern for both types

Open the generated header and replace the `(void)state; (void)x; /* TODO: implement */`
stub. The only difference between the two is the C type — the logic is
`state->acc += x` in both cases:

**`native/inc/acc_f32/acc_f32_core.h`**

```c
static inline void
acc_f32_step(acc_f32_state_t *state, float x)
{
    state->acc += x;
}
```

**`native/inc/acc_cf64/acc_cf64_core.h`**

```c
static inline void
acc_cf64_step(acc_cf64_state_t *state, double complex x)
{
    state->acc += x;
}
```

`steps()` is already done — jm generates the batch loop in `_core.c` that
calls `step()` for each element. You get `steps()` = `add()` for free.

### Named methods — `native/src/acc_f32/acc_f32_core.c`

`get` reads the accumulator. `dump` is the interesting one: capture first,
then zero, then return the captured value. The order matters.

```c
float
acc_f32_get(acc_f32_state_t *state)
{
    return state->acc;
}

float
acc_f32_dump(acc_f32_state_t *state)
{
    float v = state->acc;
    state->acc = 0.0f;
    return v;
}

void
acc_f32_madd(
    acc_f32_state_t *state,
    const float *x, size_t x_len,
    const float *h, size_t h_len)
{
    size_t n = x_len < h_len ? x_len : h_len;
    for (size_t i = 0; i < n; i++)
        state->acc += x[i] * h[i];
}

void
acc_f32_add2d(acc_f32_state_t *state, const float *x, size_t x_len)
{
    for (size_t i = 0; i < x_len; i++)
        state->acc += x[i];
}

void
acc_f32_madd2d(
    acc_f32_state_t *state,
    const float *x, size_t x_len,
    const float *h, size_t h_len)
{
    size_t n = x_len < h_len ? x_len : h_len;
    for (size_t i = 0; i < n; i++)
        state->acc += x[i] * h[i];
}
```

### Named methods — `native/src/acc_cf64/acc_cf64_core.c`

Note the `(double)h[i]` cast in `madd` and `madd2d`: `h` is `float` (real
weights), `x` is `double complex`. Widening before the multiply preserves
precision in the intermediate result.

```c
double complex
acc_cf64_get(acc_cf64_state_t *state)
{
    return state->acc;
}

double complex
acc_cf64_dump(acc_cf64_state_t *state)
{
    double complex v = state->acc;
    state->acc = 0.0 + 0.0 * I;
    return v;
}

void
acc_cf64_madd(
    acc_cf64_state_t *state,
    const double complex *x, size_t x_len,
    const float *h, size_t h_len)
{
    size_t n = x_len < h_len ? x_len : h_len;
    for (size_t i = 0; i < n; i++)
        state->acc += x[i] * (double)h[i];
}

void
acc_cf64_add2d(
    acc_cf64_state_t *state,
    const double complex *x, size_t x_len)
{
    for (size_t i = 0; i < x_len; i++)
        state->acc += x[i];
}

void
acc_cf64_madd2d(
    acc_cf64_state_t *state,
    const double complex *x, size_t x_len,
    const float *h, size_t h_len)
{
    size_t n = x_len < h_len ? x_len : h_len;
    for (size_t i = 0; i < n; i++)
        state->acc += x[i] * (double)h[i];
}
```

The patch scripts automate these edits:

```sh
python3 .steps/04_patch_f32.py
python3 .steps/04_patch_cf64.py
```

---

## 5. Build and test

```sh
make
make test
```

`make` runs cmake configure + compile.  `make test` runs CTest (the C smoke
tests) and then the auto-generated Python integration tests.

The generated C tests in `native/tests/test_acc_f32_core.c` and
`native/tests/test_acc_cf64_core.c` exercise `create`, `reset`, and the
`step`/`steps` round-trip using the `CHECK` macro.

Expected output:

```
[100%] Built target accumulator
Test project /tmp/.../my_acc/build
    Start 1: test_acc_f32_core
1/2 Test #1: test_acc_f32_core ............   Passed
    Start 2: test_acc_cf64_core
2/2 Test #2: test_acc_cf64_core ............   Passed

100% tests passed, 0 tests failed out of 2
```

---

## 6. Use from Python

```python
"""Quick demo: AccF32 and AccCf64 from Python."""

import sys

sys.path.insert(0, "src")

import numpy as np

from my_acc.accumulator import AccCf64, AccF32

# --- AccF32: step == push ---
f = AccF32()
f.step(np.float32(1.0))
f.step(np.float32(2.0))
f.step(np.float32(3.0))
print(f"AccF32 after push 1+2+3: get() = {f.get()}")  # 6.0

# steps == batch add
f.reset()
f.steps(np.ones(100, dtype=np.float32))
print(f"AccF32 after steps(ones*100): get() = {f.get()}")  # 100.0

# dump: atomic get + reset
f.reset()
f.step(np.float32(42.0))
v = f.dump()
print(f"AccF32 dump() = {v}, get() after = {f.get()}")  # 42.0, 0.0

# madd: weighted sum
f.reset()
x = np.array([1, 2, 3, 4], dtype=np.float32)
h = np.array([0.25, 0.25, 0.25, 0.25], dtype=np.float32)
f.madd(x, h)
print(f"AccF32 madd([1,2,3,4], [0.25]*4): get() = {f.get()}")  # 2.5

# add2d: 2-D shaped accumulate (flattened through C)
f.reset()
mat = np.arange(12, dtype=np.float32).reshape(3, 4)
for row in mat:
    f.add2d(row)
print(f"AccF32 add2d(3x4 arange): get() = {f.get()}")  # 66.0

# --- AccCf64: step with complex ---
c = AccCf64()
c.step(1 + 2j)
c.step(3 + 4j)
g = c.get()
print(f"AccCf64 after push (1+2j)+(3+4j): get() = {g}")  # (4+6j)

# AccCf64 madd: complex signal, real weights
c.reset()
sig = np.array([1 + 1j, 2 + 2j, 3 + 3j], dtype=np.complex128)
w = np.array([1.0, 0.5, 0.25], dtype=np.float32)
c.madd(sig, w)
g2 = c.get()
# (1+1j)*1.0 + (2+2j)*0.5 + (3+3j)*0.25 = (2.75+2.75j)
print(f"AccCf64 madd: get() = {g2}")

# AccCf64 dump: returns value and zeroes
c.reset()
c.step(5 + 6j)
dumped = c.dump()
print(f"AccCf64 dump() = {dumped}, get() after = {c.get()}")
```

Run it from `my_acc/`:

```sh
python3 .steps/06_demo.py
```

Expected output:

```
AccF32 after push 1+2+3: get() = 6.0
AccF32 after steps(ones*100): get() = 100.0
AccF32 dump() = 42.0, get() after = 0.0
AccF32 madd([1,2,3,4], [0.25]*4): get() = 2.5
AccF32 add2d(3x4 arange): get() = 66.0
AccCf64 after push (1+2j)+(3+4j): get() = (4+6j)
AccCf64 madd: get() = (2.75+2.75j)
AccCf64 dump() = (5+6j), get() after = 0
```

All operations go through the C extension with no Python arithmetic.  The
`steps()` method is the auto-generated batch loop — you get it for free without
writing a single line of looping code.

---

## 7. Bonus: `just-makeit perf` + explicit SIMD

### 7.1 Enable perf infrastructure

```sh
# run from inside my_acc
just-makeit perf
```

One command adds three things to the project:

| Added | Effect |
| ----- | ------ |
| `native/inc/jm_perf.h` | `JM_FORCEINLINE`, `JM_HOT`, `JM_RESTRICT`, `JM_LIKELY`, ... |
| `native/inc/jm_simd.h` | `JM_VEC_F32`, `JM_ADD_F32`, `JM_LOAD_F32`, `JM_HSUM_F32`, `JM_SIMD_WIDTH_F32` |
| `#include "jm_perf.h"` inserted in each `_core.h` | Makes all macros available to every `.c` that includes the header |

`just-makeit.toml` gains `perf = "true"` and each object's `step()` qualifier is
upgraded from `static inline` to `JM_FORCEINLINE JM_HOT`.

### 7.2 Benchmark

Save this as `bench.py` in `my_acc/`:

```python
"""Measure AccF32.steps() and AccCf64.steps() throughput (samples/sec)."""

import sys
import timeit

import numpy as np

sys.path.insert(0, "src")
from my_acc.accumulator import AccCf64, AccF32

BLOCK = 100_000
RUNS = 1_000

f = AccF32()
sig_f32 = np.random.randn(BLOCK).astype(np.float32)
elapsed = min(timeit.repeat(lambda: f.steps(sig_f32), number=RUNS, repeat=5))
print(
    f"AccF32  {BLOCK:>7,} samples  "
    f"{RUNS * BLOCK / elapsed / 1e9:.2f} G samples/sec"
)

c = AccCf64()
sig_c128 = (np.random.randn(BLOCK) + 1j * np.random.randn(BLOCK)).astype(
    np.complex128
)
elapsed = min(timeit.repeat(lambda: c.steps(sig_c128), number=RUNS, repeat=5))
print(
    f"AccCf64 {BLOCK:>7,} samples  "
    f"{RUNS * BLOCK / elapsed / 1e9:.2f} G samples/sec"
)
```

Build and measure across three stages:

```sh
PY=$(python3 -c "import sys; print(sys.executable)")

# Stage 1 — Release build, no SIMD flags (scalar reduction)
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DPython3_EXECUTABLE="$PY" \
    -DCMAKE_VERBOSE_MAKEFILE=OFF -Wno-dev -q
cmake --build build --parallel -q
echo "=== baseline (scalar) ==="
python3 .steps/07_bench.py

# Stage 2 — ENABLE_SIMD=ON: adds -march=native -ffast-math
#           -ffast-math allows the compiler to reassociate the reduction
#           and auto-vectorise steps() using AVX2 / AVX-512 lanes.
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DENABLE_SIMD=ON \
    -DPython3_EXECUTABLE="$PY" -Wno-dev -q
cmake --build build --parallel -q
echo "=== ENABLE_SIMD=ON (auto-vectorised) ==="
python3 .steps/07_bench.py

# Stage 3 — Explicit SIMD: replace steps() with JM_ADD_F32 + JM_HSUM_F32,
#           rebuild with ENABLE_SIMD=ON still active.
python3 .steps/07_patch_perf.py
cmake --build build --parallel -q
echo "=== explicit SIMD (JM_ADD_F32 + JM_HSUM_F32) ==="
python3 .steps/07_bench.py
```

### 7.3 Results

Measured on x86-64 (AVX2), AMD Ryzen 9, `BLOCK = 100_000`:

```
=== Stage 1: Release -O3, JM_FORCEINLINE JM_HOT on step(), scalar steps() ===
AccF32  100,000 samples  1.66 G samples/sec   1.0×

=== Stage 2: ENABLE_SIMD=ON (-ffast-math -march=native) ===
AccF32  100,000 samples  1.65 G samples/sec   ≈1.0×

=== Stage 3: explicit JM_VEC_F32 + JM_RESTRICT, ENABLE_SIMD=ON ===
AccF32  100,000 samples  18.11 G samples/sec  10.9×
```

### 7.4 Why stage 2 does not improve

Stage 1 to stage 2 adds `-ffast-math` and `-march=native`.  `-ffast-math` allows
the compiler to reassociate the reduction — a prerequisite for vectorisation.
So why does stage 2 show no gain?

The generated `acc_f32_steps()` signature is:

```c
void acc_f32_steps(
    acc_f32_state_t *state,
    const float     *input,
    size_t           n)
```

Both `state` (which contains `state->acc`, a `float`) and `input` are `float`
pointers.  Without a `restrict` qualifier, the compiler must assume they could
overlap — that `input[i]` might be the same memory as `state->acc`.  Under that
assumption every iteration must observe the previous one's store before it can
load `input[i]`, serialising the entire loop regardless of flags.

### 7.5 The unlock: `JM_RESTRICT`

The patch script (`07_patch_perf.py`) replaces the generated `steps()` with an
explicit SIMD version.  Two things happen simultaneously:

1. `JM_RESTRICT` is added to both parameters — telling the compiler they
   cannot alias.  Now it is free to vectorise or reorder reads.
2. The inner loop is written explicitly using `JM_VEC_F32`, `JM_ADD_F32`,
   `JM_LOAD_F32`, and `JM_HSUM_F32`.

```python
python3 .steps/07_patch_perf.py
```

The replacement in `native/src/acc_f32/acc_f32_core.c`:

```c
#if JM_SIMD_WIDTH_F32 > 1
JM_HOT void
acc_f32_steps(acc_f32_state_t *JM_RESTRICT state,
              const float *JM_RESTRICT input, size_t n)
{
    JM_VEC_F32 vacc = JM_ZERO_F32();
    size_t i = 0;
    for (; i + JM_SIMD_WIDTH_F32 <= n; i += JM_SIMD_WIDTH_F32)
        vacc = JM_ADD_F32(vacc, JM_LOAD_F32(input + i));
    state->acc += JM_HSUM_F32(vacc);
    for (; i < n; i++)
        state->acc += input[i];
}
#else
JM_HOT void
acc_f32_steps(acc_f32_state_t *JM_RESTRICT state,
              const float *JM_RESTRICT input, size_t n)
{
    for (size_t i = 0; i < n; i++)
        state->acc += input[i];
}
#endif
```

### 7.6 Width portability

`JM_SIMD_WIDTH_F32` is set at compile time by `jm_simd.h`:

| ISA       | `JM_SIMD_WIDTH_F32` | `JM_VEC_F32` | `JM_ADD_F32` |
| --------- | ------------------- | ------------ | ------------ |
| AVX-512F  | 16                  | `__m512`     | `_mm512_add_ps` |
| AVX2+FMA  | 8                   | `__m256`     | `_mm256_add_ps` |
| Scalar    | 1                   | `float`      | `+`          |

The same source compiles to the widest available tier with no `#ifdef` in user
code.  On scalar targets `JM_SIMD_WIDTH_F32 == 1` so `i + 1 <= n` is always
true in the vector loop — it degenerates to a single-element loop identical to
the `#else` branch, and `JM_HSUM_F32` is a no-op identity.

The `#if / #else / #endif` guard is a safety net: on scalar targets the
`#else` branch compiles instead, keeping the generated `.so` valid even on a
machine with no SIMD support.

### 7.7 `AccCf64` and complex SIMD

The `AccCf64` benchmarks show no improvement because the same aliasing problem
applies to `acc_cf64_steps()` and the patch only covers `acc_f32`.  Adding
`JM_RESTRICT` there follows the same pattern.  Explicit SIMD for `double
_Complex` is more involved: the storage is two consecutive doubles (real then
imaginary), so you need `JM_VEC_F64` with stride-2 access or interleaved
accumulation — left as an exercise once the `AccF32` workflow is understood.

---

## See also

- [Filter module example](filter_module.md) — two types sharing a module,
    focused on the module-grouping mechanics
- [Extend commands — `jm method`](../commands/extend.md)
- [Template gallery — processor](../templates/processor.md)
