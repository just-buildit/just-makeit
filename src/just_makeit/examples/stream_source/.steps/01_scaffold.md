## 1. Scaffold a streamable source

```{01_scaffold.sh}
```

The flags that matter:

| Flag                       | Effect                                                                          |
| -------------------------- | ------------------------------------------------------------------------------- |
| `--arg-type void`          | A source — `step()` takes no input, it generates from state.                    |
| `--return-type float`      | Each sample is a `float`; `steps(n)` returns an `NDArray[np.float32]`.           |
| `--mutable`                | `step()` advances state in place (the ramp moves), so the state pointer is non-`const`. |
| `--streamable`             | Generate `stream()` and `__iter__`. For a source, the producer is the built-in `steps`. |
| `--stream-block 256`       | The default block `__iter__` pulls when the caller gives none.                   |
| `--state value:float:0.0`  | The running output value.                                                       |
| `--state step_inc:float:1.0` | How much `value` advances per sample.                                         |

`--streamable` adds a C iterator type (`RampStreamIter`) and a `stream()`
method to the generated extension — nothing else about the object changes.
The manifest records it as a single key:

```toml
[ramp]
arg_type             = "void"
return_type          = "float"
mutable              = "true"
streamable           = "true"
stream_block_default = "256"
```
