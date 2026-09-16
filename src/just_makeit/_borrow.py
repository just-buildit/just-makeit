"""_borrow.py — a method that returns a BORROWED view, and what it needs.

gh-1312. Every other array-returning shape jm generates hands back memory
*somebody allocated for the call*: NumPy allocates it, or the caller supplies
it as ``out=``. A borrow is the other arrangement — the C state already owns
the memory, the kernel returns a pointer into it, and the binding wraps that
pointer without copying.

jm could already emit exactly that, from two places, and both are
**accessors**: the ``buf_field`` property and an array state's
``get_<name>_view()`` (see :func:`_context._parse.borrow_view_c`, which they
now share). There was no *method* form, so a kernel whose whole point is
``give me a pointer to n samples`` had no declaration.

Why it is worth generating
--------------------------
The C is cheap; the binding is not. Pinning the right owner, checking the
steal, stating the lifetime contract identically in the runtime docstring and
the stub, and knowing what ``destroy()`` does to an outstanding view — every
adopter re-derives all of it, and the failure mode of getting it wrong is a
silent use-after-free rather than a build error. That is the argument for one
emitter, and it holds at a single instance.

What the contract is, and what it is not
----------------------------------------
**A borrow is a usage contract.** The returned array is valid until the
author's own release call; using it afterwards reads whatever the producer
has written since. jm *states* that contract on both faces and does not try
to enforce it, because in CPython it cannot: there is no hook on element
access, and every scheme that refuses to recycle while a view is outstanding
(a buffer-protocol export count, a refcounted token) refuses **correct**
idiomatic code, since the caller's own name for the previous view is still
bound at every point the producer could check. Measured on gh-1312.

This is deliberately *not* the shape ``docs/memory-ownership.md`` rule 3
forbids. That rule was derived from gh-219 (a grow-on-demand buffer that
``free()``d under an outstanding view — a genuine use-after-free) and gh-437
(the *next call* overwriting the same buffer in place). A borrow over a
fixed mapping has neither: nothing is reallocated, so the worst case is stale
values rather than a dangling pointer, and the invalidating call is the
consumer's own explicit release rather than an unrelated later call.

The element type, and integer IQ
--------------------------------
The view's element type is the method's ``return_type``, and it must be one
``_CTYPE_META`` registers -- so there is no complex-integer borrow, because
there is no complex-integer type at all. C's ``_Complex`` is float-only and
numpy has no complex-integer dtype, so ``int16_t _Complex`` has nothing to
convert through; the CLI and ``jm apply`` both refuse it by name.

Integer IQ is borrowed as a **two-field record** -- ``record_dtype`` over
``int16_t i, q``, i.e. ``[('i','<i2'),('q','<i2')]``. Decided on gh-1310
after measuring all four zero-copy spellings of the same bytes; the two
worth remembering as rejected are 1-D interleaved ``int16`` of length ``2n``
(no longer one element per sample) and 1-D packed ``int32``, which is
**silently wrong** -- ``d + 1`` increments I only, because int32 addition
carries across the I/Q boundary. The structured form refuses arithmetic
loudly instead, and gh-1314 tracks giving integer IQ the operations
``complex64`` gets.

This is also why ``elements_per_sample`` (gh-805 §C) is not extended to a
borrow. It is a param-side key -- ``_coerce.array_len_c`` uses it to divide
an *incoming* array's length -- and the interleaving it describes is the
representation gh-1310 rejected for this direction, so reaching for it here
would re-introduce a measured wrong answer rather than fill a gap.

The count
---------
The view's length is a *declared param*, because the kernel is told how many
elements to hand back (``wait(n)``). ``borrow_count`` names it; with exactly
one param that name is obvious and is defaulted. With several it is not, and
a wrong guess sizes a view over memory the state does not own — so it is
required rather than inferred.
"""

from __future__ import annotations


def is_borrow(m: dict) -> bool:
    """True when *m* returns a borrowed view over the C state's memory.

    Examples
    --------
    >>> is_borrow({"name": "wait", "borrow": True})
    True
    >>> is_borrow({"name": "steps"})
    False
    """
    return bool(m.get("borrow"))


def is_writeable(m: dict) -> bool:
    """Whether the borrowed view is writeable. Default: **no**.

    A consumer holding a view into a producer's region should not be writing
    through it, so read-only is the default here — unlike the ``buf_field``
    property, whose writeable behaviour predates this and is preserved. The
    two answers are stated rather than shared; see the asymmetry note in
    ``docs/memory-ownership.md``.

    Examples
    --------
    >>> is_writeable({"borrow": True})
    False
    >>> is_writeable({"borrow": True, "borrow_writeable": True})
    True
    """
    return bool(m.get("borrow_writeable"))


