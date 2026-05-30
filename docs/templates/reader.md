# `jm object NAME --reader` — reader (external source → output)

## Customization

```sh
--no-step --init-param filepath:"const char *"
# drive via: jm method NAME read  --return-type T --param n:size_t
#           jm method NAME seek  --param offset:int64_t
#           jm method NAME close
```

The reader preset is the [object generalist](index.md) with the auto-`step()`
turned off — the standard sample-by-sample interface doesn't fit I/O.
The state struct, lifecycle (`create` / `destroy` / `reset`), CPython
binding, and tests stay; you drive the component through custom
methods (`read` / `seek` / `close`) registered via `jm method`. The
constructor takes a `filepath` (or socket, or any handle) via
`init_param`; state carries the fd, position, and other internal
bookkeeping.

Concrete examples: a binary file reader for a custom format, a CSV
row reader, a WAV / PNG / Parquet loader, a TCP socket consumer, an
mmap'd shared-memory channel reader, or any source where the data
lives outside the process.

**Status: proposed `--reader` shorthand.** Tracked in
[`developers/wizard-design.md`](../developers/wizard-design.md). The
underlying flag bundle (`--no-step --init-param ...` + a few
`jm method` calls) works today; the named alias and the bundled
custom-method scaffolding haven't shipped yet.

This preset is the direct fix for the workflow gh-69 was trying to
express via TOML — `init_params` for the user-facing ctor, `state` for
internal bookkeeping.

## Command

Works today as a two-step (the `--reader` shorthand is Phase 3a; the
underlying flags do the same thing):

```sh
jm new my_dsp
cd my_dsp
jm object NAME \
    --no-step \
    --init-param 'filepath:const char *' \
    --init-param header_bytes:size_t:0 \
    --state fd:int:-1 \
    --state file_size:size_t:0 \
    --state position:size_t:0
```

Note: `--no-step` isn't accepted on `jm new`'s `--object`, so the
reader preset needs the two-step path until the `--reader` flag ships.

## TOML written

The commands above write the component fragment to `objects/NAME.toml`.
Hand-author the fragment and `jm apply` — both paths produce identical
files.

```toml
# objects/NAME.toml
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "false"
no_state = "false"
no_step = "true"

[[init_params]]
name = "filepath"
type = "const char *"
default = "NULL"

[[init_params]]
name = "header_bytes"
type = "size_t"
default = "0"

[[state]]
name = "fd"
type = "int"
default = "-1"

[[state]]
name = "file_size"
type = "size_t"
default = "0"

[[state]]
name = "position"
type = "size_t"
default = "0"
```

The `--init-param` syntax in shell needs quoting because of the
space in `const char *`. In a hand-authored TOML fragment, no
quoting issue — just write `type = "const char *"`.

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

## Extending the initial command

A component's whole spec lives in the `jm object` command that
creates it — flags compose. Here's the base above plus more
customizations:

Base from the Command section above + two more customizations.
**NEW** lines are the additions; everything unmarked is the base
preset.

=== "Shell"

    ```sh
    jm object NAME \
        --no-step \
        --init-param 'filepath:const char *' \
        --init-param header_bytes:size_t:0 \
        --state fd:int:-1 \
        --state file_size:size_t:0 \
        --state position:size_t:0 \
        --state mmap_base:size_t:0 \                # NEW: extra state field
        --perf                                      # NEW: hot-path annotation
    ```

=== "TOML"

    ```toml
    # objects/NAME.toml
    arg_type = "float _Complex"
    return_type = "float _Complex"
    mutable = "false"
    no_state = "false"
    no_step = "true"
    perf = "true"                # NEW

    [[init_params]]
    name = "filepath"
    type = "const char *"
    default = "NULL"

    [[init_params]]
    name = "header_bytes"
    type = "size_t"
    default = "0"

    [[state]]
    name = "fd"
    type = "int"
    default = "-1"

    [[state]]
    name = "file_size"
    type = "size_t"
    default = "0"

    [[state]]
    name = "position"
    type = "size_t"
    default = "0"

    # NEW: extra state field
    [[state]]
    name = "mmap_base"
    type = "size_t"
    default = "0"
    ```

What each addition contributes:

