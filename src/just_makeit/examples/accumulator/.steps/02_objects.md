## 2. Add the accumulator types

```{02_objects.sh}
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
