# running_stats example

Welford's online algorithm — streaming mean and variance over any sequence of
real-valued samples.  Useful anywhere you need live statistics without storing
the full dataset: monitoring, data pipelines, scientific computing, control loops.

Follow along to scaffold, implement, build, and use it yourself.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example running_stats
# running_stats: PASSED
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

## 1. Scaffold

```sh
just-makeit new my_stats \
    --object running_stats \
    --state "n:int32_t:0" \
    --state "mean:double:0.0" \
    --state "m2:double:0.0"
```

Three state variables — all zero by default, so `RunningStats()` needs no arguments:

| Name   | Type      | Role                                |
| ------ | --------- | ----------------------------------- |
| `n`    | `int32_t` | Sample count                        |
| `mean` | `double`  | Running mean (Welford)              |
| `m2`   | `double`  | Sum of squared deviations (Welford) |

Variance = `m2 / (n - 1)` once `n > 1`.

---

## 2. Implement

Open `native/inc/running_stats/running_stats_core.h` and replace the stub.
The algorithm mutates state, so the signature changes from `const` to mutable.
The real part of the input is the sample value; the return packs `mean` into
the real part and sample variance into the imaginary part:

```c
// before
static inline float _Complex running_stats_step (
    const running_stats_state_t *state, float _Complex x)
{
  (void)state; /* TODO: implement using state variables */
  return x;
}
```

```c
// base — Welford's online algorithm (mean + variance only)
// Input:  real part = new sample (imaginary part ignored)
// Output: real = current mean, imag = sample variance (0 until n > 1)
static inline float _Complex running_stats_step (running_stats_state_t *state,
                                                 float _Complex x)
{
  double sample = (double)crealf (x);
  state->n++;
  double delta = sample - state->mean;
  state->mean += delta / (double)state->n;
  double delta2 = sample - state->mean;
  state->m2 += delta * delta2;
  double var = (state->n > 0) ? state->m2 / (double)state->n : 0.0;
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

print(f"n:        {s.get_n()}")  # 8
print(f"mean:     {s.get_mean():.4f}")  # 5.0000
print(f"variance: {y.imag:.4f}")  # 4.0000  (packed into imag of last step)

# reset and try a single-pass block via steps()
s.reset()
y_all = s.steps(data)
print(f"final mean from steps(): {y_all[-1].real:.4f}")  # 5.0000
print(f"final var  from steps(): {y_all[-1].imag:.4f}")  # 4.0000
```

---

## 5. Try it from C

After `make`, the combined shared library is at `build/libmy_stats.so`.

```c
// demo.c
#include "running_stats/running_stats_core.h"
#include <complex.h>
#include <stdio.h>

int
main (void)
{
  running_stats_state_t *s = running_stats_create (0, 0.0, 0.0);

  float data[] = { 2, 4, 4, 4, 5, 5, 7, 9 };
  float _Complex y;
  for (int i = 0; i < 8; i++)
    y = running_stats_step (s, data[i] + 0.0f * I);

  printf ("n:        %d\n", running_stats_get_n (s));      /* 8     */
  printf ("mean:     %.4f\n", running_stats_get_mean (s)); /* 5.0000 */
  printf ("variance: %.4f\n", (double)cimagf (y));         /* 4.0000 */

  running_stats_reset (s);
  printf ("after reset: n=%d mean=%.1f\n", running_stats_get_n (s),
          running_stats_get_mean (s)); /* n=0 mean=0.0 */

  running_stats_destroy (s);
  return 0;
}
```

```sh
gcc -O2 -std=c99 -Inative/inc demo.c \
    -Lbuild -lmy_stats -Wl,-rpath,build \
    -lm -o demo && ./demo
```

---

## 6. Add more state

Track the min and max alongside the running statistics:

```sh
just-makeit add --state "min_val:double:0.0" --state "max_val:double:0.0"
make test
```

