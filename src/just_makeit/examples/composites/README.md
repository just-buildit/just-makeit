# Composites — the `kind = "handle"` generator

This example builds one jm project that demonstrates the **object-of-objects
`kind = "handle"` generator**: a single typed CPython class generated over an
opaque hand-C resource handle, with RAII lifetime.

A handle module is the *resource* shape — a file writer, a socket, a session,
a clock. You hand-write only the resource's C; `jm apply` materializes the
whole binding: the typed class, its constructor, its methods, decoded-from-a-
getter properties, and the context-manager / `close()` protocol.

It is the focused, teaching member of the capsule / composer / handle family.
For the full surface (capsule free-functions, composer object-of-objects, and
every handle shape) see [`docs/object-of-objects.md`](../object-of-objects.md).

Run it end to end:

```sh
jm example composites
```

---

## The hand-written resource

The only code you write is the resource itself — here a small FIFO ring
buffer. It opens and closes (the create/close lifecycle), pushes and pops
arrays (the methods), fills a `*_stats_t` struct (the decoded getters read it),
and has a gain get/set pair (the writable property).

The public header lives under `native/inc/ringbuf/`:

```c
#ifndef RINGBUF_H
#define RINGBUF_H
#include <stddef.h>
typedef struct ringbuf ringbuf_t;
typedef struct
{
  size_t used;
} ringbuf_stats_t;
/**
 * @brief Open a fixed-capacity FIFO ring buffer of 32-bit floats.
 *
 * The buffer holds up to `capacity` samples; a push past capacity drops
 * the overflow and reports how many were accepted.
 *
 * @param capacity  Maximum number of samples buffered at once.
 */
ringbuf_t *ringbuf_open (size_t capacity);
void       ringbuf_close (ringbuf_t *r);
/**
 * @brief Append samples to the buffer, scaling each by the current gain.
 * @param x  Samples to append (oldest-to-newest).
 * @return The number of samples accepted; fewer than requested once full.
 * @code
 * >>> import numpy as np
 * >>> from composites.ring import Ring
 * >>> r = Ring(capacity=4)
 * >>> r.push(np.array([1, 2, 3, 4, 5, 6], np.float32))
 * 4
 * @endcode
 */
size_t ringbuf_push (ringbuf_t *r, const float *x, size_t n);
/**
 * @brief Remove and return the oldest buffered samples, FIFO order.
 * @param n  Maximum number of samples to remove.
 * @return A new array of the popped samples (up to `n`, fewer if drained).
 */
size_t ringbuf_pop (ringbuf_t *r, float *out, size_t n);
void   ringbuf_stats (const ringbuf_t *r, ringbuf_stats_t *out);
/**
 * @brief The gain applied to every pushed sample.
 */
float ringbuf_get_gain (const ringbuf_t *r);
void  ringbuf_set_gain (ringbuf_t *r, float gain);
#endif
```

The implementation is a pure-C OBJECT library under `native/src/ringbuf/`,
vendored as a `[project] c_deps` entry — no Python wrapper of its own:

```
# native/src/ringbuf/CMakeLists.txt
add_library(ringbuf_core OBJECT ringbuf.c)
target_include_directories(ringbuf_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc)
```

---

## The handle manifest

One `[module.ring]` section, `kind = "handle"`, declares the whole class. jm
reads it and generates everything — `ring_ext.c`, `CMakeLists.txt`, `ring.pyi`:

```
[project]
c_deps = ["ringbuf"]

[module.ring]
kind = "handle"
backing = "ringbuf"
header = "ringbuf/ringbuf.h"
package = "."
type_name = "Ring"
context_manager = true
create_fn = "ringbuf_open"
close_fn = "ringbuf_close"
depends_on = [{ name = "ringbuf", link = true }]
create_args = [{ name = "capacity", type = "size_t" }]

[[module.ring.methods]]
name = "push"
fn = "ringbuf_push"
returns = "size_t"
args = [{ name = "x", type = "float[]" }]

[[module.ring.methods]]
name = "pop"
fn = "ringbuf_pop"
returns = "float[]"
args = [{ name = "n", type = "size_t" }]

[[module.ring.getters]]
fn = "ringbuf_stats"
out = "ringbuf_stats_t"
cache = false
fields = [
  { name = "used", from = "used", type = "size_t" },
  { name = "fill_fraction", type = "double", expr = "self->capacity ? (double)tmp.used / (double)self->capacity : 0.0" },
]

[[module.ring.getters]]
fn = "ringbuf_get_gain"
out = "float"
fields = [{ name = "gain", type = "float", writable_fn = "ringbuf_set_gain" }]
```

The pieces:

- **`create_fn` / `close_fn`** — the open/close lifecycle. `close()` is
  idempotent and `tp_dealloc` closes a forgotten handle, so the resource always
  releases. `context_manager = true` also emits `__enter__` / `__exit__`.
- **`methods`** — `push` is an **array-in** method (numpy-marshaled), returning
  the count accepted; `pop` is an **int-in -> array-out** method returning an
  independent numpy-owned array (never a dangling view).
- **`getters`** — the decoded-getter property, the genuinely handle-specific
  code. `ringbuf_stats()` fills a live struct; `used` reads a field directly,
  `fill_fraction` is a derived `expr`. A scalar return-by-value getter whose
  field names a `writable_fn` becomes the read/write `gain` property.

