# `jm object NAME --reader` — reader (file / socket, custom verbs)

**Status: proposed.** Tracked in
[`developers/wizard-design.md`](../developers/wizard-design.md). The
`--reader` flag would bundle:

- `--no-step` (the standard `step()` interface doesn't fit I/O)
- `--init-param filepath:"const char *"`
- Custom methods `read()`, `seek()`, `close()` registered upfront
- A `_core.c` skeleton with `open()` / `mmap()` already wired

This preset is the direct fix for the workflow gh-69 was trying to
express via TOML — `init_params` for the user-facing ctor, `state` for
internal bookkeeping.

## Command

```sh
jm object NAME --reader \
    --init-param filepath:"const char *" \
    --init-param header_bytes:size_t:0 \
    --state fd:int:-1 \
    --state file_size:size_t:0 \
    --state position:size_t:0
```

## What you get

### `native/inc/NAME/NAME_core.h` (proposed)

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

### `native/src/NAME/NAME_core.c` (proposed)

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

| Slot                        | Accepts                                                                                                                                                                                         | Default in this template                           |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| `--init-param name:T:D`     | Path/filename strings use `const char *`. Numeric options accept any [scalar](../types.md#constructor--init-param-types). `string_enum:read,write,rw` is the canonical pattern for a mode flag. | `filepath:"const char *", header_bytes:size_t:0`   |
| `--state field:T:D`         | Internal bookkeeping. Any [scalar](../types.md#state-variable-types). The file descriptor pattern is `fd:int:-1`.                                                                               | `fd:int:-1, file_size:size_t:0, position:size_t:0` |
| Method return / output type | The `read()` verb returns a `T[]` ndarray sized via `out_type`; the element type follows the [array element table](../types.md#array-element-types).                                            | `out_type = "float _Complex"`                      |

`const char *` is the load-bearing type here: it's a valid
[init-param](../types.md#constructor--init-param-types) (parsed by
PyArg as a Python str → C string lifetime managed by Python) but is
**not** a valid state field. Persist the parsed result (the `fd`, a
copy of the buffer) instead.
