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
2. **No Python site spells one by hand.** An f-string that formats a raw
   name (a variable named ``comp``, ``component``, ``obj``, ``cname``,
   ``module`` ...) directly before a derived-symbol suffix (``_create``,
   ``_state_t``, ``_steps``, ``_CORE_H`` ...) or before ``_{...}`` (a
   method's ``<comp>_<name>``) is a hand-spelled symbol. 363 before phase 1,
   175 after it, **0** after phase 1b (gh-1633) -- so this is strict now:
   :data:`BASELINE` is empty and any new site fails with its line. The only
   escapes are :data:`EXEMPT_FUNCS`, each a function whose spellings are not
   derived exported symbols, with the reason. The vocabulary is a
   heuristic; `tests/test_gh1633_stem_reaches_every_symbol.py` is the oracle
   that needs none (it renders under a stem override and reads the C).

GATE: no C/H template derives a symbol from <<component>>/<<module>>, and no
      module spells a derived symbol by hand.
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

#: Functions whose ``{name}_...`` spellings are NOT derived exported symbols,
#: each with the reason (gh-1633). Keyed by function, not line, so an edit
#: elsewhere cannot move a real site under an exemption -- and a NEW site in
#: one of these functions is exempt only if it is the same kind of thing,
#: which review of that function sees.
EXEMPT_FUNCS: "dict[tuple[str, str], str]" = {
    ("_composer.py", "render_serializers"): (
        "`static PyObject *<cname>_<name>` binders: file-local, never "
        "exported, so phase 2 does not prefix them"
    ),
    ("_composer.py", "_settings_getset_c"): (
        "`static` getset binders: file-local, never exported"
    ),
    ("_composer.py", "arg_scopes"): (
        "names the static binder whose locals gh-1512 checks, not a symbol"
    ),
    ("_method.py", "_varargs_core_c"): (
        "a FILE name (`<obj>_<method>_core.c`), which stays the object's"
    ),
}


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
        src = path.read_text(encoding="utf-8")
        lines = derivations(src)
        exempt = [
            f
            for f in ast.walk(ast.parse(src))
            if isinstance(f, ast.FunctionDef)
            and (rel.as_posix(), f.name) in EXEMPT_FUNCS
        ]
        n = sum(
            1
            for ln in lines
            if not any(f.lineno <= ln <= f.end_lineno for f in exempt)
        )
        if n:
            out[rel.as_posix()] = n
    return out


def test_every_exempt_function_exists():
    """An exemption naming a function that is gone exempts nothing, and
    would quietly exempt a new function that took the name."""
    missing = []
    for (rel, name), _why in EXEMPT_FUNCS.items():
        src = (PKG / rel).read_text(encoding="utf-8")
        if not any(
            isinstance(f, ast.FunctionDef) and f.name == name
            for f in ast.walk(ast.parse(src))
        ):
            missing.append(f"{rel}:{name}")
    assert missing == [], missing


#: The hand-spelled derived symbols left per module. EMPTY since gh-1633:
#: a site that reappears fails. Kept as a table so a future carve-out is a
#: visible, reviewed pin rather than a quiet exemption.
BASELINE: "dict[str, int]" = {}


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


def create_fallbacks(source: str) -> "list[int]":
    """Lines of *source* that re-spell the create-name RULE: an ``or``
    whose fallback is an f-string ending ``_create``.

    `_csym.create_name` is that rule's one spelling; a copy picks the stem
    it happens to have and decides "declared name, else default" again,
    which is the pair that drifts (gh-1591).

    >>> create_fallbacks('x = fn or f"{comp}_create"\\n')
    [1]
    >>> create_fallbacks('x = CSYM.create_name(comp, fn)\\n')
    []
    """
    out = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        last = node.values[-1]
        if (
            isinstance(last, ast.JoinedStr)
            and last.values
            and isinstance(last.values[-1], ast.Constant)
            and str(last.values[-1].value).endswith("_create")
        ):
            out.append(node.lineno)
    return out


def test_the_create_name_rule_is_spelled_once():
    bad = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG)
        if {"templates", "examples"} & set(rel.parts) or rel.name in _EXEMPT:
            continue
        for line in create_fallbacks(path.read_text(encoding="utf-8")):
            bad.append(f"{rel.as_posix()}:{line}")
    assert bad == [], (
        "the `<declared> or <stem>_create` rule is `_csym.create_name` "
        "(gh-1591); call it with the stem you have:\n" + "\n".join(bad)
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
