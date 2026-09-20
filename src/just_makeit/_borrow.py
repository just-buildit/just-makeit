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

Why a NULL needs a table (gh-1418 part 2)
-----------------------------------------
A borrow reports failure by returning NULL, and that is the *whole* signal —
there is no count to inspect and no rc to print. jm's answer was one blanket
``ValueError("<name> failed")``, which is wrong for the shape a borrow is
generated for: a blocking ``wait(n)`` gives up for reasons the caller must
tell apart. End-of-stream is not an error a consumer loop wants to catch as
``ValueError``; a Ctrl-C is not bad input.

So the author declares ``status_fn`` — one C function that owns the
precedence — and rows mapping its result to a jm error category. Two things
make this a table rather than a second ``error``/``error_message`` pair:

- **the status function takes the borrow's count argument**, not just the
  state, because "``n`` can never be satisfied" is a property of
  ``(state, n)``;
- **the table is per METHOD**, since one object may lend through two borrows
  that read the same status differently — a blocking ``wait`` can never see
  "pending", while for a non-blocking ``peek`` that is the normal answer.

It **composes with** ``none_on_empty`` rather than replacing it: on NULL the
binding checks signals, looks the status up, and a status with no row falls
through to ``none_on_empty`` if set, else to today's blanket raise. That is
what lets ``peek`` share one table with ``wait`` and differ only in the row
it declines to write.

