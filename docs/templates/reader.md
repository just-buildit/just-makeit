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
jm method NAME close
```

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

size_t NAME_read(NAME_state_t *state, float complex *out, size_t n);
int    NAME_seek(NAME_state_t *state, size_t sample_index);
void   NAME_close(NAME_state_t *state);
```

### `native/src/NAME/NAME_core.c`

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

size_t
NAME_read(NAME_state_t *state, float complex *out, size_t n)
{
    /* TODO: read up to n complex samples from state->fd into out[].
       Return the number actually read. The default body does a raw
       read() of n * sizeof(float complex) bytes. */
    ssize_t bytes = read(state->fd, out, n * sizeof(*out));
    if (bytes <= 0) return 0;
    state->position += (size_t)bytes;
    return (size_t)bytes / sizeof(*out);
}

int
NAME_seek(NAME_state_t *state, size_t sample_index)
{
    /* TODO: translate sample_index to byte offset and lseek. */
    off_t off = (off_t)(sample_index * sizeof(float complex));
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

## What you fill in

Replace the raw-read default with your format. Common shapes:

- Wire-format demultiplexing (separate I and Q from interleaved bytes).
- Header parsing (use `header_bytes` to skip a file header).
- Type conversion (read int16 from disk, return float complex).
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
