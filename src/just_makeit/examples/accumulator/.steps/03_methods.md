## 3. Add named methods

```{03_methods.sh}
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