``PyErr_CheckSignals()`` comes first and is emitted for *every* borrow,
declared table or not. It is generic — a kernel that blocked with the GIL
released is exactly where a pending signal accumulates — and gating it behind
the table would leave a borrow that declares none swallowing Ctrl-C.
"""

from __future__ import annotations

from . import _config as C
from ._context._diagnostics import format_raise_c, placeholders


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


#: The binding-side record of the last borrow's count. Binding-side and not
#: C-side on purpose: it exists for a Python convenience, and the ring should
#: not pay a store on every lend to provide it.
RELEASE_FIELD = "_jm_borrowed"


def releases(m: dict) -> list[str]:
    """The borrows *m* releases, in declaration order (gh-1426 A).

    A borrow's contract is "valid until the author's own release call", and
    until now jm stated that in prose and knew nothing about the call. Naming
    it is what lets the release's count DEFAULT to the outstanding borrow's,
    which is the whole ergonomic point: `view = buf.wait(512); buf.consume()`.

    **Declared on the releasing method, listing the borrows** -- rather than
    a bare `release = true` -- because the relationship is then checkable,
    and because one object has two releases that differ: `consume(n)` takes a
    count and `reset()` does not. Under a bare flag those two would have to
    mean different things with nothing in the key saying which.

    Examples
    --------
    >>> releases({"name": "consume", "releases": ["wait", "peek"]})
    ['wait', 'peek']
    >>> releases({"name": "step"})
    []
    """
    return [str(r) for r in (m.get("releases") or [])]


def is_release(m: dict) -> bool:
    """True when *m* releases at least one borrow."""
    return bool(releases(m))


def release_count_param(m: dict) -> str:
    """Name of the release's count param, or ``""`` when it takes none.

    The same rule as `count_param`, deliberately worded the same way:
    defaulted from the sole param, named with ``release_count`` when there is
    more than one. A release that takes NO param is not an error -- that is
    ``reset()``, which invalidates whatever is outstanding and has no count
    to default.

    Examples
    --------
    >>> release_count_param({"releases": ["wait"], "params": [{"name": "n"}]})
    'n'
    >>> release_count_param({"releases": ["wait"]})
    ''
    >>> release_count_param({"releases": ["wait"], "release_count": "k",
    ...                      "params": [{"name": "k"}, {"name": "flags"}]})
    'k'
    """
    declared = str(m.get("release_count", "") or "").strip()
    if declared:
        return declared
    params = m.get("params") or []
    if len(params) == 1:
        return str(params[0].get("name", ""))
    return ""


def release_resolve_c(m: dict) -> str:
    """Resolve the release's count and clear the record (gh-1426 A).

    Emitted between the parse block and the call. Three shapes in one, and
    the middle one is the feature:

    - **no count param** (``reset()``): clear the record, nothing else.
    - **omitted argument**: the parsed local is ``0`` (the render-time
      default), so it takes the outstanding count.
    - **nothing outstanding and no argument**: a ``RuntimeError`` naming the
      method. The hand-written binding this replaces consumed 0 silently;
      a release with no borrow behind it is a caller bug, and saying so is
      kinder than a no-op that looks like it worked.

    An explicit ``0`` is indistinguishable from an omitted argument here, and
    deliberately so: releasing nothing is the same caller bug either way, and
    a sentinel to tell them apart would buy a distinction with no behaviour
    behind it.

    **The record is cleared BEFORE the call**, not after. It is a convenience
    default and never a check -- `consume(k)` with ``k < n`` stays legal for
    overlapped frames -- so a failing release leaving it clear costs the next
    bare call its default and nothing else. Clearing it after would mean
    threading this through every return shape for that.

    Examples
    --------
    >>> print(release_resolve_c(
    ...     {"name": "consume", "releases": ["wait"],
    ...      "params": [{"name": "n", "type": "size_t"}]}), end="")
        if (!n) {
            n = self->_jm_borrowed;
            if (!n) {
                PyErr_SetString(PyExc_RuntimeError,
                    "consume() has no outstanding borrow to release; "
                    "pass a count, or call it after a borrow");
                return NULL;
            }
        }
        self->_jm_borrowed = 0;
    >>> print(release_resolve_c(
    ...     {"name": "reset", "releases": ["wait"]}), end="")
        self->_jm_borrowed = 0;
    """
    if not is_release(m):
        return ""
    count = release_count_param(m)
    clear = f"    self->{RELEASE_FIELD} = 0;\n"
    if not count:
        return clear
    name = str(m.get("name", "<method>"))
    return (
        f"    if (!{count}) {{\n"
        f"        {count} = self->{RELEASE_FIELD};\n"
        f"        if (!{count}) {{\n"
        f"            PyErr_SetString(PyExc_RuntimeError,\n"
        f'                "{name}() has no outstanding borrow to release; "\n'
        f'                "pass a count, or call it after a borrow");\n'
        f"            return NULL;\n"
        f"        }}\n"
        f"    }}\n" + clear
    )


def is_release_count(m: dict, pname: str) -> bool:
    """Whether *pname* is the param a release defaults from (gh-1426 A).

    Asked by BOTH `.pyi` writers, which is the whole reason it is a
    function: one renders from the render-time context and the other from
    the manifest, so a rule spelled twice is a stub that disagrees with
    its sibling about whether an argument is required.

    Examples
    --------
    >>> m = {"releases": ["wait"], "params": [{"name": "n"}]}
    >>> is_release_count(m, "n"), is_release_count(m, "flags")
    (True, False)
    """
    return is_release(m) and pname == release_count_param(m)


def record_count_c(m: dict) -> str:
    """Remember this borrow's count, for a release to default to.

    Emitted once, where the view is built -- so nothing re-derives it, and a
    borrow jm did not generate cannot be mistaken for one it did.
    """
    return f"    self->{RELEASE_FIELD} = (size_t)({count_param(m)});\n"


def status_fn(m: dict) -> str:
    """Name of the C function reporting WHY a borrow returned NULL, or ``""``.

    Examples
    --------
    >>> status_fn({"borrow": True, "status_fn": "dp_f32_wait_status"})
    'dp_f32_wait_status'
    >>> status_fn({"borrow": True})
    ''
    """
    return str(m.get("status_fn", "") or "").strip()


def status_rows(m: dict) -> list[dict]:
    """The declared ``status -> exception`` rows, in declaration order.

    Order is preserved rather than sorted: it is the order the cases are
    emitted in, and an author reading the generated C beside the manifest
    should find them in the same sequence.

    Examples
    --------
    >>> status_rows({"status_errors": [{"status": "DP_WAIT_CLOSED",
    ...                                 "error": "EOFError"}]})
    [{'status': 'DP_WAIT_CLOSED', 'error': 'EOFError'}]
    >>> status_rows({"borrow": True})
    []
    """
    return list(m.get("status_errors") or [])


def parse_status_error(spec: str) -> dict:
    """``STATUS:ExcName[:message]`` -> a row, for ``--status-error``.

    Split on the first two colons only, so a message may contain them
    ("closed: nothing left to read"). The same shape `_record` uses for
    ``--result-field name:type[:doc]``, and for the same reason: a repeatable
    multi-attribute declaration is a colon spec here, never a parallel family
    of flags.

    Examples
    --------
    >>> parse_status_error("DP_WAIT_CLOSED:EOFError")
    {'status': 'DP_WAIT_CLOSED', 'error': 'EOFError'}
    >>> parse_status_error("DP_WAIT_TOO_LARGE:ValueError:n exceeds capacity")
    {'status': 'DP_WAIT_TOO_LARGE', 'error': 'ValueError', \
'message': 'n exceeds capacity'}
    """
    parts = spec.split(":", 2)
    if len(parts) < 2:
        raise SystemExit(
            f"--status-error {spec!r}: expected STATUS:ExcName[:message].\n"
            f"  STATUS is the C constant your status function returns, "
            f"ExcName a Python exception\n"
            f"  class (e.g. DP_WAIT_CLOSED:EOFError)."
        )
    row = {"status": parts[0].strip(), "error": parts[1].strip()}
    if len(parts) == 3 and parts[2].strip():
        row["message"] = parts[2].strip()
    return row


def _is_c_identifier(text: str) -> bool:
    """Whether *text* can name a C enum constant.

    Deliberately not ``str.isidentifier``: that accepts non-ASCII letters
    Python allows and a C compiler does not, so the refusal would arrive from
    the author's compiler instead of from jm.
    """
    return (
        bool(text)
        and not text[0].isdigit()
        and all(c.isascii() and (c.isalnum() or c == "_") for c in text)
    )


#: How a resolved slot reaches `PyErr_Format`. Two conversions, not one per
#: C type: `_rc_raise_c` already established casting to the wide type rather
#: than carrying a printf map beside `_CTYPE_META`, and the rendered text is
#: the same either way. A `complex` slot has no scalar reading and is
#: refused rather than given a third.
_SLOT_CONVERSIONS = {
    "int": ("%lld", "(long long)"),
    "float": ("%g", "(double)"),
}


def _slot_for(ctype: str, expr: str) -> "tuple[str, str] | None":
    """``(conversion, argument)`` for reading *expr* of type *ctype*."""
    from ._types import _CTYPE_META

    kind = str(_CTYPE_META.get(ctype, {}).get("kind", ""))
    if kind not in _SLOT_CONVERSIONS:
        return None
    conversion, cast = _SLOT_CONVERSIONS[kind]
    return conversion, f"{cast}{expr}"


def _property_read(prop: dict, component: str) -> str:
    """The C expression reading *prop*, or ``""`` if jm cannot read it here.

    Only the two scalar backings: a plain accessor property reads through the
    getter jm declared for it, and a ``field`` one reads the struct member.
    Every other backing (`buf_field`, a codec, a container, a capsule) is a
    value with no scalar reading, so it is refused by name rather than given
    an expression that happens to compile.
    """
    name = str(prop.get("name", ""))
    unsupported = ("buf_field", "codec", "capsule", "count_fn", "entry_fn")
    if any(prop.get(k) for k in unsupported):
        return ""
    if prop.get("field"):
        return f"self->handle->{prop['field']}"
    return f"{component}_get_{name}(self->handle)"


def message_slots(
    m: dict, component: str = "", properties: "list[dict] | None" = None
) -> "dict[str, tuple[str, str]]":
    """What a status message's ``{name}`` slots may refer to (gh-1426 C).

    Two scopes, because those are the two the author already declared and
    jm already reads: the method's own **params** -- `{n}` is the count the
    caller passed -- and the object's **properties**, which is where a
    capacity lives. Both are in scope at the raise: a param is a local in
    the wrapper, a property reads through `self->handle`.

    Examples
    --------
    >>> slots = message_slots(
    ...     {"name": "wait", "params": [{"name": "n", "type": "size_t"}]},
    ...     "ring", [{"name": "capacity", "type": "size_t"}])
    >>> slots["n"]
    ('%lld', '(long long)n')
    >>> slots["capacity"]
    ('%lld', '(long long)ring_get_capacity(self->handle)')
    """
    out: dict[str, tuple[str, str]] = {}
    for p in m.get("params") or []:
        slot = _slot_for(str(p.get("type", "")), str(p.get("name", "")))
        if slot:
            out[str(p.get("name", ""))] = slot
    for prop in properties or []:
        expr = _property_read(prop, component)
        if not expr:
            continue
        slot = _slot_for(str(prop.get("type", "")), expr)
        if slot:
            out[str(prop.get("name", ""))] = slot
    return out


def status_dispatch_c(
    m: dict,
    state_expr: str = "self->handle",
    component: str = "",
    properties: "list[dict] | None" = None,
) -> str:
    """The NULL-path dispatch for a borrow: signals, then the status table.

    Emitted inside the wrapper's ``if (!_p) {`` block, ahead of the fallback
    the caller supplies (``Py_RETURN_NONE`` for ``none_on_empty``, else the
    blanket raise). Every row ``return``s, so falling out of the ``switch``
    IS the fallback — no row for a status means "jm was told nothing about
    this one", which is precisely when the old behaviour is still right.

    ``switch`` on the call rather than a stored local: the status type is the
    author's enum, named in a header jm never reads, so there is no type to
    declare the local with. ``default:`` also silences ``-Wswitch`` for the
    enumerators the table declines to handle.

    The raise itself is `empty_raise_c` — the same emitter the blanket
    refusal uses, for the reason its docstring gives: a borrow fails by
    returning NULL and there is no code to print, so ``PyErr_SetString`` is
    the honest form and the author's text stays an argument.

    Emitted at the eight-space body indent of that block, which is where
    `empty_raise_c` already puts its statements — so the rows and the
    fallback beneath them line up without this reindenting a shared emitter
    that three other shapes depend on.

    Examples
    --------
    >>> print(status_dispatch_c({
    ...     "name": "wait", "borrow": True, "params": [{"name": "n"}],
    ...     "status_fn": "dp_f32_wait_status",
    ...     "status_errors": [{"status": "DP_WAIT_CLOSED",
    ...                        "error": "EOFError",
    ...                        "message": "the ring closed"}]}), end="")
            if (PyErr_CheckSignals()) return NULL;
            switch (dp_f32_wait_status(self->handle, n)) {
            case DP_WAIT_CLOSED:
            PyErr_SetString(PyExc_EOFError,
            "the ring closed");
            return NULL;
            default: break;
            }
    """
    # gh-1418: unconditional, table or not. A borrow that blocked with the
    # GIL released is exactly where a pending signal is waiting, and a
    # borrow that declares no table would otherwise swallow Ctrl-C and
    # report it as bad input.
    out = "        if (PyErr_CheckSignals()) return NULL;\n"
    rows = status_rows(m)
    if not rows or not status_fn(m):
        return out
    name = str(m.get("name", "<method>"))
    out += (
        f"        switch ({status_fn(m)}({state_expr}, {count_param(m)})) {{\n"
    )
    slots = message_slots(m, component, properties)
    for row in rows:
        message = str(row.get("message", "") or "").strip()
        # gh-1426 C: `format_raise_c` falls back to `empty_raise_c` when the
        # message references nothing, so the static case keeps exactly one
        # spelling rather than a second that happens to agree today.
        out += f"        case {row['status']}:\n" + format_raise_c(
            str(row["error"]),
            message or f"{name} failed ({row['status']})",
            slots,
            indent=8,
        )
    return out + "        default: break;\n        }\n"


def why_not(
    m: dict,
    component: str = "",
    properties: "list[dict] | None" = None,
) -> str:
    """Why this borrow declaration cannot be generated, or ``""``.

    Returns prose for the author, in the shape jm's other refusals use: what
    is wrong, and the one edit that fixes it. A bare ``False`` would make
    "jm will not" and "jm cannot" look alike (the ``_outbuf.why_not``
    lesson).
    """
    name = m.get("name", "<method>")
    if not is_borrow(m):
        # gh-1418: the keys are a BORROW's, and a borrow's only failure
        # signal is the NULL they read. Accepted on any other shape they
        # would be recorded, exit 0, and dropped -- which is the whole
        # finding of this issue, one shape over.
        if status_fn(m) or status_rows(m):
            return (
                f"method '{name}': `status_fn` / `status_errors` describe "
                f"why a BORROW returned NULL.\n"
                f"  This method is not a borrow, so there is no NULL to "
                f"explain. Declare `borrow`, or drop\n"
                f"  the keys -- a method that reports failure another way "
                f"uses `error` / `error_message`."
            )
        return ""
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
    return _why_not_status(m, str(name), component, properties)


def why_not_release(m: dict, methods: "list[dict] | None" = None) -> str:
    """Why this RELEASE declaration cannot be generated, or ``""`` (gh-1426 A).

    Separate from :func:`why_not` because it asks about a *relationship*: the
    names in ``releases`` are other methods, so it needs the object's method
    list and `why_not` does not. That is the check the list-of-names spelling
    buys over a bare flag, and the reason the spelling was chosen.
    """
    named = releases(m)
    if not named:
        if m.get("release_count"):
            return (
                f"method '{m.get('name', '<method>')}': `release_count` "
                f"names the param a RELEASE defaults from, and this method "
                f"releases nothing.\n"
                f"  Declare `releases` with the borrow(s) it ends, or drop "
                f"`release_count`."
            )
        return ""
    name = str(m.get("name", "<method>"))
    if name in named:
        return (
            f"method '{name}': `releases` names itself.\n"
            f"  A release ends a BORROW; a method cannot be both ends of "
            f"that contract."
        )
    by_name = {str(x.get("name", "")): x for x in (methods or [])}
    for target in named:
        if methods is not None and target not in by_name:
            return (
                f"method '{name}': `releases` names '{target}', which is "
                f"not a method of this object.\n"
                f"  Declare it, or correct the name -- a release of nothing "
                f"is a contract with one party."
            )
        if methods is not None and not is_borrow(by_name[target]):
            return (
                f"method '{name}': `releases` names '{target}', which is "
                f"not a borrow.\n"
                f"  Only a `--borrow` hands out a view that needs releasing; "
                f"a method returning a copy\n  has nothing outstanding."
            )
    params = m.get("params") or []
    declared = str(m.get("release_count", "") or "").strip()
    if declared and declared not in [str(p.get("name", "")) for p in params]:
        return (
            f"method '{name}': `release_count` names '{declared}', which is "
            f"not one of its params.\n"
            f"  Declare a param of that name, or point `release_count` at an "
            f"existing one."
        )
    if declared and params and str(params[-1].get("name", "")) != declared:
        # Not a jm preference: the release's count becomes an OPTIONAL
        # parameter, and a defaulted parameter cannot precede a required
        # one -- in the generated signature, in the `.pyi`, or in Python
        # itself. jm's own stub writer refuses the result, so the refusal
        # belongs here where it can name the fix.
        return (
            f"method '{name}': `release_count` names '{declared}', which is "
            f"not the LAST param.\n"
            f"  A release's count is optional, and an optional parameter "
            f"cannot come before a required one.\n"
            f"  Declare '{declared}' last."
        )
    if len(params) > 1 and not declared:
        names = ", ".join(str(p.get("name", "?")) for p in params)
        return (
            f"method '{name}': `releases` cannot tell which of "
            f"{len(params)} params is the count ({names}).\n"
            f"  Name it with `release_count`. The same rule as "
            f"`borrow_count`: defaulted from a sole param,\n  never guessed "
            f"among several."
        )
    return ""


def _why_not_status(
    m: dict,
    name: str,
    component: str = "",
    properties: "list[dict] | None" = None,
) -> str:
    """Why this borrow's status table cannot be generated, or ``""``.

    Split from :func:`why_not` only for length; it is the same refusal
    surface and is reached from there on every face.
    """
    fn, rows = status_fn(m), status_rows(m)
    slots = message_slots(m, component, properties)
    # Each half is inert without the other, and inert is the state gh-1418
    # is about: a key recorded, accepted with exit 0, and dropped. Refusing
    # both directions is what keeps "declared" and "honoured" the same word.
    if rows and not fn:
        return (
            f"method '{name}': `status_errors` has nothing to read the "
            f"status FROM.\n"
            f"  Name the C function with `status_fn` -- it is called as "
            f"`fn(state, {count_param(m)})` after a\n"
            f"  NULL, and its return is what these rows match."
        )
    if fn and not rows:
        return (
            f"method '{name}': `status_fn` is declared and no "
            f"`status_errors` row reads it.\n"
            f"  jm would call '{fn}' and discard the answer. Declare a row "
            f"(`--status-error STATUS:ExcName`),\n"
            f"  or drop `status_fn`."
        )
    if fn and not _is_c_identifier(fn):
        return (
            f"method '{name}': `status_fn` '{fn}' is not a C function name.\n"
            f"  It is emitted as a call in the generated binding, so it must "
            f"be a plain identifier."
        )
    seen: set[str] = set()
    for row in rows:
        status = str(row.get("status", "") or "").strip()
        error = str(row.get("error", "") or "").strip()
        if not status or not error:
            return (
                f"method '{name}': every `status_errors` row needs both "
                f"`status` and `error`.\n"
                f"  Got status={status or '<missing>'!r}, "
                f"error={error or '<missing>'!r}. The row maps one C "
                f"constant\n  to one Python exception."
            )
        if not _is_c_identifier(status):
            # The `0` case gh-1418 calls out lands here too, and says so:
            # a status function answers 0 for SUCCESS, which cannot be the
            # answer after a NULL, so a row for it can only ever be dead C.
            extra = (
                "\n  `0` is what a status function answers for SUCCESS, and "
                "a borrow only asks after a NULL,\n  so that row could never "
                "fire."
                if status == "0"
                else ""
            )
            return (
                f"method '{name}': `status_errors` status '{status}' is not "
                f"a C constant name.\n"
                f"  It is emitted as a `case` label, so it must be the "
                f"enumerator your status function\n"
                f"  returns (e.g. DP_WAIT_CLOSED).{extra}"
            )
        if error not in C.ERROR_CATEGORIES:
            supported = ", ".join(sorted(C.ERROR_CATEGORIES))
            return (
                f"method '{name}': `status_errors` names exception "
                f"'{error}', which jm does not emit.\n"
                f"  Choose one of: {supported}."
            )
        unknown = [
            slot
            for slot in placeholders(str(row.get("message", "") or ""))
            if slot not in slots
        ]
        if unknown:
            # gh-1426 C: an unresolved slot would otherwise reach the author
            # as a literal `{capacity}` in a runtime message -- the quietest
            # possible failure, since the raise still works and only the one
            # number anybody wanted is missing.
            available = ", ".join(sorted(slots)) or "(none)"
            return (
                f"method '{name}': `status_errors` message references "
                f"{{{unknown[0]}}}, which is not in scope.\n"
                f"  A message may name this method's params and this "
                f"object's properties. Available here: {available}.\n"
                f"  A property backed by a buffer, a codec or a capsule has "
                f"no scalar reading and cannot be named."
            )
        if status in seen:
            # Two `case` labels of one value is a compile error in the
            # author's build rather than a jm diagnostic -- and the author
            # would be reading generated C to find out which row lost.
            return (
                f"method '{name}': `status_errors` maps '{status}' twice.\n"
                f"  One status has one exception; the second row would be an "
                f"unreachable `case` label."
            )
        seen.add(status)
    return ""
