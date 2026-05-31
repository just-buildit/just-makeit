# accumulator example

A two-type accumulator module — `AccF32` (real float) and `AccCf64` (complex
double) — each with five methods. The most comprehensive scaffold-and-implement
example in the gallery.

## TL;DR — see it work first

```sh
just-makeit example accumulator
# accumulator: PASSED
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
- Methods with scalar, array-in, and array-out shapes
- The full edit-implement-test-iterate cycle on a multi-type project

______________________________________________________________________

## 1. Scaffold

```sh
just-makeit new my_acc --module accumulator
cd my_acc
```

Add the two types:

```sh
just-makeit object acc_f32 --module accumulator \
    --arg-type float --return-type float \
    --state "sum:float:0.0" \
    --state "count:uint32_t:0"

just-makeit object acc_cf64 --module accumulator \
    --arg-type "double _Complex" --return-type "double _Complex" \
    --mutable \
    --state "sum:double _Complex:0.0" \
    --state "count:uint32_t:0"
```

Add five methods to `acc_f32`:

```sh
# Get the running mean
just-makeit method acc_f32 get --module accumulator \
    --arg-type void --return-type float

# Dump the running sum into a float output array
just-makeit method acc_f32 dump --module accumulator \
    --arg-type void --return-type float --variable-output

# Multiply-accumulate: scale each sample before adding
just-makeit method acc_f32 madd --module accumulator \
    --arg-type float --return-type float \
    --param "scale:float"

# Accumulate a 2-D float array (batch of rows)
just-makeit method acc_f32 add2d --module accumulator \
    --param "rows:float[]" --return-type void

# Multiply-accumulate over a 2-D array
just-makeit method acc_f32 madd2d --module accumulator \
    --param "rows:float[]" --param "scales:float[]" --return-type float
```

Repeat the same five methods on `acc_cf64` (substituting `double _Complex`
types). `acc_cf64` uses `--mutable` because the accumulator step updates state.

______________________________________________________________________

## 2. Implement

Open `native/inc/accumulator/acc_f32_core.h` and fill in each stub. The
`step()` and all five methods need bodies. For example:

```c
/* step: accumulate one sample */
static inline float
acc_f32_step(const acc_f32_state_t *state, float x)
{
    /* cast away const — this is a mutable accumulator */
    acc_f32_state_t *s = (acc_f32_state_t *)state;
    s->sum += x;
    s->count++;
    return x;
}
```

```c
/* get: return running mean */
float
acc_f32_get(const acc_f32_state_t *state)
{
    if (state->count == 0) return 0.0f;
    return state->sum / (float)state->count;
}
```

______________________________________________________________________

## 3. Build and test

```sh
make && make test
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

# Real accumulator
a = AccF32()
for x in [1.0, 2.0, 3.0]:
    a.step(x)
print(a.get())      # 2.0  (mean of [1, 2, 3])

# Batch processing
signal = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
b = AccF32()
b.steps(signal)
print(b.get())      # 2.5

# Complex accumulator
c = AccCf64()
c.step(1.0 + 2.0j)
c.step(3.0 + 4.0j)
print(c.get_sum())  # (4+6j)
```

______________________________________________________________________

## Key concepts

**Multi-type modules.** `jm module` creates the subpackage shell; `jm object --module` adds each type. All types in the module share one `.so` and one
`CMakeLists.txt`, but each has its own sacred `_core.h` and `_core.c` — fully
independent implementations.

**Five methods, one verb.** Each `jm method` call appends a fresh stub to
`_core.c` and injects a declaration into `_core.h`. Existing bodies are never
touched — the verb is additive and splice-free.

**Different types, same module shape.** `AccF32` (const step, real) and
`AccCf64` (mutable step, complex) differ in `--arg-type`, `--return-type`, and
`--mutable`, but both live in the same module extension with no extra wiring.

## See also

- [Filter module example](filter_module.md) — two types sharing a module,
    focused on the module-grouping mechanics
- [Extend commands — `jm method`](../commands/extend.md)
- [Template gallery — processor](../templates/processor.md)
