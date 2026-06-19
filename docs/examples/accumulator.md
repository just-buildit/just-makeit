# accumulator example

A two-type accumulator module — `AccF32` (real float) and `AccCf64` (complex
double) — each with five methods. The most comprehensive scaffold-and-implement
example in the gallery.

## TL;DR — see it work first

```sh
just-makeit example accumulator
# accumulator: all checks passed
```

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

- A **module subpackage** grouping two types in one `.so`
- Objects with **different scalar types** sharing a module (`float` and
    `double _Complex`)
- Adding **five methods per object** with `jm method`
- Methods with scalar-return and array-input shapes
- The full edit-implement-test-iterate cycle on a multi-type project

______________________________________________________________________

## 1. Scaffold

```sh
just-makeit new my_acc
cd my_acc
just-makeit module accumulator
```

`just-makeit new` with no `--object` creates the project skeleton only.
`just-makeit module accumulator` adds an empty module slot that the two types
will share.

Add the two types:

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

Both types carry a single state field `acc` and both use `--mutable` because the
accumulator step writes to state. `step(x) -> void` with `--mutable` is exactly a
push operation, and the auto-generated `steps()` batch loop gives you add for
free.

Add five methods to `acc_f32`:

```sh
# Read the running total (no input arg)
just-makeit method acc_f32 get \
    --module accumulator \
    --arg-type void \
    --return-type float

# Atomic read-then-zero: v = acc; acc = 0; return v
just-makeit method acc_f32 dump \
    --module accumulator \
    --arg-type void \
    --return-type float

# Multiply-accumulate two arrays: acc += dot(x, h)
just-makeit method acc_f32 madd \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:float[]" \
    --param "h:float[]"

# Flat-accumulate an input array
just-makeit method acc_f32 add2d \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:float[]"

# Weighted flat-accumulate
just-makeit method acc_f32 madd2d \
    --module accumulator \
    --arg-type void \
    --return-type void \
    --param "x:float[]" \
    --param "h:float[]"
```

Repeat the same five methods on `acc_cf64`, substituting `double _Complex` for
the return type (`get`/`dump`) and for the `x` array element type (`madd`,
`add2d`, `madd2d`); the `h` weight array stays `float[]`. All array-input
methods use `--arg-type void` with `--param "name:type[]"` — each array param
expands to a `const elem_t *name` pointer plus a `size_t name_len` length, with
a matching NumPy buffer acquisition in the Python glue.

______________________________________________________________________

## 2. Implement

The `step()` lives in the header and is one line — the same pattern for both
types, differing only in the C type. Open
`native/inc/acc_f32/acc_f32_core.h` and replace the stub:

```c
static inline void
acc_f32_step(acc_f32_state_t *state, float x)
{
    state->acc += x;
}
```

The five named method bodies go in `native/src/acc_f32/acc_f32_core.c`. `get`
reads the accumulator; `dump` captures, zeroes, then returns the captured value:

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

`acc_cf64` follows the identical shape with `double complex`; in its `madd` and
`madd2d` the real `h[i]` weight is widened with a `(double)h[i]` cast before the
multiply. `steps()` is already done — jm generates the batch loop in `_core.c`
that calls `step()` for each element.

______________________________________________________________________

## 3. Build and test

```sh
make
make test
```

Both CTest (C lifecycle smoke test) and pytest (Python integration tests) run.

______________________________________________________________________

## 4. Use from Python

```sh
pip install -e .
```

```python
import numpy as np
from my_acc.accumulator import AccF32, AccCf64

# Real accumulator — step == push
f = AccF32()
f.step(np.float32(1.0))
f.step(np.float32(2.0))
f.step(np.float32(3.0))
print(f.get())      # 6.0  (sum of [1, 2, 3])

# Batch processing — steps == add
f.reset()
f.steps(np.ones(100, dtype=np.float32))
print(f.get())      # 100.0

# dump: atomic get + reset
f.reset()
f.step(np.float32(42.0))
print(f.dump())     # 42.0
print(f.get())      # 0.0

# Complex accumulator
c = AccCf64()
c.step(1 + 2j)
c.step(3 + 4j)
print(c.get())      # (4+6j)
```

______________________________________________________________________

## Key concepts

**Multi-type modules.** `jm module` creates the subpackage shell; `jm object --module` adds each type. All types in the module share one `.so` and one
`CMakeLists.txt`, but each has its own sacred `_core.h` and `_core.c` — fully
independent implementations.

**Five methods, one verb.** Each `jm method` call appends a fresh stub to
`_core.c` and injects a declaration into `_core.h`. Existing bodies are never
touched — the verb is additive and splice-free.

**`dump` needs its own verb.** It returns the current total *and* zeroes the
accumulator in one atomic C call. There is no way to express "return current
value and mutate state" with just `step()` semantics — hence a named method.

**Different types, same module shape.** `AccF32` (real) and `AccCf64` (complex)
differ only in `--arg-type`, `--return-type`, and the array element type of
their params, but both live in the same module extension with no extra wiring.

## See also

- [Filter module example](filter_module.md) — two types sharing a module,
    focused on the module-grouping mechanics
- [Extend commands — `jm method`](../commands/extend.md)
- [Template gallery — processor](../templates/processor.md)
