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

# A condition names a bool-ish field on the state struct, interpolated straight
# into C — so anything but a plain identifier is rejected before it can become
# an undeclared-identifier error in the user's build instead of a jm message.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

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
            f"    if (self->handle->{cond}) {{\n"
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
        # The pre-gh-482 hardcoded template text, to the byte.
        body = (
            "        PyErr_SetString(PyExc_MemoryError,\n"
            f'                        "{component}_create returned NULL");\n'
        )
    else:
        body = (
            f"        PyErr_SetString(PyExc_{category},\n"
            f"{_c_string_literal(message, 24)});\n"
        )
    return {
        "create_fail_block": (
            f"    if (!self->handle) {{\n{body}        return -1;\n    }}\n"
        )
    }
