# ring_buffer example

A complete, useful object: a bounded single-producer/single-consumer ring
buffer whose reads lend a **zero-copy view** of memory the ring already owns,
whose failures are typed rather than lumped together, and whose release call
you write the count for once.

Rings like this turn up wherever a producer and a consumer meet — audio and
radio sample streams, telemetry and log spooling, packet capture, frame
queues between threads. Nothing here is domain-specific; the element type is
the only thing the two objects below disagree about.

It is also the example where **every feature of a borrow appears together**,
which is deliberate: their composition is where the gaps have been.

## TL;DR — see it work first

```sh
just-makeit example ring_buffer
# ring_buffer: PASSED
```

!!! note "Every borrow feature, exercised together"

    `header_only`, `borrow`, a borrowed `record_dtype`, `nogil`,
    `none_on_empty`, a `status_fn` table, a message that names a param and a
    property, `strict`, and `releases` — all in one object that builds and
    runs. They shipped separately, and their composition is exactly where the
    gaps were: two defects
    ([gh-1426](https://github.com/just-buildit/just-makeit/issues/1426)) got
    past a suite that asserted on generated *text*, because nothing compiled
    these shapes together. This example is that missing gate.

______________________________________________________________________

## What it demonstrates

- **`--header-only`** — the component's C is entirely `static inline` in its
    header. jm scaffolds **no `<comp>_core.c`**, and the CMake core library is
    `INTERFACE` rather than `OBJECT` (an OBJECT library with no sources is a
    hard *configure* error).
- **`--borrow`** — `wait(n)` returns a pointer into the ring's own memory. The
    binding wraps it with no copy, marks it read-only, and pins the object so
    the memory cannot outlive the view.
- **`--record-dtype` on a borrow** — a two-field sample comes back as a
    structured array, because numpy has no complex-integer dtype.
- **`--nogil`** — the GIL is released across the read. On a kernel that can
    wait this is correctness, not speed: with the GIL held a producer thread
    could never run, so a threaded producer/consumer would hang rather than
    fail.
- **`--status-fn` / `--status-error`** — a NULL read means one of several
    things, and the binding says which. One C function owns the precedence;
    the table maps its answers to `ValueError`, `EOFError` and
    `KeyboardInterrupt`. End of stream is what a consumer loop **catches**,
    not an error.
- **A message that names its numbers** — `wait({n}) can never be satisfied:
    the ring holds {capacity}` resolves `{n}` from the method's param and
    `{capacity}` from a declared property, through `PyErr_Format`.
- **`--none-on-empty`** — `peek()` shares one table with `wait()` and
    declines only the "not yet" row, so it answers `None` where `wait()`
    would raise. One table, two readings.
- **`--releases`** — `consume()` defaults its count to whatever the last read
    lent, so `view = r.wait(512); r.consume()` writes the number once.
    `close()` is a second release that takes no count and simply invalidates
    whatever is outstanding.
- **`--strict`** — the write path refuses a wrong dtype, rank or stride
    instead of silently casting, copying and flattening it. On the one path
    whose purpose is to avoid copies, the kindness is the bug.

## Two rings, because the element type decides the shape

| object     | element            | `wait(n)` returns                    |
| ---------- | ------------------ | ------------------------------------ |
| `Cf32Ring` | `float _Complex`   | `complex64`, 1-D                     |
| `Iq16Ring` | `iq16_t {i, q}`    | `[('i','<i2'),('q','<i2')]`, 1-D     |

They are two components rather than one template because their element shapes
genuinely differ — see
[gh-1310](https://github.com/just-buildit/just-makeit/issues/1310).

______________________________________________________________________

## 1. Declare

```sh
just-makeit new ringdemo && cd ringdemo
just-makeit module rings

just-makeit object cf32_ring --module rings --header-only --no-step \
    --arg-type void --return-type "float _Complex" \
    --state 'data:float _Complex[64]' --state head:size_t:0 --state tail:size_t:0

just-makeit property cf32_ring capacity --module rings --type size_t \
    --expr 'sizeof(self->handle->data) / sizeof(self->handle->data[0])'

just-makeit method cf32_ring write --arg-type "float _Complex[]" \
    --return-type size_t --strict

just-makeit method cf32_ring wait --borrow --param n:size_t --nogil \
    --status-fn cf32_ring_wait_status \
    --status-error "CF32_TOO_LARGE:ValueError:wait({n}) can never be satisfied: the ring holds {capacity}" \
    --status-error "CF32_CLOSED:EOFError:end of stream: the producer closed the ring" \
    --status-error "CF32_WRAPS:ValueError:that span wraps; consume() first"

just-makeit method cf32_ring peek --borrow --param n:size_t --none-on-empty \
    --status-fn cf32_ring_wait_status \
    --status-error "CF32_TOO_LARGE:ValueError:peek({n}) can never be satisfied: the ring holds {capacity}" \
    --status-error "CF32_CLOSED:EOFError:end of stream: the producer closed the ring"

just-makeit method cf32_ring consume --param n:size_t --arg-type void \
    --return-type void --releases wait,peek
just-makeit method cf32_ring close --arg-type void --return-type void \
    --releases wait,peek
```

The status vocabulary is the **author's**, like the record struct below, and
goes in the component's own header — jm names it and never defines it:

```c
typedef enum {
    CF32_OK = 0,          /* n elements are readable now             */
    CF32_PENDING = 1,     /* fewer than n so far; nothing is wrong   */
    CF32_TOO_LARGE = 2,   /* n exceeds capacity: never satisfiable   */
    CF32_CLOSED = 3,      /* closed with fewer than n left: the end  */
    CF32_WRAPS = 4        /* the span would wrap the buffer          */
} cf32_ring_status_t;
```

`CF32_PENDING` has a row in neither table, which is the point: `peek()` falls
through it to `--none-on-empty` and answers `None`, while `wait()` has no
such fallback and raises. One vocabulary, two readings.

The integer ring is the same, plus the record its view hands back. The struct
is the **author's** and goes in the component's own header before the method
that names it:

```c
/* native/inc/iq16_ring/iq16_ring_core.h */
typedef struct { int16_t i; int16_t q; } iq16_t;
```

```sh
just-makeit method iq16_ring wait --borrow --param n:size_t \
    --record-dtype iq16_t --result-field i:int16_t --result-field q:int16_t
```

## 2. Implement — in the header

There is no `_core.c`. jm says so when it generates the stub:

```
Done!  Implement cf32_ring_wait() in cf32_ring_core.h
```

```c
static inline float _Complex *
cf32_ring_wait(cf32_ring_state_t *state, size_t n)
{
    size_t have = state->head - state->tail;
    size_t off  = state->tail & 63;
    if (n > have || off + n > 64)
        return NULL;          /* NULL is the failure signal; jm raises */
    return &state->data[off];
}
```

The header is **sacred** — an implementation written there survives
`just-makeit apply`.

## 3. Use from Python

```python
r = Cf32Ring()
r.write(np.arange(8, dtype=np.complex64))
v = r.wait(4)          # complex64, zero-copy
v.base is r            # True  — the view pins the object
v.flags.writeable      # False — a consumer does not write through a borrow
r.consume(4)
```

```python
q = Iq16Ring()
q.write(np.array([1, 100, 2, 101, 3, 102], dtype=np.int16))  # interleaved
w = q.wait(3)
w.dtype          # dtype([('i', '<i2'), ('q', '<i2')])
w.itemsize       # 4  == sizeof(iq16_t)
w.shape          # (3,)  — one element per SAMPLE, not per int16
w['i']           # array([1, 2, 3], dtype=int16)
w + 1            # TypeError — refused, not silently wrong
```

That last line is the point of the representation. The tempting alternative —
packing I/Q into one `int32` — is also 1-D and one element per sample, and
`d + 1` increments **I only**, because int32 addition carries across the I/Q
boundary. A structured array refuses loudly instead.

______________________________________________________________________

## Key concepts

**A borrow is a usage contract, not an enforced one.** The view is valid until
the author's own release call (`consume()` here). jm states that on both faces
and does not try to enforce it, because in CPython it cannot: every scheme that
refuses to recycle while a view is outstanding also refuses *correct* idiomatic
code, since the caller's name for the previous view is still bound at every
point the producer could check. Measured on
[gh-1312](https://github.com/just-buildit/just-makeit/issues/1312).

**The ring here is simpler than doppler's.** `wait()` refuses a span that would
wrap; doppler double-maps its memory so a wrapping span is still contiguous.
That mapping is a property of the ring, not of the jm features shown here.

## See also

- [Extending an object](../commands/extend.md) — `--borrow`, `--record-dtype`
- [Types](../types.md) — why there is no complex-integer dtype
- [Memory ownership](../memory-ownership.md) — who owns a returned array
