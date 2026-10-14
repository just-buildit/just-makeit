"""A GIL-free call and the teardown that frees under it (gh-2272).

Every binding kind jm generates can release the GIL around a kernel call --
an object or a handle / capsule method declaring ``nogil``, a composer's
generator loop always -- and every kind also generates an explicit free:
``destroy()``, ``close()``, ``__exit__``. The free ran holding the GIL and
asked nothing, so thread A inside a released call and thread B calling
``close()`` raced: B freed the state under A's running kernel. doppler's
``Writer.write`` crashed (SIGSEGV) in 10 of 10 runs (doppler-dsp/doppler#2294).

``tp_dealloc`` cannot race this way: a running call holds a reference to
``self``, so the object cannot be deallocated under it. Only the explicit
teardown can, so only it waits.

The contract, in this order, all of it rendered from this file:

1. A released region is ``jm_inflight_enter`` / ``Py_BEGIN_ALLOW_THREADS``
   / the call / ``Py_END_ALLOW_THREADS`` / ``jm_inflight_leave``. The count
   moves under the GIL, so it needs no atomics.
2. The teardown first makes the object refuse new calls -- an object NULLs
   ``self->handle``, a handle or composer sets ``closed``, a capsule sets
   ``destroyed`` -- through the guard every method already runs, so a call
   that starts during ``close()`` is refused, not admitted.
3. Then it waits, with the GIL released, for the count to reach zero
   (``jm_inflight_wait``), and only then frees.

Because step 2 NULLs an object's ``self->handle`` before the wait, a released
object call must not read ``self->handle`` inside the region: `hoist_handle`
reads it into a local before the GIL is dropped. The other kinds keep their
pointer until the free, so they need no hoist.

Waiting rather than refusing ``close()`` is deliberate: "one thread records,
another stops it" is the ordinary use of an object with a ``close()``, and a
``RuntimeError`` there would make it a race the caller has to win.

Not covered here, and filed: a teardown reached from a Python callback
*during argument conversion* -- after a method's guard ran, before its kernel
call -- leaves the call a NULL state, with or without ``nogil`` (gh-2273);
and a composer source's property setters free a buffer a running generator
may borrow, and never invalidate it (gh-2274).
"""

from __future__ import annotations

#: The binding-struct field every kind keeps its in-flight count in. Named
#: once: the struct, the region, the teardown and the dealloc spell it here,
#: and `_docsync` reads a fragment for it.
FIELD = "_jm_inflight"

#: Marks the helper's definition, so a translation unit that is handed it
#: twice (a module aggregator and a fragment) compiles it once.
DEFINED = "JM_INFLIGHT_DEFINED"

INFLIGHT_C = """\
#ifndef JM_INFLIGHT_DEFINED
#define JM_INFLIGHT_DEFINED
/* gh-2272: the GIL-free calls running on one object, so its explicit
 * teardown can wait for them instead of freeing under them. `n` is read
 * and written only with the GIL held. `idle` exists only once a teardown
 * has had to wait: that teardown holds it, and the last call to leave
 * gives it back. */
typedef struct {
    Py_ssize_t n;
    int waiting;
    PyThread_type_lock idle;
} jm_inflight_t;

#if defined(__GNUC__) || defined(__clang__)
__attribute__((unused))
#endif
static inline void
jm_inflight_enter(jm_inflight_t *g)
{
    g->n++;
}

#if defined(__GNUC__) || defined(__clang__)
__attribute__((unused))
#endif
static inline void
jm_inflight_leave(jm_inflight_t *g)
{
    if (--g->n == 0 && g->waiting) {
        g->waiting = 0;
        PyThread_release_lock(g->idle);
    }
}

/* Called with the GIL held, after the object refuses new calls. Returns
 * with none running: 0, or -1 with MemoryError when no lock could be had,
 * in which case nothing has been waited for. */
#if defined(__GNUC__) || defined(__clang__)
__attribute__((unused))
#endif
static inline int
jm_inflight_wait(jm_inflight_t *g)
{
    while (g->n > 0) {
        if (!g->idle) {
            g->idle = PyThread_allocate_lock();
            if (!g->idle) {
                PyErr_NoMemory();
                return -1;
            }
        }
        /* Take the lock, then block on it again with the GIL released:
         * the last jm_inflight_leave releases it. The GIL is dropped by
         * function here, not by the macro pair, so the macro still marks
         * exactly the calls a binding runs without it. */
        (void)PyThread_acquire_lock(g->idle, NOWAIT_LOCK);
        g->waiting = 1;
        PyThreadState *ts = PyEval_SaveThread();
        (void)PyThread_acquire_lock(g->idle, WAIT_LOCK);
        PyEval_RestoreThread(ts);
        PyThread_release_lock(g->idle);
    }
    return 0;
}

#if defined(__GNUC__) || defined(__clang__)
__attribute__((unused))
#endif
static inline void
jm_inflight_free(jm_inflight_t *g)
{
    if (g->idle) {
        PyThread_free_lock(g->idle);
        g->idle = NULL;
    }
}
#endif /* JM_INFLIGHT_DEFINED */
"""


