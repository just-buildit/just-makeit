# gain example

A scalar-gain component — the simplest possible just-makeit project.
Follow along to scaffold, implement, build, and use it yourself.

---

## 1. Scaffold

```sh
just-makeit new my_gain \
    --component gain \
    --state gain:double:1.0
cd my_gain
```

You now have a complete project skeleton: C library, Python extension, CMake
build, CTest, and pytest — all wired together.

---

## 2. Implement

Open `native/inc/gain/gain_core.h` and replace the `gain_step` stub:

```c
// before
static inline float complex
gain_step(const gain_state_t *state, float complex x)
{
    (void)state; /* TODO: implement DSP using state variables */
    return x;
}

// after
static inline float complex
gain_step(const gain_state_t *state, float complex x)
{
    return (float complex)state->gain * x;
}
```

That's it — one line of DSP, everything else is generated.

---

## 3. Build and test

```sh
make           # CMake configure + build
make test      # CTest (C) + pytest (Python)
```

Expected output:

```
test_gain_core PASSED
...
8 passed in 0.08s
```

---

## 4. Try it from Python

```sh
pip install -e .
```

```sh
python3 -c "
import numpy as np
from my_gain import Gain

g = Gain(gain=0.5)
x = np.ones(8, dtype=np.complex64)
y = g.steps(x)
print(y)           # [0.5+0.j  0.5+0.j  ...]

g.set_gain(2.0)
print(g.step(1.0 + 1.0j))   # (2+2j)
"
```

---

## 5. Try it from C

After `make`, the static library is at `build/libgain_core.a`.

```c
// demo.c
#include "gain/gain_core.h"
#include <complex.h>
#include <stdio.h>

int main(void) {
    gain_state_t *g = gain_create(0.5);
    float complex y = gain_step(g, 2.0f + 4.0f * I);
    printf("%.1f + %.1fj\n", crealf(y), cimagf(y));  /* 1.0 + 2.0j */
    gain_destroy(g);
    return 0;
}
```

```sh
gcc -O2 -std=c99 -Inative/inc demo.c build/libgain_core.a -o demo && ./demo
```

---

## 6. Add more state

```sh
just-makeit add --state pan:double:0.0
make test
```

`add` regenerates the six state-sensitive files and wires in the new variable —
getters, setters, test cases, Python stub — without touching your implementation.
