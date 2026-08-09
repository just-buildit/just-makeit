"""_diagnostics — context builders for declarative Python-level diagnostics.

`make_warnings_ctx` (gh-481) emits a ``PyErr_WarnEx`` guarded by a bool field
on the state struct, right after a *successful* construction.

Why it has to be generated rather than hand-written: C has no channel for
"succeeded, but with a caveat". ``create()`` returns non-NULL or it doesn't, so
a Python warning is literally unrepresentable in C and could only ever be
hand-patched into the ``_ext.c`` glue. That glue is regenerated wholesale from
the manifest, and jm's own sanctioned way to pick up a new declarative field on
an existing object is to delete the fragment and let ``jm apply`` recreate it —
which silently dropped the patch, with no diagnostic. Declaring the warning
here makes it survive that round-trip like any other generated boilerplate.

`make_errors_ctx` (gh-482) is its sibling: translating a ``create()`` failure
into the exception the component actually meant, instead of the blanket
``MemoryError`` the glue used to hardcode. Not an expressiveness gap — C can
already signal failure by returning NULL. The gap was that jm mistranslated the
failure on arrival, reporting a bad-parameter refusal as an allocation error.
So the fix lives in the translation, not in a new post-construction hook.

The two are siblings, not twins, and the asymmetry is deliberate:

- A warning is a **table array** (`[[<comp>.warnings]]`): a component can have
  any number of best-effort caveats, each keyed on a different state field.
- An error is a **scalar pair** (`create_error` / `create_error_message`):
  ``create()`` has exactly one failure channel — a NULL return — so there is
  exactly one translation to declare.

That single channel is also this feature's known limit. NULL is NULL: with
`create_error` declared, *every* create() failure reports as that category,
including a genuine allocation failure. Distinguishing reasons would need an
err out-param, which changes ``create()``'s signature in the sacred
``_core.h``/``_core.c`` and requires the component to set the code itself. That
was scoped out deliberately — see the `gh-482-errors-wip` branch. Both builders
here stay pure glue: no sacred file is touched by either.
"""

from __future__ import annotations

import re

# Message prose is authored by a human and lands inside a C string literal.
_C_ESCAPES = str.maketrans(
    {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}
)

# A bare identifier names a bool-ish field on the state struct and is reached
# through the handle for the author. Anything else is taken as a complete C
# expression (gh-601) — see `condition_expr`.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def condition_expr(condition: str) -> str:
    """The C a warning's ``if`` tests, from its declared *condition*.

    gh-601. A property's ``expr`` takes arbitrary C and a warning's condition
    had to be a bare identifier, and the strict one is the one that needed to
    be flexible: the shape that must reach through a pointer is the
    **forwarder** — an object whose state struct is a handle onto a shared
    engine::

        typedef struct {
            acq_state_t *engine;
        } burst_acq_state_t;

    There is no bool field on that struct and there never will be, since
    adding one would duplicate state that already exists on the engine and
    have to be kept in sync. So every property on such an object goes through
    ``expr``, and its one warning was the only thing in the file that could
    not be declared at all — leaving a hand-written block in an otherwise
    fully generated fragment, to be re-applied by hand after every
    regeneration. The reporter lost it once already, and a regeneration is
    exactly when nobody is looking for a missing runtime warning.

    A bare identifier keeps its existing meaning, so every manifest written
    before this renders byte-for-byte as it did:

    >>> condition_expr("underpowered")
    'self->handle->underpowered'

    Anything else is used verbatim, exactly as ``expr`` is — the author
    supplies the whole reach, and jm does not guess where it starts:

    >>> condition_expr("self->handle->engine->underpowered")
    'self->handle->engine->underpowered'
    """
    return (
        f"self->handle->{condition}" if _IDENT.match(condition) else condition
    )


# Project-wide line budget (CLAUDE.md: 79 cols for all languages).
_MAX_COL = 79