- `--state mmap_base:size_t:0` — another state field; struct member,
    getter, setter, ctor kwarg, and reset assignment, all generated.
- `--perf` — annotates this object's hot-path functions with
    `JM_HOT` / `JM_FORCEINLINE`. (`jm perf` retrofits the whole project
    at once.)

Bodies live in `_core.c`. Open the file, fill in `NAME_create()`
(open the file, populate state), `NAME_destroy()` (close the file,
free state), and the custom-method bodies. There is no flag for
lifting bodies from elsewhere.

### Methods, properties, and variable-output

A reader is method-driven (`read` / `seek` / `close`), so methods
are load-bearing. CLI verbs are fine when adding one at a time;
multi-method reader specs are naturally authored in the TOML
fragment.

=== "One-off via CLI"

    ```sh
    # Custom verbs (the reader's primary API)
    jm method NAME read  --return-type "float _Complex" --param n:size_t
    jm method NAME seek  --param offset:int64_t
    jm method NAME close

    # Expose a cursor as a read-only Python property
    jm property NAME position --type size_t

    # Scalar accessor
    jm method NAME tell --return-type int64_t

    # Variable-output sweep
    jm method NAME scan \
        --variable-output --max-out 64 \
        --result-field idx:size_t \
        --result-field magnitude:float
    ```

=== "Many at once via TOML"

    ```toml
    # objects/NAME.toml — append the blocks below
    [[properties]]
    name = "position"
    type = "size_t"
    writable = false

    [[methods]]
    name = "read"
    return_type = "float _Complex"

    [[methods.params]]
    name = "n"
    type = "size_t"

    [[methods]]
    name = "seek"
    return_type = "void"

    [[methods.params]]
    name = "offset"
    type = "int64_t"

    [[methods]]
    name = "close"
    return_type = "void"

    [[methods]]
    name = "tell"
    arg_type = "void"
    return_type = "int64_t"

    [[methods]]
    name = "scan"
    arg_type = "float _Complex[]"
    return_type = "size_t"
    variable_output = true
    max_out = 64

    [[methods.result_fields]]
    name = "idx"
    type = "size_t"

    [[methods.result_fields]]
    name = "magnitude"
    type = "float"
    ```

Either path updates the fragment and regenerates glue. Sacred
`_core.c` is not touched; add each method's body yourself —
`read` / `seek` / `close` are the workhorses for a reader —
following the declaration in `_core.h`.

### The resulting Python class

After the composed `jm object` command plus the follow-ups, the same
reader `NAME` Python class exposes:

```python
rdr = NAME(filepath="capture.iq", header_bytes=0, mmap_base=0)
pos = rdr.position             # property (read-only)
offset = rdr.tell()            # scalar method
events = rdr.scan(chunk)       # variable-output method
chunk = rdr.read(4096)         # primary reader API
rdr.seek(0); rdr.close()
```

The C surface, the binding, the tests, the bench, and the `.pyi`
stub all stay in sync.

## Concrete types

| Slot                                      | Accepts                                                                                                                                 | Rejects                                                                                          | Default                                            |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | -------------------------------------------------- |
| `--init-param name:T:D`                   | Path/filename strings use `const char *`. Any [scalar](../types.md#constructor--init-param-types), `T[]`, `T[][]`, `string_enum:a,b,c`. | `T[N]` (fixed length — that's `--state` territory).                                              | `filepath:"const char *", header_bytes:size_t:0`   |
| `--state field:T:D`                       | Any [scalar](../types.md#state-variable-types). The file descriptor pattern is `fd:int:-1`.                                             | `const char *` (use an `--init-param` to receive the path, then store the parsed `fd` / `size`). | `fd:int:-1, file_size:size_t:0, position:size_t:0` |
| Method return / output (`out_type = "T"`) | Any [array element type](../types.md#array-element-types). `read()` returns a `T[]` ndarray sized from the requested sample count.      | `bool`, `int`, `const char *`, `long double _Complex`.                                           | `float _Complex`                                   |

`const char *` is the load-bearing type here. It is a valid init-param
(PyArg parses the Python str; lifetime is managed on the Python side)
but is **not** a valid state field — persist the parsed result (the
`fd`, a `size_t`) instead. This is the asymmetry the reader preset
exists to formalise.
