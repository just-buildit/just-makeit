# ring_buffer example

A header-only ring buffer whose `wait(n)` lends a **zero-copy view** of memory
the ring already owns — the shape of doppler's `DECLARE_DP_BUFFER`, declared
rather than hand-written.

## TL;DR — see it work first

```sh
just-makeit example ring_buffer
# ring_buffer: PASSED
```

!!! note "Three features, first exercised together"

    This is the first example where `header_only`, `borrow` and a borrowed
    `record_dtype` all appear at once. They shipped separately, and their
    composition is exactly where the gaps were.

______________________________________________________________________

## What it demonstrates

- **`--header-only`** — the component's C is entirely `static inline` in its
    header. jm scaffolds **no `<comp>_core.c`**, and the CMake core library is
    `INTERFACE` rather than `OBJECT` (an OBJECT library with no sources is a
    hard *configure* error).
- **`--borrow`** — `wait(n)` returns a pointer into the ring's own memory. The
    binding wraps it with no copy, marks it read-only, and pins the object so
    the memory cannot outlive the view.
- **`--record-dtype` on a borrow** — integer IQ comes back as a structured
    array, because numpy has no complex-integer dtype.

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

just-makeit method cf32_ring write --arg-type "float _Complex[]" --return-type size_t
just-makeit method cf32_ring wait  --borrow --param n:size_t
just-makeit method cf32_ring consume --param n:size_t --arg-type void --return-type void
```

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
