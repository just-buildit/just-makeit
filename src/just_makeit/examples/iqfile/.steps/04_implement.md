## 4. Implement the C kernels

```{04_patch_writer.py}
```

```{04_patch_reader.py}
```

### Cf32ToQ15 — step()

The return type is `int32_t`.  Rather than adding a separate output buffer for
the two `int16_t` values, the step packs both into one `int32_t`
(I in the low 16 bits, Q in the high 16 bits):

```{04_step_writer.c}
```

Python unpacks the packed array with `ndarray.view(np.int16)`:

```python
packed = writer.steps(cf32_block)          # dtype int32, shape (N,)
q15    = packed.view(np.int16)             # dtype int16, shape (2N,) — [i0,q0,i1,q1,…]
q15.tofile("samples.q15")
```

Samples are clamped to `[-scale, +scale]` before casting so an overdriven
input never wraps around silently.

### Q15ToCf32 — step()

Reads four bytes (two `int16_t`) from `state->fd` on every call:

```{04_step_reader.c}
```

`fd` is an `int32_t` state variable — pass a POSIX file descriptor at
construction time:

```python
import os
fd     = os.open("samples.q15", os.O_RDONLY)
reader = Q15ToCf32(fd=fd)
block  = reader.steps(1024)    # reads 4 KiB, returns complex64 ndarray
os.close(fd)
```

### Counters and eof

`samples_written` and `samples_read` are **field-backed** properties — the
struct already has `uint32_t samples_written;` — so the patch adds a single
line to each `_steps()` function:

```c
state->samples_written += (uint32_t)n;   /* in cf32_to_q15_steps() */
state->samples_read    += (uint32_t)n;   /* in q15_to_cf32_steps()  */
```

`eof` is a **computed** property.  The patch appends `q15_to_cf32_get_eof()`
to `_core.c`; it uses `lseek` to compare the current and end file positions:

```c
off_t cur = lseek(state->fd, 0, SEEK_CUR);
off_t end = lseek(state->fd, 0, SEEK_END);
lseek(state->fd, cur, SEEK_SET);
return cur == end ? 1 : 0;
```

### Document once, in C

The sacred header is also the single source of truth for **documentation**. A
Doxygen `/** ... */` comment on `<obj>_create()` flows straight into the
generated `conv.pyi` as the Python class summary — document the intent in C,
once, and every surface inherits it:

```c
/**
 * @brief Pack complex float samples into interleaved q15 (int16 I/Q).
 */
cf32_to_q15_state_t *cf32_to_q15_create(float scale);
```

Property docstrings come from **whichever source actually backs the getter**:

| Property                          | Kind         | Doc source                         |
| --------------------------------- | ------------ | ---------------------------------- |
| `samples_written`, `samples_read` | field-backed | manifest `jm property --doc` value |
| `eof`                             | computed     | header `@brief` on the getter      |

A `--field` property's getter is auto-implemented inline — there is *no header
declaration to annotate* — so its docstring lives in the manifest, authored at
`jm property` time (step 3). A **computed** property (`eof`) has a real getter
declaration, so its `@brief` in `q15_to_cf32_core.h` becomes the property
docstring:

```c
/**
 * @brief True (1) once the backing file descriptor is exhausted.
 */
int32_t q15_to_cf32_get_eof(const q15_to_cf32_state_t *state);
```

`jm apply` re-derives the stub, and `conv.pyi` now carries a real summary on
each class plus a documented `eof` property:

```python
@final
class Q15ToCf32:
    """Read interleaved q15 (int16 I/Q) samples as complex float.
    ...
    """
    @property
    def eof(self) -> int:
        """True (1) once the backing file descriptor is exhausted."""
```

This example has no named `jm method` — only `step()`/`steps()` and
properties — and a property getter renders as prose, not as a runnable
`@code` doctest. So iqfile ships rich class summaries and property docs; for a
**runnable** header-authored doctest (`pytest --doctest-glob='*.pyi'`), see the
`accumulator` and `views_module` examples, whose named methods carry `@code`
blocks that execute against the built extension.

The enrichment for both types is scripted — run it after the kernels are
implemented, then re-derive the glue:

```{04b_doxygen.py}
```

```sh
python3 .steps/04b_doxygen.py
just-makeit apply
```
