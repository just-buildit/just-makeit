## 1. Scaffold a streamable blockwise producer

```{01_scaffold.sh}
```

The flags that matter:

| Flag                  | Effect                                                                                               |
| --------------------- | ---------------------------------------------------------------------------------------------------- |
| `--variable-output`   | Add a `run(n) -> array` method whose output length is decided at call time (the blockwise producer). |
| `--streamable`        | Generate `stream()` / `__iter__`. With a `variable_output` method present, it drives that method.    |
| `--mutable`           | `run()` advances `pos` in place as the source drains.                                                |
| `--state total:int32_t:20` | Total samples this source will ever emit.                                                       |
| `--state pos:int32_t:0`    | How many have been emitted so far.                                                              |

`--variable-output` makes the object a generator: a pre-allocated output buffer
is sized once at `__init__`, and each `run(n)` returns a **zero-copy view** into
it. `--streamable` notices the `variable_output` method and picks it as the
stream producer (it wins over the built-in `steps`), so `stream()` calls `run`
block by block and stops the moment it returns an empty block.
