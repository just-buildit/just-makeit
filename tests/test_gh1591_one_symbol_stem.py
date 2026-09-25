"""gh-1591: one owner of the C symbol stem, `_csym`.

Every C identifier jm derives from a component, module or function name --
``<comp>_create``, ``<comp>_state_t``, the ``<COMP>_CORE_H`` guard, a
method's ``<comp>_<name>`` -- collides with another installed jm package that
has a component of the same name: at link time, and in one translation unit,
where the shared include guard silently drops the second header. Phase 2
prefixes them through ``[project] c_prefix``; phase 1 (this) makes `_csym`
the one place a derived symbol's stem comes from, so phase 2 is one change
there and not an edit at every site that spells one.

A symbol stem is not the FILE stem. ``<comp>_core.c``, the ``<comp>_core``
CMake target and the ``<comp>/`` header directory are namespaced by the
package directory (gh-1583) and stay the component's name; so do the whole
Python face (``PyInit_<comp>``, class names) and the ``static`` binding
internals that are never exported (``PyTypeObject`` statics, the `_enumc`
tables), which phase 2 does not prefix. Only a derived, EXPORTED-shape
symbol counts here.

Two gates, each reading jm's source as source -- the templates' code with
comments and string literals masked, and the Python AST -- so prose ABOUT a
symbol is never a finding:

1. **The C/H templates derive no symbol from the name.** In code, a
   ``<<component>>`` / ``<<COMPONENT>>`` / ``<<module>>`` / ``<<MODULE>>``
   slot is never followed by ``_<identifier>``: a symbol reads
   ``<<csym>>`` / ``<<CSYM>>``. This one is strict -- zero.
2. **A ratchet on the Python sites.** An f-string that formats a raw name
   (a variable named ``comp``, ``component``, ``obj``, ``cname``, ``module``
   ...) directly before a derived-symbol suffix (``_create``, ``_state_t``,
   ``_steps``, ``_CORE_H`` ...) or before ``_{...}`` (a method's
   ``<comp>_<name>``) is a hand-spelled symbol. 180 remain after phase 1
   (363 before it); phase 1b, gh-1633, moves them, and phase 2 cannot land
   until it has. The vocabulary is a heuristic, so a suffix outside it is
   not counted -- phase 2's `nm` gate is the oracle that catches those. The
   count per file is pinned in
   :data:`BASELINE` and may only shrink. A file over its pin fails with the
   new site's line; a file UNDER its pin fails too, until the pin is
   lowered, so the ratchet never holds slack a new site could hide in.

GATE: no C/H template derives a symbol from <<component>>/<<module>>, and no
      module spells more derived symbols by hand than its pinned count.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from just_makeit._docsync import _code_mask

PKG = Path(__file__).resolve().parent.parent / "src" / "just_makeit"

# ---------------------------------------------------------------------------
# 1. templates
# ---------------------------------------------------------------------------

_SLOT = re.compile(r"/\*<<(\w+)>>\*/|<<(\w+)>>")
#: A slot becomes ``JMSLOT<name>JMEND``: the end marker keeps a slot NAMED
#: ``module_fn_smoke_calls`` from reading as ``<<module>>`` then ``_fn...``.
_RAW = re.compile(r"JMSLOT(component|COMPONENT|module|MODULE)JMEND_[A-Za-z_]")


def template_derivations(text: str) -> "list[str]":
    """The code lines of a C/H template that derive a symbol from a raw name.

    Slots become placeholder identifiers first, because a wrapped slot
    ``/*<<component>>*/`` is itself a C comment and the mask would blank it;
    then comments and string literals are masked, so a file name in a
    comment (``@file <<component>>_core.h``) or a string
    (``"test_<<component>>_core"``) is not code.

    >>> template_derivations("/*<<component>>*/_state_t *o;")
    ['/*<<component>>*/_state_t *o;']
    >>> template_derivations("/*<<csym>>*/_state_t *o; /* <<component>>_x */")
    []
    """
    placed = _SLOT.sub(
        lambda m: f"JMSLOT{m.group(1) or m.group(2)}JMEND", text
    )
    masked = _code_mask(placed)
    lines = text.splitlines()
    return [
        lines[masked.count("\n", 0, m.start())] for m in _RAW.finditer(masked)
    ]


def test_no_template_derives_a_symbol_from_the_name():
    bad = []
    for path in sorted((PKG / "templates" / "c").rglob("*")):
        if path.suffix not in (".c", ".h"):
            continue
        for line in template_derivations(path.read_text(encoding="utf-8")):
            bad.append(f"{path.relative_to(PKG)}: {line.strip()}")
    assert bad == [], (
        "a C identifier in a template derives from <<csym>> / <<CSYM>> "
        "(gh-1591), not the component or module name:\n" + "\n".join(bad)
    )


# ---------------------------------------------------------------------------
# 2. Python ratchet
# ---------------------------------------------------------------------------

#: Variables that hold a raw component / module / function NAME.
NAMES = frozenset(
    {
        "comp",
        "component",
        "obj",
        "cname",
        "module",
        "mod",
        "comp_name",
        "component_name",
        "obj_name",
        "object_name",
    }
)

#: The suffixes jm derives an exported-shape symbol with. ``_core`` is not
#: here: it is the file stem and the CMake target, which stay the name's.
_SUFFIX = re.compile(
    r"_(create|destroy|reset|step|steps|step_batch|state_t|t|state_ptr|"
    r"state_adopt|CORE_H|BRIDGE_H|PROCGLOBAL_H)(?![A-Za-z0-9_])"
)
_JOIN = re.compile(r"_(get_|set_)?$")

#: The owner, and modules that are not jm's (the examples' own scripts).
_EXEMPT = {"_csym.py"}


def _raw_name(node) -> bool:
    """*node* formats a raw name: ``comp``, ``ctx["component"]``,
    ``o.component``, or any of them ``.upper()``."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "upper"
    ):
        node = node.func.value
    if isinstance(node, ast.Name):
        return node.id in NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in NAMES
    if isinstance(node, ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Index):  # Python 3.8
            key = key.value
        return isinstance(key, ast.Constant) and key.value in NAMES
    return False


