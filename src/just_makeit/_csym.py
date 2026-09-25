"""The C stem jm derives a component's, module's or function's symbols from.

gh-1591, phase 1 of 3. The one owner of the question "what does a symbol jm
names *for* the author start with". Two different things share a component's
name today, and the distinction is the point of this module:

- the **file stem** -- ``native/src/<comp>/<comp>_core.c``, the
  ``<comp>_core`` CMake target, the ``<comp>/`` header directory. Files are
  namespaced by the package directory (gh-1583) and never move for this.
- the **symbol stem** (:func:`stem`) -- ``<stem>_create``,
  ``<stem>_state_t``, the ``<STEM>_CORE_H`` guard, a method's
  ``<stem>_<name>``. Every C identifier jm derives from a name, as opposed
  to one the author named (``fn =``, ``create_fn``, ``status_fn``, ...),
  which is never touched.

Today the two are equal: :func:`stem` returns the name it is given, and every
project renders byte-identically to before this module existed.

Phase 2 adds ``[project] c_prefix`` and reads it here and nowhere else, so
two installed jm packages that share a component name stop colliding at link
time and in one translation unit (the shared include guard silently drops
the second header today). Its rules, stated here so they land in this file:

- a name that already starts with ``<prefix>_`` is used as is (doppler's
  ``dp_tlm`` stays ``dp_tlm_create``, not ``dp_dp_tlm_create``);
- ``apply`` refuses a manifest whose derived symbol set has a duplicate --
  ``x`` and ``dp_x`` both deriving ``dp_x_create`` -- checked over the whole
  set, so a component, a method and a module function that happen to derive
  one name are caught by the same check.

Every function takes an *owner*, exactly as :mod:`_incpath` does: a path
inside the project, or its manifest as a dict -- never a bare package name,
because a name does not say whether the project declares a prefix.

The template slots are ``<<csym>>`` / ``<<CSYM>>``, set wherever
``<<component>>`` / ``<<module>>`` are (:func:`slots`); `render()` refuses a
template that uses one without it, through the same check that guards
``<<inc_prefix>>``. ``tests/test_gh1591_one_symbol_stem.py`` refuses a
template that derives a symbol from ``<<component>>``, and ratchets the
Python sites that still spell one by hand.
"""

from __future__ import annotations

from . import _incpath as INC

#: The template slots whose value depends on the project's symbol stem.
#: `render()` refuses a template that uses one when its context lacks it --
#: the same check, and the same loop, that guards `_incpath.PROJECT_SLOTS`.
SLOTS = ("csym", "CSYM")


def stem(owner: INC.Owner, name: str) -> str:
    """The C stem jm derives *name*'s symbols from, in *owner*'s project.

    *name* is a component, a module (its C name, ``cname``) or a module
    function. Today it is *name* itself; phase 2 of gh-1591 prefixes it when
    the project declares ``c_prefix``.

    The owner is validated even though nothing reads it yet, so every
    caller already passes the project that phase 2 will ask.

    >>> stem({"project": {"name": "p"}}, "fir")
    'fir'
    """
    INC.manifest(owner)
    return name


def upper(owner: INC.Owner, name: str) -> str:
    """:func:`stem` in upper case: an include guard's or ``#define``'s.

    >>> upper({"project": {"name": "p"}}, "fir")
    'FIR'
    """
    return stem(owner, name).upper()


def create_name(stem_: str, create_fn: "str | None" = None) -> str:
    """The constructor an object's faces call: the declared *create_fn*, else
    ``<stem>_create`` -- the one spelling of the default (gh-1328, gh-1591).

    Takes a stem already derived (:func:`stem`, or a render context's
    ``csym``) so a context-only caller and a manifest caller share one rule:
    ``C.object_create_name`` is this over the manifest.

    >>> create_name("fir"), create_name("fir", "fir_open")
    ('fir_create', 'fir_open')
    """
    return create_fn or f"{stem_}_create"


def ctx_create_name(ctx: dict) -> str:
    """:func:`create_name` for a render context: its ``create_name`` slot
    when the state context set one, else the default from its ``csym``.

    >>> ctx_create_name({"csym": "fir"})
    'fir_create'
    """
    declared = ctx.get("create_name")
    return str(declared) if declared else create_name(str(ctx["csym"]))


def slots(owner: INC.Owner, name: str) -> "dict[str, str]":
    """The template slots for a render of *name*'s files in *owner*'s project.

    >>> slots({"project": {"name": "p"}}, "fir")
    {'csym': 'fir', 'CSYM': 'FIR'}
    """
    return {"csym": stem(owner, name), "CSYM": upper(owner, name)}
