# running_stats example

Welford's online algorithm — streaming mean and variance over any sequence of
real-valued samples.  Useful anywhere you need live statistics without storing
the full dataset: monitoring, data pipelines, scientific computing, control loops.

Follow along to scaffold, implement, build, and use it yourself.

---

## 1. Scaffold

```sh
just-makeit new my_stats \
    --component running_stats \
    --state "n:int32_t:0" \
    --state "mean:double:0.0" \
    --state "m2:double:0.0"
```

Three state variables — all zero by default, so `RunningStats()` needs no arguments:

| Name   | Type       | Role                              |
|--------|------------|-----------------------------------|
| `n`    | `int32_t`  | Sample count                      |
| `mean` | `double`   | Running mean (Welford)            |
| `m2`   | `double`   | Sum of squared deviations (Welford) |

Variance = `m2 / (n - 1)` once `n > 1`.

---

## 2. Implement

Open `native/inc/running_stats/running_stats_core.h` and replace the stub.
The algorithm mutates state, so the signature changes from `const` to mutable.
The real part of the input is the sample value; the return packs `mean` into
the real part and sample variance into the imaginary part:

```c
// before
static inline float complex
running_stats_step(const running_stats_state_t *state, float complex x)
{
    (void)state; /* TODO: implement using state variables */
    return x;
}
```

```c
// after — Welford's online algorithm
// Input:  real part = new sample (imaginary part ignored)
// Output: real = current mean, imag = sample variance (0 until n > 1)
static inline float complex
running_stats_step(running_stats_state_t *state, float complex x)
{
    double sample = creal(x);
    state->n++;
    double delta  = sample - state->mean;
    state->mean  += delta / (double)state->n;
    double delta2 = sample - state->mean;
    state->m2    += delta * delta2;
    double var = (state->n > 1) ? state->m2 / (double)(state->n - 1) : 0.0;
    return (float)state->mean + (float)var * I;
}
```

---

## 3. Build and test

```sh
make
make test
```

---

## 4. Try it from Python

```sh
pip install -e .
```

```python
import numpy as np
from my_stats import RunningStats

# All defaults are 0 — no arguments needed
s = RunningStats()

# Classic Welford test dataset: mean=5, variance=4
data = np.array([2, 4, 4, 4, 5, 5, 7, 9], dtype=np.complex64)
for x in data:
    y = s.step(x)

print(f"n:        {s.get_n()}")          # 8
print(f"mean:     {s.get_mean():.4f}")   # 5.0000
print(f"variance: {y.imag:.4f}")         # 4.0000  (packed into imag of last step)

# reset and try a single-pass block via steps()
s.reset()
y_all = s.steps(data)
print(f"final mean from steps(): {y_all[-1].real:.4f}")   # 5.0000
print(f"final var  from steps(): {y_all[-1].imag:.4f}")   # 4.0000
```

---

## 5. Try it from C

After `make`, the static library is at
`build/native/src/running_stats/librunning_stats_core.a`.

```c
// demo.c
#include "running_stats/running_stats_core.h"
#include <complex.h>
#include <stdio.h>

int main(void) {
    running_stats_state_t *s = running_stats_create(0, 0.0, 0.0);

    double data[] = {2, 4, 4, 4, 5, 5, 7, 9};
    float complex y;
    for (int i = 0; i < 8; i++)
        y = running_stats_step(s, (float)data[i] + 0.0f * I);

    printf("n:        %d\n",   running_stats_get_n(s));       /* 8     */
    printf("mean:     %.4f\n", running_stats_get_mean(s));    /* 5.0000 */
    printf("variance: %.4f\n", (double)cimagf(y));            /* 4.0000 */

    running_stats_reset(s);
    printf("after reset: n=%d mean=%.1f\n",
           running_stats_get_n(s), running_stats_get_mean(s)); /* n=0 mean=0.0 */

    running_stats_destroy(s);
    return 0;
}
```

```sh
gcc -O2 -std=c99 -Inative/inc demo.c \
    build/native/src/running_stats/librunning_stats_core.a \
    -lm -o demo && ./demo
```

---

## 6. Add more state

Track the min and max alongside the running statistics:

```sh
just-makeit add --state "min_val:double:0.0" --state "max_val:double:0.0"
make test
```

`add` regenerates only the state-sensitive files — your `running_stats_step`
implementation is untouched.
