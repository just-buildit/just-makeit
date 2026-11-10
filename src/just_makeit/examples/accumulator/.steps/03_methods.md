## 3. Add named methods

```{03_methods.sh}
```

Five extra methods per type, plus one more on `AccCf64` — eleven commands
total.  Why can't these be `step`?

| Method    | Why it's not `step`                                               |
| --------- | ----------------------------------------------------------------- |
| `get`     | Read-only peek at the accumulator; no input arg, different return |
| `dump`    | Atomic read-then-zero: `v = acc; acc = 0; return v`              |
| `madd`    | Weighted accumulate: `acc += dot(x, h)` — two array inputs        |
| `add2d`   | Flat accumulate of an input array (step applied row-by-row)       |
| `madd2d`  | Weighted flat accumulate: `madd` applied to each row              |
| `add_int` | `AccCf64` only: add a whole number `k` — a scalar `int32_t` input |

`dump` is the interesting one.  It returns the current total *and* zeroes the
accumulator in one atomic C call.  There is no way to express "return current
value and mutate state" with just `step()` semantics.

All array-input methods use `--arg-type void` with `--param "name:type[]"`.
Each array param expands to two C arguments — a `const elem_t *name` pointer
and a `size_t name_len` length — and a matching NumPy buffer acquisition in
the Python glue.

`add_int` takes a scalar param, `--param "k:int32_t"`, and becomes one C
argument, `int32_t k`.  Python ints are unbounded, so the generated glue checks
the range before it calls C: any value from `-2**31` to `2**31 - 1` arrives
exactly, and anything outside raises `OverflowError` naming the parameter and
the range — it never wraps.  The message is the same on every platform: the
glue parses through a 64-bit `long long` and states the bound itself, so it
does not depend on how wide the platform's C `long` is (64 bits on Linux and
macOS, 32 on Windows).  A `double` holds every `int32_t` exactly, which is why
`add_int` lives on `AccCf64` and not on the `float` accumulator.

After these eleven commands, the module's per-object binding fragments
`native/src/accumulator/accumulator_ext_acc_f32.c` and
`accumulator_ext_acc_cf64.c` hold `AccF32Object` and `AccCf64Object`, with
fully generated Python argument parsing and NumPy buffer protocol for all
array parameters. `accumulator_ext.c` `#include`s both and registers the two
types in its one `PyInit_accumulator`.