def _c_string_literal(message: str, indent: int) -> str:
    """Render `message` as one or more adjacent C string literals.

    C concatenates adjacent string literals, so a long message wraps across
    lines without changing its value. Each continuation line is aligned to
    `indent` so the result survives clang-format unchanged.

    Parameters
    ----------
    message : str
        Raw prose. Escaped for C; not otherwise transformed.
    indent : int
        Column at which each literal starts.

    Returns
    -------
    str
        Newline-joined literals, no trailing newline. Always at least one
        literal, so an empty message renders as ``""``.

    Examples
    --------
    >>> _c_string_literal("hi", 4)
    '    "hi"'
    >>> print(_c_string_literal("a" * 80, 4)[:20])
        "aaaaaaaaaaaaaaa
    """
    escaped = message.translate(_C_ESCAPES)
    # Budget: the indent, the two quotes, and (worst case) a trailing comma.
    budget = _MAX_COL - indent - 3
    if budget < 8:  # pathological indent — don't try to be clever
        return f'{" " * indent}"{escaped}"'

    words = escaped.split(" ")
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}" if cur else w
        if cur and len(cand) > budget:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur or not lines:
        lines.append(cur)

    pad = " " * indent
    # Re-add the separating space to every line but the last, so the
    # concatenated literal reads identically to the input.
    out = [
        f'{pad}"{ln}{" " if i < len(lines) - 1 else ""}"'
        for i, ln in enumerate(lines)
    ]
    return "\n".join(out)


def _rc_raise_c(category: str, message: str, indent: int = 21) -> str:
    """The ``PyErr_Format`` that turns a failing return code into an exception.

    Shared by ``status_return``, ``error_negative`` (gh-823 Ask D) and the
    ``exit`` finalizer (gh-805 §H). It lives here, beside `_c_string_literal`,
    rather than in `_methods` — the third caller is in `_destroy`, and a
    teardown emitter reaching into the method renderer for its raise is how
    the two spellings this docstring describes came apart in the first place.
    The callers
    disagree about *which* codes are failures — ``!= 0`` against ``< 0`` — and
    about what happens afterwards, so the test and the success path stay with
    each caller. Everything from the raise inwards is one concept, and it had
    two implementations: ``error_negative`` routed the author's text through
    `_c_string_literal` as an **argument**, while ``status_return`` spliced the
    method name straight into the format string and hard-coded
    ``PyExc_ValueError``.

    That split is the reason ``status_return`` could not carry a message: the
    keys existed, and only one of the two emitters read them.

    Parameters
    ----------
    category : str
        An `_config.ERROR_CATEGORIES` name, already validated at declaration
        time — ``"ValueError"`` when the author declared nothing.
    message : str
        The author's prose, or the derived ``"<name> failed"``. Passed as an
        **argument** to a fixed ``"%s (rc=%lld)"`` format, never as the format
        itself: spliced in as the format, a ``%`` in ordinary prose ("100%
        done") becomes a live conversion with no argument behind it, and
        ``PyErr_Format`` walks off the end of its varargs — on the error path
        only, which is the path least likely to be exercised before a release.
    indent : int
        Continuation-line indent for the rendered literal.

    Notes
    -----
    ``%lld`` + ``(long long)`` rather than ``%d``: ``error_negative``'s return
    may be ``int64_t``, and truncating it mangles precisely the error code
    worth reading. ``status_return``'s ``_rc`` is an ``int``, which widens
    losslessly — so unifying on the wider conversion removes a difference
    rather than parameterising one, and the rendered message is unchanged
    (``"<name> failed (rc=-4)"`` either way).
    """
    return (
        f"        PyErr_Format(PyExc_{category},"
        f' "%s (rc=%lld)",\n'
        f"{_c_string_literal(message, indent)},\n"
        f"                     (long long)_rc);\n"
        f"        return NULL;\n"
    )


def declared_raise(m: dict) -> "tuple[str, str] | None":
    """``(category, message)`` a declared method raises, or ``None``.

    The one reading of the ``status_return`` / ``error_negative`` / ``error``
    / ``error_message`` quartet. `_rc_raise_c` renders the raise from this
    pair and every doc face documents it from the same pair, so a member
    cannot advertise an exception class its binding does not use — which is
    gh-869 in the direction gh-864 did not cover.

    Returns ``None`` for a method whose binding translates no status into an
    exception. That is not "no exception at all": every wrapper still carries
    the ``RuntimeError "destroyed"`` liveness guard, which is jm's plumbing
    rather than the author's contract and is deliberately undocumented here.

    Parameters
    ----------
    m : dict
        One ``[[<comp>.methods]]`` entry.

    Examples
    --------
    >>> declared_raise({"name": "close"}) is None
    True
    >>> declared_raise({"name": "close", "status_return": True})
    ('ValueError', 'close failed')
    >>> declared_raise({"name": "seek", "error_negative": True,
    ...                 "error": "OSError", "error_message": "bad offset"})
    ('OSError', 'bad offset')
    """
    if not (m.get("status_return") or m.get("error_negative")):
        return None
    return (
        m.get("error") or "ValueError",
        m.get("error_message") or f"{m.get('name', '')} failed",
    )


