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

The one heap object the generated C API hands out is the state itself, from
`comp_create()`, released by `comp_destroy()`.

!!! note "Scope: DSP kernels"

    This rule is about **DSP kernels** — the signal-processing entry points jm
    generates and that you hand-write against them. It is deliberately not
    phrased as "nothing in the C API returns a pointer", because that is not
    true of every neighbouring surface: doppler's messaging API, for instance,
    has `dp_msg_data(dp_msg_t *)`. A rule that is 98% true collects
    exceptions until it means nothing. This one is exactly true for kernels.

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
`max(max_out(), n)` and trims.

The trim is a **view**, not a copy — `PyArray_SimpleNewFromData` over the same
memory with `PyArray_SetBaseObject` pinning the full allocation. So the cost
is one small object plus **retained over-allocation**: the view keeps
`_cap - n_out` unused elements resident until the view itself is dropped.
Where the kernel fills the allocation exactly — the generator shape's normal
case — a fast path returns the array directly and there is no view at all.

The thing to watch on a short-writing kernel is therefore **memory, not CPU**.
A kernel whose `max_out()` is far above its typical `n_out` will keep the
difference alive for as long as the caller keeps the result. If that matters,
tighten `max_out()` or use `out=`.

### `max_out()` is a sizing contract, nothing else

With no instance buffer, `max_out()` sizes nothing internal. It does two
things: bounds the per-call allocation, and validates `out=`.

It is **not** a reliable call-independent upper bound. A generator's
`steps(count)` writes exactly `count` samples, which can exceed it. The real
contract is:

```
n_out <= max(max_out(state), n_requested)
```

Returning `0` is legal and means "unknown" — the binding then sizes from the
call. If your kernel needs to know the true capacity it was given, declare
`pass_capacity = true` and take a trailing `size_t max_out`; that is the
mechanism for a kernel that must bounds-check rather than trust.

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

A wrong-dtype `out=` raises `TypeError` rather than being cast, because a cast
would fill a throwaway temporary and leave your array untouched while still
returning a correct-looking result (gh-581). An undersized `out=` raises
`ValueError`; the requirement is `len(out) >= max(max_out(), n_requested)`.

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
