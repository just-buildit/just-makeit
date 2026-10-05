## 3. Add properties

```{03_properties.sh}
```

Three properties across the two types:

| Object      | Property          | Kind      | Type       | Notes                                    |
| ----------- | ----------------- | --------- | ---------- | ---------------------------------------- |
| `Cf32ToQ15` | `samples_written` | `--field` | `uint32_t` | incremented by `steps()`                 |
| `Q15ToCf32` | `samples_read`    | `--field` | `uint32_t` | incremented by `steps()`                 |
| `Q15ToCf32` | `eof`             | computed  | `int32_t`  | implement by comparing `lseek` positions |

**Field-backed** (`--field`): adds `uint32_t samples_written;` to the state
struct and auto-implements the getter as `return state->samples_written` — no
`<<IMPLEMENT>>` stub needed. That getter has no header declaration to carry
a Doxygen `@brief`, so `--doc` is where its docstring lives (see step 4).

**Computed** (`eof`, no `--field`): getter stub calls `iqfile_q15_to_cf32_get_eof()`
which you implement — returning 1 when the file position is at end of file
(or `fd < 0`).
