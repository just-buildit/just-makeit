"""_diagnostics — context builders for declarative Python-level diagnostics.

Today this is `make_warnings_ctx` (gh-481): a ``PyErr_WarnEx`` guarded by a
bool field on the state struct, emitted right after a *successful*
construction.

Why it has to be generated rather than hand-written: C has no channel for
"succeeded, but with a caveat". ``create()`` returns non-NULL or it doesn't, so
a Python warning is literally unrepresentable in C and could only ever be
hand-patched into the ``_ext.c`` glue. That glue is regenerated wholesale from
the manifest, and jm's own sanctioned way to pick up a new declarative field on
an existing object is to delete the fragment and let ``jm apply`` recreate it —
which silently dropped the patch, with no diagnostic. Declaring the warning
here makes it survive that round-trip like any other generated boilerplate.

A sibling lands in gh-482: translating a ``create()`` failure into the
exception the component actually meant, instead of today's blanket
``MemoryError``. The two read as siblings — per-component table-arrays keyed on
``category`` + ``message`` plus a discriminator — but they are deliberately not
symmetric, because the underlying problems aren't. A warning is pure glue: it
fires on live state C already computed, and jm owns the whole path. An error
fires when there is no object at all, so the reason needs its own out-param
channel on ``create()`` — which changes the C API and means splicing the sacred
``_core.h``/``_core.c``. Hence the separate change.
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
