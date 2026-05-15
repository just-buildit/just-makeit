## 3. Add properties

```{03_properties.sh}
```

Three properties across the two types:

| Object      | Property          | Kind      | Type       | Notes                               |
| ----------- | ----------------- | --------- | ---------- | ----------------------------------- |
| `Cf32ToQ15` | `samples_written` | `--field` | `uint32_t` | incremented by `step()`             |
| `Q15ToCf32` | `samples_read`    | `--field` | `uint32_t` | incremented by `step()`             |
| `Q15ToCf32` | `eof`             | computed  | `int32_t`  | implement via `read()` return value |

**Field-backed** (`--field`): adds `uint32_t samples_written;` to the state
struct and auto-implements the getter as `return state->samples_written` — no
`<<IMPLEMENT>>` stub needed.

**Computed** (`eof`, no `--field`): getter stub calls `q15_to_cf32_get_eof()`
which you implement — returning 1 when the last `read()` returned 0 bytes.