def derivations(source: str) -> "list[int]":
    """Line numbers of the f-strings in *source* that spell a derived C
    symbol from a raw name.

    >>> derivations('x = f"{comp}_create"\\n')
    [1]
    >>> derivations('x = f"{csym}_create"; y = f"{comp}_core.c"\\n')
    []
    >>> derivations('x = f"{comp}_{name}"\\n')
    [1]
    """
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.JoinedStr):
            continue
        vals = node.values
        for i, v in enumerate(vals[:-1]):
            if not (isinstance(v, ast.FormattedValue) and _raw_name(v.value)):
                continue
            nxt = vals[i + 1]
            if not (
                isinstance(nxt, ast.Constant) and isinstance(nxt.value, str)
            ):
                continue
            if _SUFFIX.match(nxt.value) or (
                _JOIN.match(nxt.value)
                and i + 2 < len(vals)
                and isinstance(vals[i + 2], ast.FormattedValue)
            ):
                out.append(node.lineno)
    return out


def _counts() -> "dict[str, int]":
    out = {}
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG)
        if {"templates", "examples"} & set(rel.parts) or rel.name in _EXEMPT:
            continue
        n = len(derivations(path.read_text(encoding="utf-8")))
        if n:
            out[rel.as_posix()] = n
    return out


#: gh-1591 phase 1: the hand-spelled derived symbols left per module. May
#: only shrink -- lower a pin in the same change that moves a site.
BASELINE = {
    "_app.py": 6,
    "_apply.py": 9,
    "_bind.py": 3,
    "_borrow.py": 1,
    "_builtins.py": 2,
    "_codec.py": 2,
    "_composer.py": 8,
    "_config.py": 2,
    "_context/_destroy.py": 2,
    "_context/_diagnostics.py": 1,
    "_context/_methods.py": 28,
    "_context/_state.py": 24,
    "_ctorsig.py": 1,
    "_docgaps.py": 3,
    "_docstring.py": 3,
    "_error.py": 1,
    "_function.py": 3,
    "_init.py": 4,
    "_invariants.py": 1,
    "_method.py": 36,
    "_object.py": 4,
    "_property.py": 8,
    "_remove.py": 6,
    "_status.py": 2,
    "_stubs.py": 12,
    "_upgrade.py": 5,
    "_view.py": 3,
}


def test_hand_spelled_symbols_only_shrink():
    now = _counts()
    over = {
        f: (n, BASELINE.get(f, 0))
        for f, n in now.items()
        if n > BASELINE.get(f, 0)
    }
    under = {
        f: (now.get(f, 0), pin)
        for f, pin in BASELINE.items()
        if now.get(f, 0) < pin
    }
    assert not over, (
        "a C symbol derived from a raw name, spelled by hand (gh-1591): "
        "derive it from `_csym.stem(owner, name)` or the render context's "
        "`csym`.\n"
        + "\n".join(
            f"  {f}: {n} > pinned {pin}, lines "
            f"{derivations((PKG / f).read_text(encoding='utf-8'))}"
            for f, (n, pin) in sorted(over.items())
        )
    )
    assert not under, (
        "a ratchet pin is higher than the count; lower it in BASELINE so "
        "the slack cannot hide a new site:\n"
        + "\n".join(
            f"  {f}: {n} < pinned {pin}"
            for f, (n, pin) in sorted(under.items())
        )
    )


# ---------------------------------------------------------------------------
# 3. the slot is refused without its owner
# ---------------------------------------------------------------------------


def test_render_refuses_a_symbol_slot_its_context_lacks():
    """A template that reads ``<<csym>>`` from a context without it is
    refused, not left holding the literal slot -- which is a valid C comment
    in its wrapped form, so it would compile into a symbol named ``_state_t``
    (the same check that guards ``<<inc_prefix>>``, gh-1583)."""
    import pytest

    from just_makeit import _csym as CSYM
    from just_makeit import _render as R

    with pytest.raises(ValueError, match="C symbol stem"):
        R.render("/*<<csym>>*/_state_t *o;", {"component": "fir"})
    assert (
        R.render(
            "/*<<csym>>*/_state_t *o;",
            CSYM.slots({"project": {"name": "p"}}, "fir"),
        )
        == "fir_state_t *o;"
    )
