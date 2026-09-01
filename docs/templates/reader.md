# `jm object NAME --preset reader` — reader (external source → output)

A **reader** opens an external source — file, socket, pipe, mmap'd
region — and yields data on demand. Unlike a generator (which
produces from internal state), a reader has a side input it must
acquire, position within, and release.

Concrete examples: a binary file reader for a custom format, a CSV
row reader, a WAV / PNG / Parquet loader, a TCP socket consumer, a
mmap'd shared-memory channel reader, or any source where the data
lives outside the process.

`--preset reader` bundles `--no-step` (the standard `step()` interface
doesn't fit I/O) with a `filepath:const char *` init-param. The scaffold
builds and tests green. Add the `read()` / `seek()` / `close()` methods
yourself with `jm method`; this preset formalises the asymmetry
init-params solve — `init_params` for the user-facing ctor (`filepath`),
`state` for internal bookkeeping (`fd`, `position`).

## Command

```sh
jm object NAME --preset reader \
    --init-param header_bytes:size_t:0 \
    --state fd:int:-1 \
    --state file_size:size_t:0 \
    --state position:size_t:0
```

The `filepath:const char *` init-param comes from the preset; the rest
are yours. Then add the I/O verbs:

```sh
jm method NAME read --param n:size_t --out-type "float _Complex"
jm method NAME seek --param sample_index:size_t --return-type int
jm method NAME close --return-type void
```

`jm method` defaults `--return-type` to `float _Complex` when omitted, so
`close` needs an explicit `--return-type void` to get a real `void` C
signature — without it you'd get a `close()` that returns (and discards)
a bogus complex value.

## What you get

### `native/inc/NAME/NAME_core.h`

```c
typedef struct {
    int      fd;
    size_t   file_size;
    size_t   position;
} NAME_state_t;

/* Ctor takes init_params; state stays internal. */
NAME_state_t *NAME_create(const char *filepath, size_t header_bytes);
void          NAME_destroy(NAME_state_t *state);
void          NAME_reset(NAME_state_t *state);

float _Complex NAME_read(NAME_state_t *state, size_t n, float _Complex *out);
int           NAME_seek(NAME_state_t *state, size_t sample_index);
void          NAME_close(NAME_state_t *state);
```

`read()`'s C return type follows `jm method`'s default (`float _Complex`,
since the command above doesn't pass `--return-type`) even though the
Python binding ignores it — the sample count comes from `n`, and the data
goes into `out`. Pass `--return-type size_t` if you want the C-level
return value to mean something (e.g. "samples actually read"); the
generated Python wrapper's behavior doesn't change either way.

### `native/src/NAME/NAME_core.c`

```c
NAME_state_t *
NAME_create(const char *filepath, size_t header_bytes)
{
    NAME_state_t *obj = calloc(1, sizeof(*obj));
    if (!obj)
        return NULL;
    obj->fd = -1;
    obj->file_size = 0;
    obj->position = 0;
    return obj;
}

void
NAME_destroy(NAME_state_t *state)
{
    free(state);
}

/* <<IMPLEMENT: read >> */
float _Complex
NAME_read(NAME_state_t *state, size_t n, float _Complex *out)
{
    (void)state; (void)n; (void)out;
    return (float _Complex)0.0f + 0.0f * I;
}

/* <<IMPLEMENT: seek >> */
int
NAME_seek(NAME_state_t *state, size_t sample_index)
{
    (void)state; (void)sample_index;
    return (int)0;
}

/* <<IMPLEMENT: close >> */
void
NAME_close(NAME_state_t *state)
{
    (void)state;
}
```

`filepath` and `header_bytes` are constructor init-params, not state —
they never reach `NAME_create()`'s body automatically (init-params carry
no auto-assignment the way `--state` does). You open the file, seed
`file_size`/`position`, and wire `fd` yourself.

## What you fill in

The `open()`/`read()`/`seek()`/`close()` logic against `state->fd`.
A typical fill-in:

```c
NAME_state_t *
NAME_create(const char *filepath, size_t header_bytes)
{
    NAME_state_t *obj = calloc(1, sizeof(*obj));
    if (!obj) return NULL;

    obj->fd = open(filepath, O_RDONLY);
    if (obj->fd < 0) { free(obj); return NULL; }

    struct stat st;
    if (fstat(obj->fd, &st) < 0) { close(obj->fd); free(obj); return NULL; }
    obj->file_size = (size_t)st.st_size;
    obj->position  = header_bytes;
    return obj;
}

void
NAME_destroy(NAME_state_t *state)
{
    if (!state) return;
    if (state->fd >= 0) close(state->fd);
    free(state);
}

float _Complex
NAME_read(NAME_state_t *state, size_t n, float _Complex *out)
{
    ssize_t bytes = read(state->fd, out, n * sizeof(*out));
    if (bytes > 0) state->position += (size_t)bytes;
    return 0;
}

int
NAME_seek(NAME_state_t *state, size_t sample_index)
{
    off_t off = (off_t)(sample_index * sizeof(float _Complex));
    if (lseek(state->fd, off, SEEK_SET) == (off_t)-1) return -1;
    state->position = (size_t)off;
    return 0;
}

void
NAME_close(NAME_state_t *state)
{
    if (state->fd >= 0) { close(state->fd); state->fd = -1; }
}
```

Other common shapes:

- Wire-format demultiplexing (separate I and Q from interleaved bytes).
- Header parsing (use `header_bytes` to skip a file header).
- Type conversion (read int16 from disk, return float \_Complex).
- Endianness swap on read.

## Python usage

```python
from <pkg> import NAME

rdr = NAME(filepath="capture.iq", header_bytes=0)
chunk = rdr.read(4096)        # → (4096,) complex64
rdr.seek(0)
rdr.close()
```

## Concrete types

| Slot                                      | Accepts                                                                                                                                | Rejects                                                                                          | Default                                            |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | -------------------------------------------------- |
| `--init-param name:T:D`                   | Path/filename strings use `const char *`. Any [scalar](../types.md#constructor-init-param-types), `T[]`, `T[][]`, `string_enum:a,b,c`. | `T[N]` (fixed length — that's `--state` territory).                                              | `filepath:"const char *", header_bytes:size_t:0`   |
| `--state field:T:D`                       | Any [scalar](../types.md#state-variable-types). The file descriptor pattern is `fd:int:-1`.                                            | `const char *` (use an `--init-param` to receive the path, then store the parsed `fd` / `size`). | `fd:int:-1, file_size:size_t:0, position:size_t:0` |
| Method return / output (`out_type = "T"`) | Any [array element type](../types.md#array-element-types). `read()` returns a `T[]` ndarray sized from the requested sample count.     | `bool`, `int`, `const char *`, `long double _Complex`.                                           | `float _Complex`                                   |

`const char *` is the load-bearing type here. It is a valid init-param
(PyArg parses the Python str; lifetime is managed on the Python side)
but is **not** a valid state field — persist the parsed result (the
`fd`, a `size_t`) instead. This is the asymmetry the reader preset
exists to formalise.
