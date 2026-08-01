# Array memory ownership

Every generated binding that returns an array has to answer one question:
**who owns this memory, and what keeps it alive?** This page is the answer, as
a rule per layer, with the measurements behind each rule and the history that
produced them.

It exists because the question was answered three different ways over about a
year, and the first two answers were wrong in ways that took a heap overflow
and a 1.5 GB leak to surface. If you are adding a new array-returning shape,
read the [rules for new shapes](#rules-for-new-shapes) at the bottom.

## The rules

> **Layer 1 — C.** A DSP kernel never allocates an output. Outputs are
> caller-supplied out-parameters.
>
> **Layer 2 — Python.** NumPy owns each call's result. Nothing is shared
> between calls.
>
> **Layer 3 — `out=`.** Exists for *placement and determinism*, not
> throughput. It is measurably slower on average.

## Layer 1 — C: the caller owns every buffer

Every generated C kernel takes its output as a pre-allocated pointer:

```c
/* blockwise */
void  comp_steps(comp_state_t *state, const T *in, size_t n, R *out);
/* variable-length */
size_t comp_verb(comp_state_t *state, const T *in, size_t n_in, R *out);
/* records */
size_t comp_verb(comp_state_t *state, rec_t *result, size_t max_results);
```

The kernel writes and returns a count. It never mallocs something the caller
must free. The `--perf` tier holds to this too: `JM_DEFINE_STEPS` uses a
stack-resident scratch buffer sized at compile time, so the entire SIMD path
is allocation-free by construction.

!!! note "Scope: sample-producing kernels"

    This rule is about the entry points that carry **samples** out of a DSP
    block. It is deliberately not phrased as "nothing in the C API returns a
    pointer" — a rule that is 98% true collects exceptions until it means
    nothing. A survey of one real consumer found 123 pointer-returning
    functions on the public surface, 91 of them `*_create` / `*_open`
    constructors, and **not one sample-producing kernel returning a pointer to
    internal storage**. The rule is exactly true where it is stated.

    The other pointer-returning shapes are real, and each has its own
    one-line contract:

    | shape                  | example                        | contract                                |
    | ---------------------- | ------------------------------ | --------------------------------------- |
    | constructor            | `comp_create()`                | **caller frees**, via `comp_destroy()`  |
    | introspection accessor | `RateConverter_stages_value()` | borrowed; valid while the object lives  |
    | zero-copy receive      | `dp_msg_data()`                | borrowed; valid until the matching free |
    | serialized metadata    | `wfm_spec_to_json()`           | **caller frees** the returned string    |

    Two of those hand out heap the caller must release. That does not weaken
    the kernel rule — it is why the kernel rule is worth stating separately.

**Why it holds.** A caller-owned output is the only arrangement where
lifetime is not a question. The C caller already knows how long it needs the
samples; the kernel cannot know, so it must not decide.

## Layer 2 — Python: NumPy owns each result

Every array-returning binding allocates its result from NumPy, per call:

```c
npy_intp _adim = (npy_intp)_cap;
PyObject *arr0 = PyArray_SimpleNew(1, &_adim, NPY_COMPLEX64);
R *_d0 = (R *)PyArray_DATA((PyArrayObject *)arr0);
size_t n_out = comp_verb(self->handle, ..., _d0);
```

No instance buffer, no free-list, no liveness tracking. The returned array
owns its memory outright: it survives `destroy()`, it outlives the object, and
two results never alias.

**Why not reuse a buffer?** Because it makes "is my previous result still
valid?" a question the binding has to answer at runtime, and two serious
attempts to answer it both failed:

| attempt | what it added                                                                                      | why it wasn't enough                                                                                                   |
| ------- | -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| gh-219  | a deferred-free freelist — grow allocates fresh and *retires* the old buffer instead of freeing it | a same-size call never grows, so it reused the buffer in place and overwrote outstanding results                       |
| gh-437  | a weakref to the last returned view; retire if it is still alive                                   | the precondition ("the caller dropped the result") is false for `x = obj.steps(n)`, so every call took the retire path |

gh-604 is the bill for the second: a 3000-iteration loop that *bound* its
result grew RSS by 1.5 GB (~514 KiB retained per call) and ran 6-8× slower
than allocating. Both layers were correct code defending an incorrect
premise — that a binding can know whether the caller still holds the previous
result.

### What this costs

Per-call allocation needs the output length *before* the kernel runs. Where a
kernel can return fewer samples than requested, the binding allocates
`max(max_out(state, n), n)` and trims (or, with `pass_capacity`, exactly
`max_out(state, n)` — see below).

The trim is not a copy. Where the kernel fills the allocation exactly — the
generator shape's normal case — a fast path returns the array directly. Where
it writes fewer, the array is shrunk in place, which releases the tail.

**The thing to watch on a short-writing kernel is memory, not CPU** — and the
amount at stake is governed entirely by how tight `max_out()` is. A kernel
whose `max_out()` is a fixed internal cap rather than a function of the input
produces a large over-allocation on every call:

| method              | n_in | n_out | `max_out(state, n_in)`  | allocated           |
| ------------------- | ---- | ----- | ----------------------- | ------------------- |
| `Resampler.execute` | 1024 | 512   | fixed cap, ignores n_in | **128× the output** |
| `LO.steps`          | 64   | 64    | fixed cap, ignores n_in | 1024×               |
| `FIR.execute`       | 1024 | 1024  | `return 0;`             | sized from `n`      |

!!! warning "`max_out()` should be a per-call bound"

    A fixed cap makes the allocation — and, if the result is trimmed by a view
    rather than shrunk, the *retention* — proportional to the cap instead of
    the data. Return a bound computed from the count argument, not a constant
    — see the signature below ([gh-607](https://github.com/just-buildit/just-makeit/issues/607)).

### `max_out()` is a sizing contract, nothing else

With no instance buffer, `max_out()` sizes nothing internal. It does two
things: bounds the per-call allocation, and validates `out=`.

Since gh-607, `*_max_out()` takes the same count the binding is about to pass
to the kernel — named to mirror the kernel's own parameter for that method's
shape (`n_in` for an array-arg method, `<param>_len` for a single-array-param
method, `n` for a generator; an all-scalar-params method has no count to
mirror and its `max_out()` stays zero-arg). A fixed-output kernel takes the
parameter and ignores it — `(void)n_in;` — uniformly, since jm cannot tell
from the manifest whether a given block's output is call-independent.

### The `count` default for a void-input method

A `variable_output` method with `arg_type = "void"` has no input to size from,
so the binding gives it a `count` keyword and seeds it with `1`.

**Whether that matters depends on how the object produces its output, not on
the method's signature.** Two shapes look alike in the manifest and want
opposite things:

| Shape                                                                                                               | Does `count_default` apply?                                                                                                  |
| ------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **No-buffer / count-driven** — the kernel writes `count` samples into the binding's allocation                      | **Yes.** The count *is* the snapshot size, so its default is the method's zero-arg behaviour, and `1` is almost never right. |
| **Pre-allocated buffer** — the object owns a buffer sized at construction and the accessor hands back what is in it | **No.** The buffer already determines the snapshot; a count default has nothing to do and can only confuse the binding.      |

Signature is the wrong predicate here. A `void`-input `pass_capacity` method
can be either shape, so check what the object actually allocates before adding
the key — this is the sweep error to avoid (doppler-dsp/doppler#568, where
`delay`'s pre-allocated `_ptr_buf` meant its accessors never needed it).

For the first shape the right default is the object's natural capacity, which
lives in the user's C and cannot be derived from the manifest. Declare it
(gh-657):

```toml
[[delay.methods]]
name = "ptr"
arg_type = "void"
return_type = "double _Complex"
variable_output = true
pass_capacity = true
count_default = "state->num_taps"
```

The value is a C expression, evaluated once before argument parsing, and
overridden by any `count` the caller passes. An expression mentioning `state`
gets a local alias for the object's handle. Because it is C rather than a
Python literal, the `.pyi` and the runtime `__doc__` both render the default
as `...`.

Without the key the seed stays `1`, exactly as before.

It is **not** a reliable call-independent upper bound even so. A generator's
`steps(count)` writes exactly `count` samples, which can exceed it. The real
contract, **without** `pass_capacity`:

```
n_out <= max(max_out(state, n), n)
```

`0` is an ordinary answer now, not a "the kernel doesn't know" sentinel — a
decimator still filling its history productively returns `0` for a short
input. Without `pass_capacity` the binding clamps the allocation to at least
what the call needs regardless of what `max_out()` returns, so a
mechanically-migrated `return 0;` still allocates `n` and is safe — nothing
about `max_out()` is memory-safety-critical on this path.

**With** `pass_capacity = true`, the kernel takes a trailing `size_t max_out`
and the binding trusts the bound exactly: it allocates `max_out(state, n)`
with **no clamp**, and passes that same capacity to the kernel. The kernel is
now the one enforcing the bound — an under-reporting `max_out()` truncates at
the kernel rather than overflowing the allocation. Opt in when a kernel can
genuinely bounds-check its own writes; the exact allocation is the entire
point of doing so.

## Layer 3 — `out=` is for placement and determinism

`out=` writes into an array you supply. Use it when *where* the samples land
matters — an mmap'd file, a shared-memory segment, a preallocated ring, a
buffer another library owns — or when you have a **tail-latency budget at
large block sizes**.

Do not use it expecting throughput. It is slower on average.

### Throughput: a fixed cost, always

Measured on a generated project, complex64, same kernel both sides:

| n      | default (alloc) | `out=`    | delta  |
| ------ | --------------- | --------- | ------ |
| 64     | 85 ns           | 157 ns    | +72 ns |
| 1,024  | 377 ns          | 420 ns    | +43 ns |
| 65,536 | 16,934 ns       | 17,003 ns | +69 ns |

The overhead is **fixed** — validation plus building the returned view — not
proportional to `n`. That is why it should be quoted in nanoseconds and never
as a percentage: the same ~60 ns is 85% of the call at `n=64` and 0.4% at
`n=65536`.

It costs more than the allocation it avoids because a NumPy allocation of a
recently-freed block is roughly 130 ns and does not grow with `n` — the
allocator hands back the same warm block.

### Determinism: real, but only above a size threshold

Per-call latency distribution, 3000 calls at `n=65536`:

|                 | p50       | p99           | p99.9     | max       |
| --------------- | --------- | ------------- | --------- | --------- |
| default (alloc) | 13,184 ns | **39,503 ns** | 43,210 ns | 58,570 ns |
| `out=`          | 13,244 ns | **15,350 ns** | 17,272 ns | 19,116 ns |

At 64k, `out=` costs +60 ns at the median and buys **2.6× better p99** and 3×
better maximum. That is the allocator occasionally reaching the OS for a large
block, removed from the tail.

**The threshold matters.** At `n <= 1024` the same comparison goes the other
way — `out=` measures p99 1,543 ns against 1,172 ns for plain allocation —
because the allocator never leaves its free-list at that size, so there is no
allocator tail to remove and you are left paying only the fixed overhead.

> Use `out=` for a latency budget at **large** blocks. It does not improve
> jitter at small ones.

### Alignment: placement and alignment collide

The buffers `out=` exists to serve are exactly the ones prone to
misalignment — mmap'd regions, offsets into a shared segment, and **any NumPy
slice**. A misaligned output costs real throughput in vectorised kernels: a
measured **16% penalty on an FFT of 4096** against a misaligned `out=`.

So placement freedom is real but not free:

!!! warning "Align your placement to 16 bytes"

    If you pass `out=`, make sure the buffer's data pointer is 16-byte
    aligned. `np.zeros(n, dtype=...)` is; `big_array[3:]` is not. Slicing to
    produce an `out=` buffer is the easy way to lose the alignment silently.

### `out=` is validated, not coerced

An `out=` buffer must be a **writable, C-contiguous ndarray of exactly the
output dtype**; anything else raises `TypeError` rather than being converted.
Both properties matter for the same reason: the marshal asks NumPy for a
contiguous array of the output dtype, and if either is missing NumPy hands back
a *converted temporary*. The kernel then fills the temporary, the temporary is
freed, and the call returns a correct-looking result while your array is never
touched.

That failure mode is invisible to anyone who only reads the return value, which
is why both are hard errors:

```python
big = np.zeros((4, 2), np.float32)
g.steps(x, out=big[:, 0])       # TypeError: out must be a writable,
                                # C-contiguous ndarray of the output dtype
```

A wrong dtype was the original trigger (gh-581); a strided buffer is the same
defect reached from the other side. Note the overlap with the alignment note
above — slicing is the common way to arrive at both problems, except a strided
slice now raises where a merely misaligned one is quietly slower.

An undersized `out=` raises `ValueError`; the requirement is
`len(out) >= max(max_out(state, n_requested), n_requested)` — independent of
`pass_capacity`, since `out=` validates the *caller's* buffer, not the
internal allocation.

## Who owns what, by shape

| Shape                              | Allocated by                    | Result aliases                                    | Kept alive by      | `out=`   |
| ---------------------------------- | ------------------------------- | ------------------------------------------------- | ------------------ | -------- |
| `step()` scalar                    | — (no array)                    | —                                                 | —                  | n/a      |
| `steps()` blockwise                | NumPy, per call                 | nothing                                           | itself             | yes      |
| `steps(n)` generator               | NumPy, per call                 | nothing                                           | itself             | no       |
| `batch` method                     | NumPy, per call                 | nothing                                           | itself             | yes      |
| `variable_output`                  | NumPy, per call                 | its own allocation (view) or nothing (exact fill) | itself             | yes      |
| `variable_output` + `multi_output` | NumPy, per call, one per output | each its own allocation                           | itself             | no       |
| `out_type` method                  | NumPy, per call                 | nothing                                           | itself             | no       |
| `result_fields`                    | stack array, copied into tuples | nothing                                           | —                  | n/a      |
| `result_fields` + `single`         | returned by value               | nothing                                           | —                  | n/a      |
| Module function `out_type`         | NumPy, per call                 | nothing                                           | itself             | no       |
| Module function `result_fields`    | heap, freed before return       | nothing                                           | —                  | n/a      |
| `buf_field` property               | **C state struct**              | the object's state                                | `self` (INCREF'd)  | n/a      |
| Array state `get_<name>()`         | NumPy, per call                 | nothing (copy)                                    | itself             | n/a      |
| Array state `get_<name>_view()`    | **C state struct**              | the object's state                                | `self`             | n/a      |
| Handle (c)/(e)                     | NumPy, per call                 | nothing                                           | itself             | no       |
| Handle (d), capsule `execute`      | **caller**                      | the caller's array                                | the caller's array | required |
| Handle (f) `bytes`                 | copied into `bytes`             | nothing                                           | —                  | n/a      |
| Composer `steps`/`compose`         | NumPy, per call                 | nothing                                           | itself             | no       |

!!! danger "Borrowed views do not survive `destroy()`"

    The two shapes that borrow the C state's memory — the `buf_field` property
    and `get_<name>_view()` — pin the Python wrapper, which keeps the *object*
    alive but not its state. An explicit `obj.destroy()` (or leaving a `with`
    block) frees the state while the view still points at it. Read such a view
    before destroying, or copy it with `np.array(v)`.

## Rules for new shapes

When you add an array-returning shape to the generator:

1. **Name the owner.** NumPy, the caller, or the C state. Write it in the
    shape's comment and add a row to the table above.
1. **A borrowed view must pin something.** If you return
    `PyArray_SimpleNewFromData` over memory you did not allocate, call
    `PyArray_SetBaseObject` on whatever keeps that memory alive. A view that
    pins nothing is a dangling pointer waiting for a `del`.
1. **Never make validity depend on a runtime probe.** If the correctness of a
    returned array depends on the binding guessing what the caller did with the
    previous one, the design is wrong. This is the specific mistake gh-219 and
    gh-437 made.
1. **Prefer per-call allocation.** It is ~130 ns, flat in `n`. Reuse is an
    optimisation you must justify with a measurement against the *hold* case,
    not the drop case.
1. **Trim in place when the array is fresh and unshared**
    (`PyArray_DIMS(arr)[0] = n`); use a view + `SetBaseObject` only when the
    base is the caller's array.

## Appendix: allocation cost

Why "just allocate" is the default — NumPy allocation, complex64:

| condition                             | cost                                   |
| ------------------------------------- | -------------------------------------- |
| steady size, result dropped           | ~130–285 ns, **flat** from n=1 to n=1M |
| varying sizes (defeats the free-list) | ×1.6                                   |
| every result retained (no recycling)  | ×5–11                                  |

The allocator recycles: a freed block of the same size comes straight back,
already mapped and warm. This is why per-call allocation costs the same at
1M samples as at 1, and why the reuse buffer's saving was bounded at roughly
one allocation — about 130 ns — no matter how large the block.

The retained-result row is the one to understand. Allocation gets 5–11×
more expensive when nothing can be recycled — but a program that retains
results is paying for that memory because it asked for it. The old reuse
buffer paid that cost for memory **nobody could reach**: retired buffers held
until `tp_dealloc`, on top of a fresh allocation per call.