def raises_doc(m: dict) -> "list[tuple[str, str]]":
    """The ``raises=`` argument for a declared method's two doc faces.

    Built from `declared_raise`, so the documented class is by construction
    the class `_rc_raise_c` emits. The description quotes the author's own
    message because that is what a caller sees at the REPL, and states which
    return codes fail — the two shapes disagree there, and a reader cannot
    tell them apart from the signature.

    Examples
    --------
    >>> raises_doc({"name": "write"})
    []
    >>> cat, desc = raises_doc({"name": "write", "status_return": True,
    ...                         "error_message": "short write"})[0]
    >>> cat
    'ValueError'
    >>> desc.startswith('If the C call returns a non-zero status.')
    True
    """
    pair = declared_raise(m)
    if pair is None:
        return []
    category, message = pair
    condition = (
        "a negative value" if m.get("error_negative") else "a non-zero status"
    )
    return [
        (
            category,
            f"If the C call returns {condition}. The exception message is "
            f"``{message}``, with the return code appended (gh-869).",
        )
    ]


def create_raises_doc(category: str, message: str) -> "list[tuple[str, str]]":
    """The ``raises=`` argument for a component's CLASS docstring (gh-805 §F).

    `raises_doc` is the per-method reading of the same question; this is the
    constructor's, from `create_error`/`create_error_message` (gh-482). Both
    exist because jm is the *only* thing that knows the answer: no header
    ``@throws`` can name the exception class jm chose, and no manifest author
    should have to restate it beside the declaration that already implies it.

    Only a **declared** failure is documented. An object with no
    ``create_error`` still raises ``MemoryError`` when ``create()`` returns
    NULL, and that is deliberately left out for the reason `declared_raise`
    leaves out the ``RuntimeError "destroyed"`` liveness guard: it is jm's
    plumbing rather than the author's contract, identical in every generated
    object, and documenting it everywhere would bury the one entry that is
    actually about this component.

    Parameters
    ----------
    category : str
        A name from `_config.ERROR_CATEGORIES`, or ``""`` when undeclared.
    message : str
        The declared exception text. This is quoted verbatim because it is
        what the caller sees at the REPL.

    Examples
    --------
    >>> create_raises_doc("", "")
    []
    >>> cat, desc = create_raises_doc("ValueError", "rate must be positive")[0]
    >>> cat
    'ValueError'
    >>> desc
    'If construction fails. The exception message is ``rate must be positive``.'
    """
    if not category:
        return []
    return [
        (
            category,
            f"If construction fails. The exception message is ``{message}``.",
        )
    ]


def warns_doc(warnings: "list[dict]") -> "list[tuple[str, str]]":
    """The ``warns=`` argument for a component's class docstring (gh-805 §F).

    Built from the same ``[[<obj>.warnings]]`` entries `make_warnings_ctx`
    compiles into the ``PyErr_WarnEx`` block, so a documented category is by
    construction the category the binding emits.

    The description names the state field that gates the warning as well as
    quoting the message. A caller who sees the warning wants to know what
    provoked it, and the condition is a field on the object's own state — the
    one piece of the contract that is visible in the author's C and nowhere in
    the Python signature.

    Parameters
    ----------
    warnings : list of dict
        Entries with ``condition``, ``category`` and ``message``, as
        `_config.warnings` returns them.

    Examples
    --------
    >>> warns_doc([])
    []
    >>> cat, desc = warns_doc([{"condition": "underpowered",
    ...                         "category": "UserWarning",
    ...                         "message": "under-powered"}])[0]
    >>> cat
    'UserWarning'
    >>> desc
    'Emitted after construction when ``underpowered`` holds: ``under-powered``.'
    """
    out: list[tuple[str, str]] = []
    for w in warnings:
        category = w.get("category", "UserWarning")
        message = w.get("message", "")
        condition = w.get("condition", "")
        out.append(
            (
                category,
                f"Emitted after construction when ``{condition}`` holds: "
                f"``{message}``.",
            )
        )
    return out


