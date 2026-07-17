## 1. Scaffold an async-streamable source

```{01_scaffold.sh}
```

The only change from the sync [`stream_source`](stream_source.md)
example is `--async-stream` in place of `--streamable`:

| Flag                       | Effect                                                                          |
| -------------------------- | ------------------------------------------------------------------------------- |
| `--arg-type void`          | A source — `step()` takes no input, it generates from state.                    |
| `--return-type float`      | Each sample is a `float`; `steps(n)` returns an `NDArray[np.float32]`.           |
| `--mutable`                | `step()` advances state in place (the ramp moves), so the state pointer is non-`const`. |
| `--async-stream`           | Generate `stream()` / `__iter__` **and** `__aiter__` / `__anext__`. Implies `--streamable`. |
| `--stream-block 256`       | The default block `__iter__` / `__aiter__` pulls when the caller gives none.     |
| `--state value:float:0.0`  | The running output value.                                                       |
| `--state step_inc:float:1.0` | How much `value` advances per sample.                                         |

`--async-stream` adds, on top of the synchronous iterator, a `PyAsyncMethods`
slot (`__aiter__` / `__anext__`) on the `RampStreamIter` type and an
`__aiter__` on the object — all in C. The manifest records one extra key:

```toml
[ramp]
arg_type             = "void"
return_type          = "float"
mutable              = "true"
streamable           = "true"
async_stream         = "true"
stream_block_default = "256"
```
