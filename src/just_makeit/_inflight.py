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

Step 3 alone is what makes the free safe: the wait returns holding the GIL
with nothing running, and the free follows without releasing it. Step 2 is
liveness -- without it a stream of new calls could hold the count above
zero for ever -- and what a closed object means to a caller. A composer
source's re-``__init__`` has no refusal to raise, so it waits without one
and a continuous ``steps()`` on another thread can starve it.

The limits that follow, documented for ``nogil``: a released kernel must
return in bounded time, since ``close()`` waits for it and the lock acquire
is not interrupted (gh-2276 asks for a ``wake``); a kernel that calls back
into Python which closes its own object deadlocks; and a second ``close()``
issued during the wait returns at once, before the free.

Because step 2 NULLs an object's ``self->handle`` before the wait, a released
object call must not read ``self->handle`` inside the region: `hoist_handle`
reads it into a local before the GIL is dropped. The other kinds keep their
pointer until the free, so they need no hoist.

Waiting rather than refusing ``close()`` is deliberate: "one thread records,
another stops it" is the ordinary use of an object with a ``close()``, and a
``RuntimeError`` there would make it a race the caller has to win.

A teardown reached from a Python callback *during argument conversion* --
after a method's guard ran, before its kernel call -- leaves the call a NULL
state, with or without ``nogil`` (gh-2273): converting an array argument can
run an ``__array__``, and that may call ``destroy()``. `recheck` is the
answer, and it is emitted by the one kernel-call emitter
(``_methods._nogil_call``), so no call site spells its own: the guard is run
again after the last conversion, the same ``RuntimeError`` is raised, and
the references the call acquired are released first.

A composer source's property setters free a buffer a running generator may
borrow (gh-2274). They go through ``_composer._replace_c``, which converts
the new value into a copy, waits here (`wait`), drops the generator, and only
then frees and installs.
"""

from __future__ import annotations

import re

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


def recheck(
    owner: str,
    call_expr: str,
    release: str,
    gone: str = "destroyed",
    indent: str = "    ",
) -> str:
    """The liveness guard again, just before the kernel call (gh-2273).

    A method's guard runs at the top, then its arguments are converted, and
    converting can run Python (an ``__array__``) that tears the object down.
    Returns the empty string when *call_expr* reads no teardown-visible
    state: an object's kernel call reads ``self->handle`` (NULLed by its
    teardown), a wrapper's reads ``<owner>->state`` (kept until the free, but
    flagged ``destroyed`` first), and anything else has nothing to check.

    *release* is the statement(s) that undo what the call acquired -- the
    array references, the allocated result -- run before the refusal, which
    is the same ``RuntimeError`` the top guard raises. It is required of
    every caller, as ``""`` when nothing was acquired, so a new call site
    must state what it holds.

    Examples
    --------
    >>> print(recheck("self", "k(self->handle, x)", "Py_DECREF(x_arr);"),
    ...       end="")
        if (!self->handle) {
            Py_DECREF(x_arr);
            PyErr_SetString(PyExc_RuntimeError, "destroyed");
            return NULL;
        }
    >>> print(recheck("w", "k(w->state)", "", "w is gone"), end="")
        if (w->destroyed) {
            PyErr_SetString(PyExc_RuntimeError, "w is gone");
            return NULL;
        }
    >>> recheck("self", "k(self->x)", "")
    ''
    """
    if owner == "self":
        if "self->handle" not in call_expr:
            return ""
        cond = "!self->handle"
    elif f"{owner}->state" in call_expr:
        cond = f"{owner}->destroyed"
    else:
        return ""
    release = release.strip()
    rel = f"{indent}    {release}\n" if release else ""
    return (
        f"{indent}if ({cond}) {{\n"
        f"{rel}"
        f'{indent}    PyErr_SetString(PyExc_RuntimeError, "{gone}");\n'
        f"{indent}    return NULL;\n"
        f"{indent}}}\n"
    )


#: An object's `recheck` block, as `_docsync` reads it on a comment-and-
#: string-masked body: the guard, whatever it releases, the refusal.
#: Anchored on the guard's CONDITION and block, not its message: strings are
#: masked there, and `recheck`'s *gone* varies. The two conditions are the
#: two `recheck` renders (an object's, a wrapper's); a test holds this to
#: `recheck`'s own output, so there is one spelling of the guard.
_RECHECK_BLOCK_RE = re.compile(
    r"if\s*\(\s*(?:!self->handle|\w+->destroyed)\s*\)\s*\{[^{}]*\}"
)


def strip_rechecks(code: str) -> str:
    """*code* without the rechecks, keeping the method's own top guard.

    `_docsync` reads a body as "raising" when it holds more than one
    ``PyErr_SetString`` -- the top guard is the one every wrapper has. A
    recheck (gh-2273) is jm's own guard, not a result shape the manifest
    asked for, so it is removed before that count, as the output-size guard
    is (gh-1710): a fragment rendered before it has none, and would
    otherwise be told "the manifest's result shape needs raises". The FIRST
    guard in a body is the top one and stays.

    Examples
    --------
    >>> body = ("if (!self->handle) { PyErr_SetString(E, ' '); return NULL; }"
    ...         " k(); if (!self->handle) { Py_DECREF(a);"
    ...         " PyErr_SetString(E, ' '); return NULL; } k2();")
    >>> strip_rechecks(body).count("PyErr_SetString")
    1
    >>> strip_rechecks("k();")
    'k();'
    """
    seen = 0

    def _drop(m: "re.Match[str]") -> str:
        nonlocal seen
        seen += 1
        return m.group(0) if seen == 1 else ""

    return _RECHECK_BLOCK_RE.sub(_drop, code)


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