State is *structural*: `add` rewrites the `running_stats_state_t` struct and
the `create()` / `reset()` lifecycle, so it rebuilds the object from the
manifest rather than splicing into your sources. That rebuild resets
`running_stats_step()` back to a fresh stub, so re-run the implement step to
restore the algorithm — now on top of the new `min_val` / `max_val` fields:

```c
// after — Welford's online algorithm + running min/max
// Input:  real part = new sample (imaginary part ignored)
// Output: real = current mean, imag = sample variance (0 until n > 1)
// State:  min_val / max_val track the smallest / largest sample seen so far.
static inline float _Complex running_stats_step (running_stats_state_t *state,
                                                 float _Complex x)
{
  double sample = (double)crealf (x);
  state->n++;
  double delta = sample - state->mean;
  state->mean += delta / (double)state->n;
  double delta2 = sample - state->mean;
  state->m2 += delta * delta2;
  if (state->n == 1 || sample < state->min_val)
    state->min_val = sample;
  if (state->n == 1 || sample > state->max_val)
    state->max_val = sample;
  double var = (state->n > 0) ? state->m2 / (double)state->n : 0.0;
  return (float)state->mean + (float)var * I;
}
```

---

## 7. Give the Python class a real docstring

The header is the single source of truth for docs, so replacing the scaffold's
boilerplate `@brief` on `running_stats_create()` with a one-line description
turns the generated `.pyi` class summary from the generic
`"RunningStats component."` into a sentence that says what the object does:

```python
"""Enrich the sacred ``running_stats_core.h`` header with a real Doxygen
``@brief`` so the generated ``.pyi`` class docstring reads as a sentence.

The header is the single source of truth for documentation: ``jm`` parses the
``/** ... */`` comment on ``running_stats_create()`` and turns its ``@brief``
into the summary line of the Python class docstring. Straight off the scaffold
that summary is the generic ``"RunningStats component."``; replacing the
boilerplate ``@brief`` with a one-line description of what the object *does*
gives the class a proper summary. A follow-up ``jm apply`` re-derives the
``.pyi`` from the edited header.

This is a *light* enrichment: ``running_stats`` exposes only auto-generated
state accessors (``get_mean`` / ``get_min_val`` / ...), no hand-written named
method, so there is nothing to hang a runnable ``@code`` doctest on — the class
summary is the whole win.

Usage:  python3 .steps/07_doxygen.py     # run from the project root
"""

from __future__ import annotations

import pathlib
import re
import sys

OBJ = "running_stats"
# One-line summary of the object, dropped in as create()'s @brief. jm lifts it
# verbatim into the `.pyi` class docstring's summary line.
CREATE_BRIEF = (
    "Streaming mean, variance, and running min/max via Welford's "
    "online algorithm."
)


def _enrich() -> None:
    header = pathlib.Path("native/inc") / OBJ / f"{OBJ}_core.h"
    text = header.read_text(encoding="utf-8")

    # Replace jm's trivial scaffold brief on <obj>_create with a real one. The
    # scaffold block carries boilerplate @param/@return/@note; collapsing it to
    # a bare @brief keeps the enrichment to the class summary alone (the
    # Parameters section of the .pyi still derives from the state fields).
    scaffold_re = re.compile(
        rf"/\*\*\n \* @brief Create a {OBJ} instance\..*?"
        rf"(?={OBJ}_state_t \*{OBJ}_create)",
        re.DOTALL,
    )
    new_create = f"/**\n * @brief {CREATE_BRIEF}\n */\n"
    text, n = scaffold_re.subn(new_create, text, count=1)
    if n != 1:
        print(f"ERROR: {OBJ}_create scaffold brief not found", file=sys.stderr)
        sys.exit(1)

    header.write_text(text, encoding="utf-8")
    print(f"enriched {header}")


if __name__ == "__main__":
    _enrich()
```

Run it, then `jm apply` re-derives the `.pyi` from the edited header.