Two naming details keep the build clean:

- the module is named **`ring`**, distinct from the **`ringbuf`** backing and
  c_dep, so the c_dep subdirectory and the handle target never collide on a
  shared `native/src/ringbuf/` binary dir;
- **`package = "."`** lands `ring.so` in the package root, so the import is the
  clean `from composites.ring import Ring` instead of `composites.ring.ring`.

`depends_on = [{ name = "ringbuf", link = true }]` is what makes the resource
link onto the module: jm adds the `ringbuf_core` OBJECT lib directly to
`ring`'s `target_link_libraries` (CMake does not pull OBJECT-lib objects into a
final `.so` transitively, so the link must be direct).

---

## The generated class

After `jm apply` and a cmake build, `composites.ring` exposes a fully typed
`Ring` class — entirely from the manifest, no hand-written Python or binding:

```python
import numpy as np

from composites.ring import Ring

r = Ring(capacity=4)
assert r.used == 0 and r.fill_fraction == 0.0

# array-in method -> count accepted (drops past capacity)
assert r.push(np.array([1, 2, 3, 4, 5], dtype=np.float32)) == 4
assert r.fill_fraction == 1.0

# int-in -> independent numpy array, FIFO oldest-first
assert r.pop(2).tolist() == [1.0, 2.0]
assert r.used == 2

# writable scalar property (push scales by gain)
r.gain = 2.0
assert r.gain == 2.0

# context manager + idempotent close()
with Ring(capacity=8) as rr:
    rr.push(np.arange(3, dtype=np.float32))
    assert rr.used == 3
# after __exit__ the handle is closed; access raises rather than crashing
try:
    _ = rr.used
except RuntimeError:
    pass
```

That is the whole point of the handle generator: a real, opaque C resource
wears an ergonomic typed-class face — constructor, array methods, decoded
properties, a writable property, and RAII — with the only hand code being the
resource's C. The capsule and composer generators cover the other two shapes of
the same idea (free functions over a handle, and an object built of objects);
see [`docs/object-of-objects.md`](../object-of-objects.md) for all three.

---

## Document once, in C

The vendored header is not only the resource's API — it is the single source of
truth for the class's **documentation**. A Doxygen `/** ... */` comment on a
`ringbuf_*` function flows straight into the generated `ring.pyi`, and a `@code`
block on a method becomes a **runnable doctest**. Nothing is written twice.

Three functions map to three documentation surfaces on the `Ring` class:

- **`ringbuf_open`** (the `create_fn`) documents the **class**: its `@brief`
  becomes the `Ring` summary and its `@param` annotates the constructor
  parameter.
- **`ringbuf_push`** (a method `fn`) documents the **method** `push`: its
  `@brief`/`@param`/`@return` become the numpy prose, and its `@code` block
  becomes a runnable `Examples` doctest.
- **`ringbuf_get_gain`** (a single-field getter `fn`) documents the **property**
  `gain`: its `@brief` becomes the property docstring.

Document `ringbuf_push`:

```c
/**
 * @brief Append samples to the buffer, scaling each by the current gain.
 * @param x  Samples to append (oldest-to-newest).
 * @return The number of samples accepted; fewer than requested once full.
 * @code
 * >>> import numpy as np
 * >>> from composites.ring import Ring
 * >>> r = Ring(capacity=4)
 * >>> r.push(np.array([1, 2, 3, 4, 5, 6], np.float32))
 * 4
 * @endcode
 */
size_t ringbuf_push (ringbuf_t *r, const float *x, size_t n);
```

`jm apply` re-derives the stub, and `Ring.push` in `ring.pyi` now carries the
full numpy-style docstring — including the `@code` block as an `Examples`
doctest:

```python
    def push(self, x: NDArray[Any]) -> int:
        """Append samples to the buffer, scaling each by the current gain.

        Parameters
        ----------
        x : NDArray[Any]
            Samples to append (oldest-to-newest).

        Returns
        -------
        int
            The number of samples accepted; fewer than requested once full.

        Examples
        --------
        >>> import numpy as np
        >>> from composites.ring import Ring
        >>> r = Ring(capacity=4)
        >>> r.push(np.array([1, 2, 3, 4, 5, 6], np.float32))
        4

        """
```

That doctest is not decoration: it runs against the *built* extension, so if the
ring buffer's capacity handling ever drifts from its documented example the
build fails. Pass `-v` to watch every `>>>` line execute:

```termynal
$ python -m doctest -v src/composites/ring.pyi
{d}Trying:{/d}
    r = Ring(capacity=4)
{d}Expecting nothing{/d}
{g}ok{/g}
{d}Trying:{/d}
    r.push(np.array([1, 2, 3, 4, 5, 6], np.float32))
{d}Expecting:{/d}
    4
{g}ok{/g}
{g}5 passed and 0 failed.{/g}
{g}Test passed.{/g}
```

In CI the whole suite is driven at once with `pytest --doctest-glob='*.pyi'`.

**Not every surface comes from the header.** A **multi-field** getter carries a
single struct `@brief` that cannot name each field it decodes, so the
`used` / `fill_fraction` properties keep their manifest-synthesized docstrings —
the same header-documents-the-C, manifest-documents-the-rest split the
`views_module` example draws. The header documents each C function once; the
manifest fills in what has no C function of its own.