def make_warnings_ctx(
    component: str,
    Component: str,
    warnings: list[dict],
) -> dict[str, str]:
    """Build the post-construction ``PyErr_WarnEx`` block (gh-481).

    Parameters
    ----------
    component : str
        Lowercase component id, e.g. ``acq``.
    Component : str
        Class-cased name, e.g. ``Acquisition``. Unused today; accepted so the
        signature matches the other ``make_*_ctx`` builders.
    warnings : list of dict
        Entries with ``after`` (only ``__init__`` today), ``condition``
        (a bool field on the state struct), ``category``, ``message``, and an
        optional ``stacklevel`` (default 1 — points at the caller's
        construction site, which is what a user needs to see).

    Returns
    -------
    dict
        Single key ``init_warn_block``. Empty string when no warnings are
        declared, so a non-adopting component renders byte-identically to
        before gh-481.

    Notes
    -----
    ``PyErr_WarnEx`` returns -1 when the warning was escalated to an exception
    (``-W error``), so the generated block propagates that as an ``__init__``
    failure rather than swallowing it — under ``-W error`` the construction
    genuinely did not succeed.

    Examples
    --------
    >>> ctx = make_warnings_ctx("acq", "Acquisition", [
    ...     {"condition": "underpowered", "category": "UserWarning",
    ...      "message": "under-powered"}])
    >>> print(ctx["init_warn_block"], end="")
        if (self->handle->underpowered) {
            if (PyErr_WarnEx(PyExc_UserWarning,
                             "under-powered",
                             1) < 0)
                return -1;
        }
    >>> make_warnings_ctx("acq", "Acquisition", [])["init_warn_block"]
    ''
    """
    _EMPTY = {"init_warn_block": ""}
    if not warnings:
        return _EMPTY

    parts: list[str] = []
    for w in warnings:
        cond = w["condition"]
        category = w.get("category", "UserWarning")
        stacklevel = int(w.get("stacklevel", 1) or 1)
        msg = _c_string_literal(w["message"], 25)
        parts.append(
            f"    if ({condition_expr(cond)}) {{\n"
            f"        if (PyErr_WarnEx(PyExc_{category},\n"
            f"{msg},\n"
            f"                         {stacklevel}) < 0)\n"
            f"            return -1;\n"
            f"    }}\n"
        )
    return {"init_warn_block": "".join(parts)}


def make_errors_ctx(
    component: str,
    category: str = "",
    message: str = "",
    create_fn: "str | None" = None,
    handle_expr: str = "self->handle",
    undeclared_body: str = "",
) -> dict[str, str]:
    """Build the create()-failure translation block (gh-482).

    Parameters
    ----------
    component : str
        Lowercase component id, e.g. ``acq``.
    category : str, optional
        A name from `_config.ERROR_CATEGORIES`. Empty means undeclared, which
        yields the historical ``MemoryError`` block unchanged.
    message : str, optional
        Text for the raised exception. Ignored when `category` is empty.
    handle_expr : str, optional
        The C expression tested for NULL. Objects store their state in
        ``self->handle``; a ``kind = "handle"`` module uses ``self->h``
        (gh-514).
    undeclared_body : str, optional
        Overrides the ``category``-empty body. A handle's historical failure
        text is a one-line ``RuntimeError``, not the object's two-line
        ``MemoryError``, so it passes its own here — that keeps every existing
        handle module's generated C byte-identical while still sharing the
        *declared* rendering below, which is the part gh-514 needed.

    Returns
    -------
    dict
        Single key ``create_fail_block``.

    Notes
    -----
    The undeclared form is byte-identical to the text this block replaced in
    ``templates/c/src/component_ext.c``, so a component that declares no
    create_error renders exactly as it did before gh-482. That equivalence is
    what makes the slot safe to introduce, and it is pinned by a test.

    Examples
    --------
    >>> print(make_errors_ctx("acq")["create_fail_block"], end="")
        if (!self->handle) {
            PyErr_SetString(PyExc_MemoryError,
                            "acq_create returned NULL");
            return -1;
        }
    >>> out = make_errors_ctx("acq", "ValueError", "bad params")
    >>> print(out["create_fail_block"], end="")
        if (!self->handle) {
            PyErr_SetString(PyExc_ValueError,
                            "bad params");
            return -1;
        }
    """
    if not category:
        # The pre-gh-482 hardcoded template text, to the byte. gh-509: when the
        # object overrides its constructor name (create_fn), the message names
        # the function that actually returned NULL — default None preserves the
        # historical ``<component>_create`` text byte-for-byte.
        _cfn = create_fn or f"{component}_create"
        body = undeclared_body or (
            "        PyErr_SetString(PyExc_MemoryError,\n"
            f'                        "{_cfn} returned NULL");\n'
        )
    else:
        body = (
            f"        PyErr_SetString(PyExc_{category},\n"
            f"{_c_string_literal(message, 24)});\n"
        )
    return {
        "create_fail_block": (
            f"    if (!{handle_expr}) {{\n{body}        return -1;\n    }}\n"
        )
    }