def count_param(m: dict) -> str:
    """Name of the param carrying the view's element count, or ``""``.

    Defaulted from the sole param when there is exactly one, because
    ``wait(n)`` leaves no room for doubt. Never guessed when there are
    several: sizing a borrowed view from the wrong argument produces an array
    reaching past what the state owns, which reads as data rather than as an
    error.

    Examples
    --------
    >>> count_param({"borrow": True, "params": [{"name": "n"}]})
    'n'
    >>> count_param({"borrow": True, "borrow_count": "how_many",
    ...              "params": [{"name": "how_many"}, {"name": "flags"}]})
    'how_many'
    >>> count_param({"borrow": True, "params": [{"name": "a"},
    ...                                         {"name": "b"}]})
    ''
    """
    declared = str(m.get("borrow_count", "") or "").strip()
    if declared:
        return declared
    params = m.get("params") or []
    if len(params) == 1:
        return str(params[0].get("name", ""))
    return ""


def element_type(record_dtype: object, return_type: str) -> str:
    """The C type of ONE element of the borrowed view.

    gh-1310. With ``record_dtype`` the element is the author's POD struct, so
    it names the pointer the kernel returns (``iq_pair_t *ring_wait(...)``),
    the cast the binding makes, and the dtype the view carries. Without it the
    element is the method's ``return_type``.

    One home because three faces ask: the sacred ``_core.h`` prototype, the
    ``_core.c`` stub written against it, and the binding that wraps the
    pointer. jm's record history is three copies of one rule drifting until
    the declaration described a kernel the binding never called -- the same
    reason :func:`_record.is_record_array` was extracted.

    The ``[]`` strip is here rather than at the call sites because a borrow's
    ``return_type`` is stored scalar but reaches this through paths that
    spell it either way.

    Examples
    --------
    >>> element_type("iq_pair_t", "float _Complex")
    'iq_pair_t'
    >>> element_type("", "float _Complex")
    'float _Complex'
    >>> element_type("", "int16_t[]")
    'int16_t'
    """
    if record_dtype:
        return str(record_dtype)
    return return_type[:-2] if return_type.endswith("[]") else return_type


def why_not(m: dict) -> str:
    """Why this borrow declaration cannot be generated, or ``""``.

    Returns prose for the author, in the shape jm's other refusals use: what
    is wrong, and the one edit that fixes it. A bare ``False`` would make
    "jm will not" and "jm cannot" look alike (the ``_outbuf.why_not``
    lesson).
    """
    if not is_borrow(m):
        return ""
    name = m.get("name", "<method>")
    params = m.get("params") or []
    if m.get("variable_output"):
        return (
            f"method '{name}': `borrow` and `variable_output` are different "
            f"answers to who owns the result.\n"
            f"  `variable_output` fills a buffer sized for the call; "
            f"`borrow` hands back a pointer\n"
            f"  into memory the state already owns. Declare one."
        )
    if m.get("out_type"):
        return (
            f"method '{name}': `borrow` has no `out_type`.\n"
            f"  `out_type` names a buffer the kernel WRITES; a borrowed view "
            f"is read from memory that\n"
            f"  already exists. The element type is the method's "
            f"`return_type`."
        )
    if not params:
        return (
            f"method '{name}': `borrow` needs a param carrying the element "
            f"count.\n"
            f"  The kernel is told how many elements to lend "
            f"(`wait(n)`), and that count sizes the view."
        )
    if not count_param(m):
        names = ", ".join(str(p.get("name", "?")) for p in params)
        return (
            f"method '{name}': `borrow` cannot tell which of {len(params)} "
            f"params is the count ({names}).\n"
            f"  Name it with `borrow_count`. Sizing a borrowed view from the "
            f"wrong argument reaches past\n"
            f"  what the state owns and reads as data rather than as an "
            f"error, so it is not guessed."
        )
    if count_param(m) not in [str(p.get("name", "")) for p in params]:
        return (
            f"method '{name}': `borrow_count` names "
            f"'{count_param(m)}', which is not one of its params.\n"
            f"  Declare a param of that name, or point `borrow_count` at an "
            f"existing one."
        )
    return ""