def struct_field(indent: str = "    ") -> str:
    """The binding-struct member holding the count.

    Examples
    --------
    >>> print(struct_field(), end="")
        jm_inflight_t _jm_inflight;  /* gh-2272: GIL-free calls running */
    """
    return f"{indent}jm_inflight_t {FIELD};  /* gh-2272: GIL-free calls running */\n"


def region(owner: str, body: str, indent: str = "    ") -> str:
    """*body* run with the GIL released, counted on *owner*'s struct.

    *owner* is the C pointer to the binding struct (``self``, ``w``);
    *body* is the already-indented statement(s) the region runs.

    Examples
    --------
    >>> print(region("self", "    k(_jm_h);\\n"), end="")
        jm_inflight_enter(&self->_jm_inflight);
        Py_BEGIN_ALLOW_THREADS
        k(_jm_h);
        Py_END_ALLOW_THREADS
        jm_inflight_leave(&self->_jm_inflight);
    """
    return opening(owner, indent) + body + closing(owner, indent)


def opening(owner: str, indent: str = "    ") -> str:
    """The first half of a `region`, for a generator that splices the body.

    Every place jm releases the GIL is this pair -- `region` itself, and the
    handle and composer generators that build the call between them -- so
    no kind releases it through a string of its own (gh-2267).

    Examples
    --------
    >>> print(opening("self") + "    k();\\n" + closing("self"), end="")
        jm_inflight_enter(&self->_jm_inflight);
        Py_BEGIN_ALLOW_THREADS
        k();
        Py_END_ALLOW_THREADS
        jm_inflight_leave(&self->_jm_inflight);
    """
    return (
        f"{indent}jm_inflight_enter(&{owner}->{FIELD});\n"
        f"{indent}Py_BEGIN_ALLOW_THREADS\n"
    )


def closing(owner: str, indent: str = "    ") -> str:
    """The second half of a `region` (see `opening`)."""
    return (
        f"{indent}Py_END_ALLOW_THREADS\n"
        f"{indent}jm_inflight_leave(&{owner}->{FIELD});\n"
    )


#: The local a released object call reads its state through.
HANDLE_LOCAL = "_jm_h"


def hoist_handle(call_expr: str) -> tuple[str, str]:
    """Read ``self->handle`` before the region, for an object's teardown.

    An object's teardown NULLs ``self->handle`` before it waits (that is how
    a new call is refused), so a call already released must not read it
    again. Returns ``(declaration, rewritten_call)``; both are empty /
    unchanged when the call does not read the handle. ``void *`` converts
    to the kernel's state pointer implicitly in C.

    Examples
    --------
    >>> hoist_handle("k(self->handle, x)")
    ('    void *_jm_h = self->handle;\\n', 'k(_jm_h, x)')
    >>> hoist_handle("k(w->state)")
    ('', 'k(w->state)')
    """
    if "self->handle" not in call_expr:
        return "", call_expr
    return (
        f"    void *{HANDLE_LOCAL} = self->handle;\n",
        call_expr.replace("self->handle", HANDLE_LOCAL),
    )


def wait(owner: str, fail: str, indent: str = "        ") -> str:
    """The teardown's wait: none of *owner*'s released calls still running.

    *fail* is the statement(s) run when no lock could be had (MemoryError
    is set): undo the refusal and return the failure, so the object stays
    usable and nothing was freed.

    Examples
    --------
    >>> print(wait("self", "return NULL;"), end="")
            /* gh-2272: a GIL-free call may still be running on this
               object; new ones are refused from here, so wait for those. */
            if (jm_inflight_wait(&self->_jm_inflight) < 0) {
                return NULL;
            }
    """
    return (
        f"{indent}/* gh-2272: a GIL-free call may still be running on this\n"
        f"{indent}   object; new ones are refused from here, so wait for those. */\n"
        f"{indent}if (jm_inflight_wait(&{owner}->{FIELD}) < 0) {{\n"
        f"{indent}    {fail}\n"
        f"{indent}}}\n"
    )


def free(owner: str, indent: str = "    ") -> str:
    """The dealloc's release of the lock a wait may have allocated.

    Examples
    --------
    >>> print(free("self"), end="")
        jm_inflight_free(&self->_jm_inflight);
    """
    return f"{indent}jm_inflight_free(&{owner}->{FIELD});\n"
